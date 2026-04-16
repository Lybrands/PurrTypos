"""
On-demand writing sub-experts (single invocation, no multi-stage pipeline).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from services.agent_tool_definitions import to_openai_tools
from services.ai_provider import create_chat_no_stream, create_chat_stream
from services.tool_executor import run_tools as executor_run_tools
from services.tool_router import ensure_skills_loaded, get_api_skill_items
from services.writing_rules import (
    extract_structured_json_from_model_text,
    normalize_polished_result,
    normalize_review_issues,
    normalize_style_unify_result,
    normalize_writing_blueprint,
)
from utils.tool_call_utils import normalize_tool_calls_list
from utils.writing_helpers import build_prior_chapter_hint, build_style_unify_prior_chapter_hint
from utils.writing_prompt import (
    build_plan_expert_prompt,
    build_polish_expert_prompt,
    build_review_expert_prompt,
    build_style_expert_prompt,
)
from utils.streaming import append_model_content, text_from_stream_choice0

logger = logging.getLogger(__name__)

SUBAGENT_ROLE_LABELS: dict[str, str] = {
    "review": "审校专家",
    "polish": "润色专家",
    "continuation_plan": "续写规划",
    "style_unify": "风格统一",
}

TOOL_NAMES_BY_ROLE: dict[str, list[str]] = {
    "review": [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "listOutlines", "queryOutline",
        "listBookCharacters", "getBookCharacters",
    ],
    "continuation_plan": [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "listOutlines", "queryOutline",
        "listBookCharacters", "getBookCharacters",
    ],
    "polish": [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "createWritingChapter",
        "listBookCharacters", "getBookCharacters",
    ],
    "style_unify": [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents",
        "listBookCharacters", "getBookCharacters",
    ],
}

CHAPTER_CATALOG_BODY_TOOL_NAMES = frozenset({
    "getChapterContent", "editChapterContent",
    "batchGetChapterContents", "createWritingChapter",
})


def _filter_tools(all_tools: list[dict], names: set[str]) -> list[dict]:
    return [
        t for t in all_tools
        if (t.get("function") or {}).get("name") in names
    ]


def _tool_result_looks_like_json_error(content: str) -> bool:
    if not isinstance(content, str):
        return False
    s = content.strip()
    if not s.startswith("{"):
        return False
    try:
        o = json.loads(s)
        return isinstance(o, dict) and isinstance(o.get("error"), str) and len(o["error"]) > 0
    except Exception:
        return False


def _merge_stream_tool_calls(
    accumulated: list[dict], delta_tool_calls: list[dict] | None,
) -> list[dict]:
    if not delta_tool_calls:
        return accumulated
    result = list(accumulated)
    for dtc in delta_tool_calls:
        idx = dtc.get("index", 0)
        while len(result) <= idx:
            result.append({})
        cur = result[idx]
        if "id" not in cur and dtc.get("id") is not None:
            cur["id"] = dtc["id"]
        if "type" not in cur and dtc.get("type") is not None:
            cur["type"] = dtc["type"]
        fn_delta = dtc.get("function") or {}
        fn_cur = cur.setdefault("function", {})
        if fn_delta.get("name") is not None:
            fn_cur["name"] = fn_delta["name"]
        if fn_delta.get("arguments") is not None:
            fn_cur["arguments"] = fn_cur.get("arguments", "") + fn_delta["arguments"]
        result[idx] = cur
    return result


async def _run_non_stream_tool_loop(
    *,
    loop_messages: list[dict],
    stage_tools: list[dict],
    tool_ctx: dict,
    send_chunk: Callable[[dict], None],
    key: str,
    api_provider: str,
    rp: dict,
    signal: Any,
    max_rounds: int = 6,
) -> str:
    """Non-streaming chat with tool_calls loop; returns final assistant text."""
    for _guard in range(max_rounds):
        if signal and signal.is_set():
            break
        opts = dict(rp)
        if stage_tools:
            opts["tools"] = stage_tools
        resp = await create_chat_no_stream(key, loop_messages, opts, api_provider, signal)
        message = resp.get("message") or {}
        content = str(message.get("content") or "")
        final_text = content
        raw_calls = message.get("tool_calls") or []
        calls_list = normalize_tool_calls_list(raw_calls if isinstance(raw_calls, list) else [])
        if not calls_list:
            return final_text

        send_chunk({
            "toolCalls": calls_list,
            "toolCallsInProgress": True,
            "partialContent": "",
            "partialThinking": "",
            "orchestratorInfo": {"stage": "writing_subagent"},
            "model": rp.get("model", ""),
        })

        tool_results = await executor_run_tools(
            calls_list, tool_ctx, lambda ev: send_chunk(ev) if ev else None,
        )

        assistant_msg = {
            "role": "assistant",
            "content": final_text,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                }
                for tc in calls_list
            ],
        }
        tool_msgs = [{"role": "tool", "tool_call_id": r["tool_call_id"], "content": r["content"]} for r in tool_results]
        loop_messages = loop_messages + [assistant_msg] + tool_msgs

        all_chapter_body = (
            calls_list
            and all((tc.get("function") or {}).get("name") in CHAPTER_CATALOG_BODY_TOOL_NAMES for tc in calls_list)
        )
        all_failed = (
            all_chapter_body
            and len(tool_results) == len(calls_list)
            and all(_tool_result_looks_like_json_error(r.get("content", "")) for r in tool_results)
        )
        if all_failed:
            return final_text

    return final_text


async def _run_streaming_tool_loop(
    *,
    loop_messages: list[dict],
    stage_tools: list[dict],
    tool_ctx: dict,
    send_chunk: Callable[[dict], None],
    key: str,
    api_provider: str,
    rp: dict,
    signal: Any,
    used_model: str,
    max_rounds: int = 6,
) -> str:
    """Streaming assistant with tool loop; emits writingSubagentDelta for content."""
    accumulated_final = ""
    for _round in range(max_rounds):
        if signal and signal.is_set():
            break
        params = dict(rp)
        if stage_tools:
            params["tools"] = stage_tools
        result = await create_chat_stream(key, loop_messages, params, api_provider, signal)
        stream = result.get("stream")
        used_model = result.get("model", used_model)
        if not stream:
            return accumulated_final

        accumulated_content = ""
        accumulated_tool_calls: list[dict] = []
        got_tool_calls = False

        async for chunk in stream:
            if signal and signal.is_set():
                break
            choices = chunk.get("choices") or []
            if not choices:
                continue
            c0 = choices[0]
            delta = c0.get("delta") or {}

            content_delta = text_from_stream_choice0(c0)
            if content_delta:
                new_acc, emitted = append_model_content(accumulated_content, content_delta)
                accumulated_content = new_acc
                if emitted:
                    send_chunk({"writingSubagentDelta": {"delta": emitted}})
            else:
                msg_content = (c0.get("message") or {}).get("content")
                if isinstance(msg_content, str) and msg_content:
                    new_acc, emitted = append_model_content(
                        accumulated_content, "", msg_content,
                    )
                    accumulated_content = new_acc
                    if emitted:
                        send_chunk({"writingSubagentDelta": {"delta": emitted}})

            raw_tool_calls = delta.get("tool_calls")
            if raw_tool_calls and isinstance(raw_tool_calls, list):
                accumulated_tool_calls = _merge_stream_tool_calls(accumulated_tool_calls, raw_tool_calls)

            finish_reason = c0.get("finish_reason")
            if finish_reason in ("stop", "length"):
                if accumulated_tool_calls:
                    valid_stop = [
                        tc for tc in accumulated_tool_calls
                        if tc.get("function", {}).get("name")
                    ]
                    if valid_stop:
                        finish_reason = "tool_calls"
                    else:
                        accumulated_final = accumulated_content
                        return accumulated_final
                else:
                    accumulated_final = accumulated_content
                    return accumulated_final

            if finish_reason in ("tool_calls", "function_call"):
                valid_calls = [
                    tc for tc in accumulated_tool_calls
                    if tc.get("function", {}).get("name")
                ]
                if not valid_calls:
                    accumulated_final = accumulated_content
                    return accumulated_final

                send_chunk({
                    "toolCalls": valid_calls,
                    "toolCallsInProgress": True,
                    "partialContent": accumulated_content,
                    "partialThinking": "",
                    "model": used_model,
                })

                tool_results = await executor_run_tools(
                    valid_calls, tool_ctx, lambda ev: send_chunk(ev) if ev else None,
                )

                results_display = []
                for r in tool_results:
                    tc_name = next(
                        (tc.get("function", {}).get("name", "")
                         for tc in valid_calls if tc.get("id") == r.get("tool_call_id")),
                        "",
                    )
                    results_display.append({
                        "tool_call_id": r.get("tool_call_id"),
                        "name": tc_name,
                        "content": r.get("content"),
                    })
                send_chunk({"toolResults": results_display})

                asst_msg: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": valid_calls,
                }
                if accumulated_content:
                    asst_msg["content"] = accumulated_content
                loop_messages = loop_messages + [asst_msg]
                for r in tool_results:
                    loop_messages.append({
                        "role": "tool",
                        "tool_call_id": r.get("tool_call_id"),
                        "content": r.get("content", ""),
                    })
                got_tool_calls = True
                break

        if not got_tool_calls:
            accumulated_final = accumulated_content
            return accumulated_final

    return accumulated_final


def _build_messages(
    *,
    role: str,
    base_system: str,
    expert_system: str,
    messages: list[dict],
    user_task: str,
) -> list[dict]:
    system_content = "\n\n".join(filter(None, [base_system, expert_system]))
    out: list[dict] = [{"role": "system", "content": system_content}]
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") != "system":
            out.append(dict(m))
    out.append({"role": "user", "content": user_task})
    return out


async def run_writing_subagent(
    *,
    role: str,
    send_chunk: Callable[[dict], None],
    signal: Any,
    key: str,
    api_provider: str,
    request_params: dict,
    tool_ctx: dict,
    messages: list[dict],
    model: str,
) -> None:
    """Run one sub-expert invocation; emits writingSubagent* SSE events."""
    role = (role or "").strip().lower()
    if role not in SUBAGENT_ROLE_LABELS:
        send_chunk({"error": f"未知 subagentRole: {role}"})
        return

    label = SUBAGENT_ROLE_LABELS[role]
    send_chunk({"writingSubagentStart": {"role": role, "label": label}})

    latest_user = next((m for m in reversed(messages or []) if m and m.get("role") == "user"), None)
    user_text = str((latest_user or {}).get("content") or "")

    base_system = next(
        (str(m.get("content") or "") for m in (messages or []) if m and m.get("role") == "system"),
        "",
    ).strip()

    prior = build_prior_chapter_hint(tool_ctx)
    style_hint = build_style_unify_prior_chapter_hint(tool_ctx)

    if role == "review":
        expert = build_review_expert_prompt(tool_ctx, prior)
        user_task = f"{prior}\n\n【用户任务】\n{user_text}" if prior else user_text
    elif role == "continuation_plan":
        expert = build_plan_expert_prompt(tool_ctx)
        user_task = user_text
    elif role == "polish":
        expert = build_polish_expert_prompt(tool_ctx, prior)
        user_task = f"{prior}\n\n【用户任务】\n{user_text}" if prior else user_text
    else:  # style_unify
        expert = build_style_expert_prompt(tool_ctx, style_hint)
        user_task = f"{style_hint}\n\n【待统一初稿与用户说明】\n{user_text}"

    loop_messages = _build_messages(
        role=role,
        base_system=base_system,
        expert_system=expert,
        messages=messages,
        user_task=user_task,
    )

    ensure_skills_loaded()
    all_api = to_openai_tools(get_api_skill_items())
    allowed = set(TOOL_NAMES_BY_ROLE.get(role, []))
    stage_tools = _filter_tools(all_api, allowed)

    rp = {**request_params, "model": model, "thinking": {"type": "disabled"}}
    prev_allow = tool_ctx.get("subagentAllowedToolNames")
    tool_ctx["subagentAllowedToolNames"] = allowed

    try:
        if role in ("review", "continuation_plan"):
            raw = await _run_non_stream_tool_loop(
                loop_messages=list(loop_messages),
                stage_tools=stage_tools,
                tool_ctx=tool_ctx,
                send_chunk=send_chunk,
                key=key,
                api_provider=api_provider,
                rp=rp,
                signal=signal,
            )
            parsed = extract_structured_json_from_model_text(raw)
            if role == "review":
                payload = {"issues": normalize_review_issues(parsed)}
            else:
                payload = {"blueprint": normalize_writing_blueprint(parsed or {})}
            send_chunk({"writingSubagentResult": {"role": role, "payload": payload}})
        else:
            raw = await _run_streaming_tool_loop(
                loop_messages=list(loop_messages),
                stage_tools=stage_tools,
                tool_ctx=tool_ctx,
                send_chunk=send_chunk,
                key=key,
                api_provider=api_provider,
                rp=rp,
                signal=signal,
                used_model=model,
            )
            parsed = extract_structured_json_from_model_text(raw)
            if role == "polish":
                norm = normalize_polished_result(parsed or {})
                payload = {"finalText": norm["finalText"], "changeSummary": norm["changeSummary"]}
            else:
                norm = normalize_style_unify_result(parsed or {})
                payload = {
                    "styleAnchors": norm["styleAnchors"],
                    "content": norm["content"],
                    "changeSummary": norm["changeSummary"],
                    "priorChaptersRead": norm.get("priorChaptersRead", []),
                }
            send_chunk({"writingSubagentResult": {"role": role, "payload": payload}})
    except Exception as e:
        logger.exception("[writing_subagents] role=%s", role)
        send_chunk({"error": str(e)})
    finally:
        if prev_allow is None:
            tool_ctx.pop("subagentAllowedToolNames", None)
        else:
            tool_ctx["subagentAllowedToolNames"] = prev_allow
        send_chunk({"writingSubagentDone": {"role": role}})

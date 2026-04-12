"""
Subagent multi-stage pipeline — port of electron/subagentPipeline.js.

Orchestrates the writing-expert stages (analyze → plan → draft →
style_unify → review → polish) and streams the final presenter
reply to the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

from services.agent_tool_definitions import to_openai_tools
from services.ai_provider import create_chat_no_stream
from services.subagent_config import (
    STAGES,
    SUBAGENT_REGISTRY,
    extract_structured_json_from_model_text,
    validate_subagent_config,
)
from services.subagent_shared import normalize_pipeline_flags
from services.tool_executor import run_tools as executor_run_tools
from services.tool_router import ensure_skills_loaded, get_api_skill_items
from utils.tool_call_utils import normalize_tool_calls_list
from utils.writing_chapters import get_writable_chapters_for_agent

logger = logging.getLogger(__name__)

try:
    validate_subagent_config()
except Exception as exc:
    logger.error("[subagent-config] invalid: %s", exc)


# ---------------------------------------------------------------------------
# Stage tool permissions (pinned tools per stage)
# ---------------------------------------------------------------------------

STAGE_PINNED_TOOLS: dict[str, list[str]] = {
    STAGES.ANALYZE: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "listOutlines", "queryOutline",
    ],
    STAGES.PLAN: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "listOutlines", "queryOutline",
    ],
    STAGES.DRAFT: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "createWritingChapter",
    ],
    STAGES.STYLE_UNIFY: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents",
    ],
    STAGES.REVIEW: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents",
    ],
    STAGES.POLISH: [
        "getStoryBackground", "getGlobalOutline", "listWritingChapters",
        "getChapterContent", "batchGetChapterContents", "createWritingChapter",
    ],
}

CHAPTER_CATALOG_BODY_TOOL_NAMES = frozenset([
    "getChapterContent", "editChapterContent",
    "batchGetChapterContents", "createWritingChapter",
])


def build_tool_permissions_for_stage(stage: str) -> list[str]:
    return list(STAGE_PINNED_TOOLS.get(stage, []))


# ---------------------------------------------------------------------------
# Tool schema helpers
# ---------------------------------------------------------------------------

def filter_tool_schemas_by_names(
    tool_schemas: list[dict], allowed_names: set[str],
) -> list[dict]:
    if not tool_schemas or not allowed_names:
        return []
    return [
        t for t in tool_schemas
        if (t.get("function") or {}).get("name") in allowed_names
    ]


def _ensure_stage_candidate_tools(
    candidate_tools: list[dict], allowed_set: set[str],
) -> list[dict]:
    ensure_skills_loaded()
    all_api = to_openai_tools(get_api_skill_items())
    all_by_name = {(t.get("function") or {}).get("name"): t for t in all_api}
    present: set[str] = set()
    out: list[dict] = []
    for t in (candidate_tools or []):
        n = (t.get("function") or {}).get("name")
        if n and n in allowed_set and n not in present:
            out.append(t)
            present.add(n)
    for nm in allowed_set:
        if nm not in present and nm in all_by_name:
            out.append(all_by_name[nm])
            present.add(nm)
    return out


def _ensure_pinned_tools_for_stage(
    stage: str, tool_schemas: list[dict], allowed_set: set[str],
) -> list[dict]:
    if not stage or not tool_schemas:
        return tool_schemas or []
    pinned = [n for n in (STAGE_PINNED_TOOLS.get(stage) or []) if n in allowed_set]
    if not pinned:
        return tool_schemas
    ensure_skills_loaded()
    all_api = to_openai_tools(get_api_skill_items())
    by_name = {(t.get("function") or {}).get("name"): t for t in all_api}
    present = {(t.get("function") or {}).get("name") for t in tool_schemas}
    out = list(tool_schemas)
    for nm in pinned:
        if nm not in present and nm in by_name:
            out.append(by_name[nm])
            present.add(nm)
    return out


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


# ---------------------------------------------------------------------------
# Context appendix
# ---------------------------------------------------------------------------

def _build_subagent_tooling_context_appendix(tool_ctx: dict) -> str:
    if not tool_ctx or tool_ctx.get("bookId") is None:
        return ""
    wc_raw = tool_ctx.get("writingChapters") or []
    wc = get_writable_chapters_for_agent(wc_raw)
    ao = tool_ctx.get("availableOutlines") or []
    acc_ch = tool_ctx.get("associatedChapterIds") or []
    acc_ol = tool_ctx.get("associatedOutlineIds") or []

    lines: list[str] = []
    lines.append("【会话同步 — 工具与界面上下文】")
    lines.append(
        "宿主已为当前会话绑定书籍与章节；**请勿在工具参数中手写 bookId**（一律由工具层使用当前会话书籍）。"
        "章节工具仅允许 chapterId；大纲查询仅允许 outlineId/outlineIds；禁止使用序号或标题作为定位参数。"
    )
    lines.append("若需操作非当前章节，必须先 listWritingChapters 读取真实 chapterId，再传 chapterId。")

    ch_desc = (
        f"《{re.sub(chr(10), ' ', str(tool_ctx.get('currentChapterTitle') or ''))}》"
        if tool_ctx.get("chapterId") else "（未选章节）"
    )
    lines.append(f"当前写作章节：{ch_desc}")

    if acc_ch and wc:
        bits = []
        for cid in acc_ch:
            c = next((x for x in wc if str(x.get("id")) == str(cid)), None)
            title = re.sub(r"\r?\n", " ", str(c.get("title") or "")).strip() if c else ""
            bits.append(f"《{title}》" if title else "（未匹配章节）")
        lines.append(f"用户在本轮关联的写作章节：{'、'.join(bits)}")
    if acc_ol and ao:
        bits = []
        for oid in acc_ol:
            o = next((x for x in ao if str(x.get("id")) == str(oid)), None)
            title = re.sub(r"\r?\n", " ", str(o.get("title") or "")).strip() if o else ""
            bits.append(f"《{title}》" if title else "（未匹配大纲）")
        lines.append(f"用户在本轮关联的大纲：{'、'.join(bits)}")

    WC_CAP = 100
    if wc:
        lines.append(f"写作章节目录（仅含可写正文的章节，每条给出 chapterId）。共 {len(wc)} 条：")
        for idx, c in enumerate(wc[:WC_CAP]):
            lines.append(f"- chapterId={str(c.get('id', '')).strip()} {str(c.get('title', '')).replace(chr(10), ' ')[:120]}")
        if len(wc) > WC_CAP:
            lines.append(f"… 余 {len(wc) - WC_CAP} 条略")

    AO_CAP = 60
    if ao:
        lines.append(f"大纲列表（每项含 outlineId；queryOutline 仅传 outlineId/outlineIds，勿传纯数字序号）。共 {len(ao)} 条：")
        for idx, o in enumerate(ao[:AO_CAP]):
            typ = str(o.get("type", "")) if o.get("type") is not None else ""
            oid = str(o.get("id", "")).strip()
            t = re.sub(r"\r?\n", " ", str(o.get("title") or ""))[:80]
            lines.append(f"- [{idx + 1}] outlineId={oid} {t}" + (f"\t{typ}" if typ else ""))
        if len(ao) > AO_CAP:
            lines.append(f"… 余 {len(ao) - AO_CAP} 条略")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_subagent_pipeline(
    *,
    send_chunk: Callable,
    signal: asyncio.Event | None,
    key: str,
    api_provider: str,
    request_params: dict,
    tool_ctx: dict,
    messages: list[dict],
    skill_specs: dict,
    agent_actions: list[str] | None = None,
    model: str,
) -> None:
    """Run the multi-stage writing-expert pipeline."""
    rp = {**request_params, "thinking": {"type": "disabled"}}
    tooling_appendix = _build_subagent_tooling_context_appendix(tool_ctx)

    async def get_tools_for_stage(stage: str) -> list[dict]:
        allowed_set = set(STAGE_PINNED_TOOLS.get(stage) or [])
        all_tools = to_openai_tools(get_api_skill_items())
        candidates = filter_tool_schemas_by_names(all_tools, allowed_set)
        candidates = _ensure_stage_candidate_tools(candidates, allowed_set)
        filtered = filter_tool_schemas_by_names(candidates, allowed_set)
        return _ensure_pinned_tools_for_stage(stage, filtered, allowed_set)

    async def chat_no_stream_unified(msgs: list[dict], stage_tools: list[dict]) -> dict:
        params = dict(rp)
        if stage_tools:
            params["tools"] = stage_tools
        return await create_chat_no_stream(key, msgs, params, api_provider, signal)

    def mark_edit_saved(used_calls: list[dict], tool_results: list[dict]) -> bool:
        for tc in (used_calls or []):
            if (tc.get("function") or {}).get("name") != "editChapterContent":
                continue
            tr = next((r for r in (tool_results or []) if r.get("tool_call_id") == tc.get("id")), None)
            if not tr or not tr.get("content"):
                continue
            try:
                o = json.loads(tr["content"])
                if o and o.get("success") is True:
                    return True
            except Exception:
                pass
        return False

    async def run_non_stream_tool_loop(
        current_messages: list[dict], stage_tools: list[dict], stage_name: str,
    ) -> dict:
        allowed_names = set(STAGE_PINNED_TOOLS.get(stage_name) or [])
        prev_allowed = tool_ctx.get("subagentAllowedToolNames")
        tool_ctx["subagentAllowedToolNames"] = allowed_names

        loop_messages = list(current_messages)
        final_text = ""
        edit_chapter_saved = False

        for _guard in range(6):
            if signal and signal.is_set():
                break
            resp = await chat_no_stream_unified(loop_messages, stage_tools)
            message = resp.get("message") or {}
            content = str(message.get("content") or "")
            if content:
                final_text = content
            raw_calls = message.get("tool_calls") or []
            calls_list = normalize_tool_calls_list(raw_calls if isinstance(raw_calls, list) else [])
            if not calls_list:
                _restore_allowed(tool_ctx, prev_allowed)
                return {"text": final_text, "thinking": "", "editChapterSaved": edit_chapter_saved}

            executable_calls = calls_list
            send_chunk({
                "toolCalls": executable_calls,
                "toolCallsInProgress": True,
                "partialContent": "",
                "partialThinking": "",
                "orchestratorInfo": {"stage": stage_name},
                "model": model,
            })

            tool_results = await executor_run_tools(executable_calls, tool_ctx, lambda ev: _forward_tool_events(send_chunk, ev))

            if mark_edit_saved(executable_calls, tool_results):
                edit_chapter_saved = True

            assistant_msg = {
                "role": "assistant",
                "content": final_text,
                "tool_calls": [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}
                    for tc in executable_calls
                ],
            }
            tool_msgs = [{"role": "tool", "tool_call_id": r["tool_call_id"], "content": r["content"]} for r in tool_results]
            loop_messages = loop_messages + [assistant_msg] + tool_msgs

            all_chapter_body = (
                executable_calls
                and all((tc.get("function") or {}).get("name") in CHAPTER_CATALOG_BODY_TOOL_NAMES for tc in executable_calls)
            )
            all_failed = (
                all_chapter_body
                and len(tool_results) == len(executable_calls)
                and all(_tool_result_looks_like_json_error(r.get("content", "")) for r in tool_results)
            )
            if all_failed:
                _restore_allowed(tool_ctx, prev_allowed)
                return {"text": final_text, "thinking": "", "editChapterSaved": edit_chapter_saved}

        _restore_allowed(tool_ctx, prev_allowed)
        return {"text": final_text, "thinking": "", "editChapterSaved": edit_chapter_saved}

    async def run_stage(stage: str, user_content: str) -> dict:
        cfg = SUBAGENT_REGISTRY[stage]
        base_system = next((str(m.get("content") or "") for m in messages if m and m.get("role") == "system"), "").strip()
        stage_system = "\n\n".join(filter(None, [base_system, tooling_appendix, cfg["systemPrompt"]]))
        stage_msgs = [
            {"role": "system", "content": stage_system},
            *[m for m in messages if m and m.get("role") != "system"],
            {"role": "user", "content": user_content},
        ]
        send_chunk({"subagentStage": stage, "subagentStageName": cfg["name"], "subagentStageStarting": True})
        stage_tools = await get_tools_for_stage(stage)
        stage_res = await run_non_stream_tool_loop(stage_msgs, stage_tools, stage)
        send_chunk({"subagentStageDone": stage, "subagentStageName": cfg["name"]})
        parsed = extract_structured_json_from_model_text(stage_res["text"])
        return {"rawText": stage_res["text"], "parsed": parsed, "editChapterSaved": bool(stage_res.get("editChapterSaved"))}

    # -----------------------------------------------------------------------
    # Pipeline execution（LangGraph 编排，节点内仍为原 run_stage / SSE）
    # -----------------------------------------------------------------------
    latest_user = next((m for m in reversed(messages) if m and m.get("role") == "user"), None)
    user_text = str((latest_user or {}).get("content") or "")

    flags = normalize_pipeline_flags(agent_actions)
    pipeline_label = flags["pipelineActionLabel"]

    from services.subagent_graph import run_writing_expert_langgraph

    await run_writing_expert_langgraph(
        user_text=user_text,
        flags=flags,
        pipeline_label=pipeline_label,
        run_stage=run_stage,
        send_chunk=send_chunk,
        signal=signal,
        key=key,
        api_provider=api_provider,
        rp=rp,
        messages=messages,
        tool_ctx=tool_ctx,
        model=model,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _restore_allowed(tool_ctx: dict, prev: Any) -> None:
    if prev is None:
        tool_ctx.pop("subagentAllowedToolNames", None)
    else:
        tool_ctx["subagentAllowedToolNames"] = prev


def _forward_tool_events(send_chunk: Callable, ev: dict | None) -> None:
    if not ev:
        return
    if ev.get("chapterCreated") is not None:
        send_chunk({"chapterCreated": ev["chapterCreated"]})
    if ev.get("chapterContentUpdated") is not None:
        send_chunk({"chapterContentUpdated": ev["chapterContentUpdated"]})
    if isinstance(ev.get("toolReadCacheMask"), list):
        send_chunk({"toolReadCacheMask": ev["toolReadCacheMask"]})
    if isinstance(ev.get("toolIndexCompleted"), int):
        chunk: dict = {"toolIndexCompleted": ev["toolIndexCompleted"]}
        if ev.get("toolFromCache") is True:
            chunk["toolFromCache"] = True
        send_chunk(chunk)

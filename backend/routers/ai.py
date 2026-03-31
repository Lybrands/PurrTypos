"""AI routes — model listing, title generation, and SSE chat streaming."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from schemas.ai import ChatStreamRequest, GenerateTitleRequest, ListModelsRequest
from utils.session_title import normalize_session_title
from utils.streaming import text_from_chat_delta, append_model_content
from utils.url import normalize_base_url

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)


# ── POST /ai/models ─────────────────────────────────────────────

@router.post("/ai/models")
async def list_models(body: ListModelsRequest):
    key = (body.apiKey or "").strip()
    if not key:
        return {"success": False, "error": "API Key 为空", "data": []}

    base_url = normalize_base_url(body.baseURL)

    if body.apiProvider == "anthropic":
        try:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=key, base_url=base_url or None)
            ids: list[str] = []
            async for m in client.models.list():
                if m and getattr(m, "id", None):
                    ids.append(m.id)
            return {"success": True, "data": ids}
        except Exception as e:
            return {"success": False, "error": str(e), "data": []}

    if not base_url:
        return {"success": False, "error": "请填写接口地址", "data": []}

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key, base_url=base_url)
        result = await client.models.list()
        ids = [m.id for m in result.data] if result.data else []
        return {"success": True, "data": ids}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}


# ── POST /ai/title ──────────────────────────────────────────────

def _fallback_session_title_from_prompt(prompt: str | None) -> str:
    """Extract first non-empty line as fallback title when model returns empty."""
    text = str(prompt or "").strip()
    if not text:
        return ""
    parts = text.split("\n")
    user_part = ""
    for p in parts:
        stripped = p.strip()
        if stripped:
            user_part = stripped
            break
    if not user_part:
        user_part = text
    line = user_part.replace("\r", "").split("\n")
    first = next((x.strip() for x in line if x.strip()), user_part)
    t = " ".join(first.split())
    return normalize_session_title(t)


@router.post("/ai/title")
async def generate_title(body: GenerateTitleRequest):
    key = (body.apiKey or "").strip()
    if not key:
        return {"success": False, "error": "API Key 为空"}

    model = (body.model or "").strip()
    if not model:
        return {"success": False, "error": "缺少模型参数"}

    base_url = normalize_base_url(body.baseURL)

    try:
        if body.apiProvider == "anthropic":
            from services.anthropic_chat import generate_title as anth_title

            title = await anth_title(key, body.prompt, {"model": model, "baseURL": base_url})
            title = (title or "").strip()
            if not title:
                title = _fallback_session_title_from_prompt(body.prompt)
                if title:
                    logger.info("[ai-generate-title] anthropic used prompt fallback")
                else:
                    logger.warning("[ai-generate-title] anthropic empty title model=%s", model)
            if not title:
                return {"success": False, "error": "标题生成结果为空"}
            logger.info("[ai-generate-title] 生成标题: %s", title)
            return {"success": True, "data": title}

        from services.openai_chat import generate_title as openai_title

        title = await openai_title(key, body.prompt, {"model": model, "baseURL": base_url})
        title = (title or "").strip()
        if not title:
            title = _fallback_session_title_from_prompt(body.prompt)
            if title:
                logger.info("[ai-generate-title] openai used prompt fallback")
        if not title:
            return {"success": False, "error": "标题生成结果为空"}
        logger.info("[ai-generate-title] 生成标题: %s", title)
        return {"success": True, "data": title}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── POST /ai/chat/stream (SSE) ──────────────────────────────────

def _merge_stream_tool_calls(
    accumulated: list[dict], delta_tool_calls: list[dict] | None
) -> list[dict]:
    """Merge incremental tool_calls deltas (by index) into a running list."""
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


@router.post("/ai/chat/stream")
async def chat_stream(body: ChatStreamRequest, request: Request):
    key = (body.apiKey or "").strip()
    if not key:
        async def _err_key():
            yield json.dumps({"error": "API Key 为空，请先在设置中添加模型并填写 API Key"})
        return EventSourceResponse(_err_key(), media_type="text/event-stream")

    opts = body.options or {}
    model = (opts.get("model") or "").strip()
    if not model:
        async def _err_model():
            yield json.dumps({"error": "无效的模型参数"})
        return EventSourceResponse(_err_model(), media_type="text/event-stream")

    temperature = opts.get("temperature")
    rest = {k: v for k, v in opts.items() if k not in ("model", "temperature")}

    base_url = normalize_base_url(body.baseURL)
    request_params: dict[str, Any] = {
        "model": model,
        **rest,
        "baseURL": base_url,
    }
    if temperature is not None:
        request_params["temperature"] = temperature
    if body.tools:
        request_params["tools"] = body.tools

    async def _event_generator():
        import asyncio

        abort = asyncio.Event()

        async def _check_disconnect():
            while not abort.is_set():
                if await request.is_disconnected():
                    abort.set()
                    break
                await asyncio.sleep(0.5)

        disconnect_task = asyncio.create_task(_check_disconnect())

        try:
            from services.ai_provider import create_chat_stream

            _am = (body.agentMode or "").strip().lower()
            _cam = (body.chatAgentMode or "").strip().lower()
            is_expert_team = _am == "expert_team" or _cam == "expert_team"
            is_writing_expert = bool(
                body.bookId
                and (
                    _am in ("subagent", "expert_team")
                    or _cam in ("expert", "subagent", "expert_team")
                )
            )

            # ── 专家团：AutoGen 多智能体多轮对话 / 写作专家：LangGraph 子管线 ─────
            if is_writing_expert:
                tool_ctx: dict[str, Any] = {
                    "bookId": body.bookId,
                    "chapterId": body.chapterId,
                    "currentChapterTitle": body.currentChapterTitle,
                    "writingChapters": list(body.writingChapters or []),
                    "availableOutlines": list(body.availableOutlines or []),
                    "associatedChapterIds": list(body.associatedChapterIds or []),
                    "associatedOutlineIds": list(body.associatedOutlineIds or []),
                }
                sub_rp: dict[str, Any] = {
                    "model": model,
                    **rest,
                    "baseURL": base_url,
                }
                if temperature is not None:
                    sub_rp["temperature"] = temperature

                progress_queue: asyncio.Queue = asyncio.Queue()

                def _send_writing_progress(ev: dict[str, Any]) -> None:
                    try:
                        progress_queue.put_nowait(ev)
                    except Exception:
                        pass

                async def _writing_runner() -> None:
                    try:
                        if is_expert_team:
                            from services.expert_team_autogen import run_expert_team_autogen

                            await run_expert_team_autogen(
                                send_chunk=_send_writing_progress,
                                signal=abort,
                                tool_ctx=tool_ctx,
                                messages=list(body.messages or []),
                                model=model,
                                api_provider=body.apiProvider,
                                key=key,
                                request_params=sub_rp,
                            )
                        else:
                            from services.subagent_pipeline import run_subagent_pipeline

                            tool_ctx["pipeline_variant"] = "writing_expert"
                            await run_subagent_pipeline(
                                send_chunk=_send_writing_progress,
                                signal=abort,
                                key=key,
                                api_provider=body.apiProvider,
                                request_params=sub_rp,
                                tool_ctx=tool_ctx,
                                messages=list(body.messages or []),
                                skill_specs={},
                                agent_actions=list(body.agentActions)
                                if body.agentActions
                                else None,
                                model=model,
                            )
                    except Exception as e:
                        logger.exception("[ai/chat/stream] writing expert / expert team")
                        await progress_queue.put({"error": str(e)})
                    finally:
                        await progress_queue.put(None)

                sub_task = asyncio.create_task(_writing_runner())
                try:
                    while True:
                        item = await progress_queue.get()
                        if item is None:
                            break
                        yield json.dumps(item)
                finally:
                    if not sub_task.done():
                        sub_task.cancel()
                        try:
                            await sub_task
                        except asyncio.CancelledError:
                            pass

                return

            # ── Agent mode: load tools from skill definitions ─────────────
            agent_tools: list[dict] = []
            if body.useToolRouter and body.bookId and not request_params.get("tools"):
                try:
                    from services.tool_router import get_api_skill_items
                    from services.agent_tool_definitions import to_openai_tools
                    skill_items = get_api_skill_items()
                    if skill_items:
                        agent_tools = to_openai_tools(skill_items)
                        request_params["tools"] = agent_tools
                        logger.info("[agent] 加载工具 %d 个", len(agent_tools))
                except Exception:
                    logger.warning("[agent] 工具加载失败", exc_info=True)

            is_collab = (
                (body.chatAgentMode or "").strip().lower() == "collab"
                or (body.writingMode or "").strip().lower() == "collab"
            )
            if is_collab and agent_tools:
                from utils.collab_prompt import filter_collab_tools

                last_user = ""
                for m in reversed(body.messages or []):
                    if isinstance(m, dict) and m.get("role") == "user":
                        last_user = str(m.get("content") or "")
                        break
                agent_tools = filter_collab_tools(list(agent_tools), last_user)
                request_params["tools"] = agent_tools
                logger.info("[agent][collab] 工具过滤后 %d 个", len(agent_tools))

            tool_exec_ctx = {
                "bookId": body.bookId,
                "chapterId": body.chapterId,
                "currentChapterTitle": body.currentChapterTitle,
                "writingChapters": list(body.writingChapters or []),
                "collabWriting": is_collab,
            }

            messages: list[dict] = list(body.messages or [])
            if is_collab:
                from utils.collab_prompt import (
                    build_collab_system_prompt,
                    build_collab_turn_appendix,
                )

                collab_inject = "\n\n".join(
                    p for p in (
                        build_collab_system_prompt(),
                        build_collab_turn_appendix(messages),
                    ) if p
                )
                if collab_inject:
                    _seen_system = False
                    for m in messages:
                        if isinstance(m, dict) and m.get("role") == "system":
                            cur = str(m.get("content") or "")
                            m["content"] = f"{collab_inject}\n\n{cur}" if cur else collab_inject
                            _seen_system = True
                            break
                    if not _seen_system:
                        messages.insert(0, {"role": "system", "content": collab_inject})
            used_model = model
            _MAX_ROUNDS = 6

            for _round in range(_MAX_ROUNDS):
                if abort.is_set():
                    break

                result = await create_chat_stream(
                    key, messages, request_params, body.apiProvider, signal=abort,
                )
                stream = result["stream"]
                used_model = result.get("model", model)

                accumulated_content = ""
                accumulated_thinking = ""
                accumulated_tool_calls: list[dict] = []
                got_tool_calls = False

                async for chunk in stream:
                    if abort.is_set():
                        break

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    c0 = choices[0]
                    delta = c0.get("delta") or {}

                    content_delta = delta.get("content") or ""
                    thinking_delta = delta.get("reasoning_content") or ""

                    if thinking_delta:
                        accumulated_thinking += thinking_delta
                        yield json.dumps({"thinkingDelta": thinking_delta})

                    if content_delta:
                        new_acc, emitted = append_model_content(
                            accumulated_content, content_delta,
                        )
                        accumulated_content = new_acc
                        if emitted:
                            yield json.dumps({"delta": emitted})
                    elif not content_delta:
                        msg_content = (c0.get("message") or {}).get("content")
                        if isinstance(msg_content, str) and msg_content:
                            new_acc, emitted = append_model_content(
                                accumulated_content, "", msg_content,
                            )
                            accumulated_content = new_acc
                            if emitted:
                                yield json.dumps({"delta": emitted})

                    raw_tool_calls = delta.get("tool_calls")
                    if raw_tool_calls and isinstance(raw_tool_calls, list):
                        accumulated_tool_calls = _merge_stream_tool_calls(
                            accumulated_tool_calls, raw_tool_calls,
                        )

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
                                yield json.dumps({"done": True, "model": used_model})
                                return
                        else:
                            yield json.dumps({"done": True, "model": used_model})
                            return

                    if finish_reason in ("tool_calls", "function_call"):
                        valid_calls = [
                            tc for tc in accumulated_tool_calls
                            if tc.get("function", {}).get("name")
                        ]
                        if not valid_calls:
                            yield json.dumps({"done": True, "model": used_model})
                            return

                        # Emit tool calls for frontend display
                        yield json.dumps({
                            "toolCalls": valid_calls,
                            "toolCallsInProgress": True,
                            "partialContent": accumulated_content,
                            "partialThinking": accumulated_thinking,
                            "model": used_model,
                        })

                        if not agent_tools:
                            yield json.dumps({"done": True, "model": used_model})
                            return

                        # ── Agent loop: execute tools then continue ───────
                        from services.tool_executor import run_tools
                        _progress_events: list[dict] = []
                        try:
                            tool_results = await run_tools(
                                valid_calls, tool_exec_ctx,
                                send_chunk=_progress_events.append,
                            )
                        except Exception:
                            raise
                        # Emit per-tool completion events for frontend progress bar
                        for evt in _progress_events:
                            yield json.dumps(evt)

                        # Emit results summary
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
                        yield json.dumps({"toolResults": results_display})

                        # Append assistant message + tool results to history
                        asst_msg: dict[str, Any] = {
                            "role": "assistant",
                            "tool_calls": valid_calls,
                        }
                        if accumulated_content:
                            asst_msg["content"] = accumulated_content
                        if accumulated_thinking:
                            asst_msg["reasoning_content"] = accumulated_thinking
                        messages = messages + [asst_msg]
                        for r in tool_results:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": r.get("tool_call_id"),
                                "content": r.get("content", ""),
                            })

                        got_tool_calls = True
                        break  # restart with updated messages

                if not got_tool_calls:
                    if not abort.is_set():
                        yield json.dumps({"done": True, "model": used_model})
                    return

            # Max rounds reached
            if not abort.is_set():
                yield json.dumps({"done": True, "model": used_model})

        except Exception as e:
            logger.exception("[ai/chat/stream] error")
            yield json.dumps({"error": str(e)})
        finally:
            abort.set()
            disconnect_task.cancel()

    return EventSourceResponse(_event_generator(), media_type="text/event-stream")

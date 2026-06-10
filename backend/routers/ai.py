"""AI routes — model listing, title generation, and SSE chat streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from schemas.ai import ChatStreamRequest, GenerateTitleRequest, ListModelsRequest
from utils.chat_stream import (
    StreamAccumulator,
    build_chat_request_params,
    build_tool_results_display,
    build_tool_round_messages,
    inject_system_prompt,
    last_user_message_text,
    resolve_chat_modes,
    valid_named_tool_calls,
)
from utils.session_title import normalize_session_title
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

async def _stream_writing_subagent(
    *,
    abort: asyncio.Event,
    key: str,
    api_provider: str | None,
    sub_rp: dict[str, Any],
    tool_ctx: dict[str, Any],
    messages: list[dict],
    model: str,
    subagent_role: str,
):
    """运行单次写作子专家，把其进度事件作为 SSE json 串逐条 yield 出来。

    子专家在后台 task 里跑，进度经 queue 回传；结束（或异常）后补一个终止哨兵。
    断开/取消时负责清理后台 task，正常收尾再补一个 ``done``。
    """
    progress_queue: asyncio.Queue = asyncio.Queue()

    def _send_sub(ev: dict[str, Any]) -> None:
        try:
            progress_queue.put_nowait(ev)
        except Exception:
            pass

    async def _sub_runner() -> None:
        try:
            from services.writing_subagents import run_writing_subagent

            await run_writing_subagent(
                role=subagent_role,
                send_chunk=_send_sub,
                signal=abort,
                key=key,
                api_provider=api_provider,
                request_params=sub_rp,
                tool_ctx=dict(tool_ctx),
                messages=list(messages),
                model=model,
            )
        except Exception as e:
            logger.exception("[ai/chat/stream] writing subagent")
            await progress_queue.put({"error": str(e)})
        finally:
            await progress_queue.put(None)

    sub_task = asyncio.create_task(_sub_runner())
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

    if not abort.is_set():
        yield json.dumps({"done": True, "model": model})


async def _run_agent_tool_round(
    valid_calls: list[dict],
    tool_exec_ctx: dict[str, Any],
    acc_content: str,
    acc_thinking: str,
) -> tuple[list[dict], list[dict]]:
    """执行一轮工具调用。

    返回 ``(events, appended_messages)``：``events`` 是要原样 yield 给前端的事件
    （逐工具进度 + 结果汇总），``appended_messages`` 是要追加进对话历史的
    ``[assistant, *tool]`` 序列。``run_tools`` 的进度经回调收集，待其结束后统一发出
    （与原 endpoint 行为一致，中间无交错 yield）。
    """
    from services.tool_executor import run_tools

    progress_events: list[dict] = []
    tool_results = await run_tools(
        valid_calls, tool_exec_ctx, send_chunk=progress_events.append,
    )
    events: list[dict] = list(progress_events)
    events.append({
        "toolResults": build_tool_results_display(tool_results, valid_calls),
    })
    appended = build_tool_round_messages(
        valid_calls, acc_content, acc_thinking, tool_results,
    )
    return events, appended


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
    request_params = build_chat_request_params(
        model, rest, base_url, temperature, body.tools,
    )

    async def _event_generator():
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

            modes = resolve_chat_modes(
                body.agentMode,
                body.chatAgentMode,
                body.writingMode,
                body.bookId,
                body.subagentRole,
            )

            tool_ctx_book: dict[str, Any] = {
                "bookId": body.bookId,
                "chapterId": body.chapterId,
                "currentChapterTitle": body.currentChapterTitle,
                "writingChapters": list(body.writingChapters or []),
                "availableOutlines": list(body.availableOutlines or []),
                "associatedChapterIds": list(body.associatedChapterIds or []),
                "associatedOutlineIds": list(body.associatedOutlineIds or []),
            }
            sub_rp = build_chat_request_params(model, rest, base_url, temperature)

            # ── 前置上下文：勾选记忆 + 关联章节/大纲内容，宿主预取后直接注入 ──
            # （取代旧的"前端拼文案 + 命令模型自己调工具去读"两套机制）
            from utils.chat_preflight import (
                build_associated_context_block,
                build_selected_memory_block,
                build_session_binding_prompt,
            )

            messages: list[dict] = list(body.messages or [])
            memory_block = await build_selected_memory_block(
                body.selectedMemoryIds, body.selectedForeshadowingIds,
            )
            inject_system_prompt(messages, memory_block)
            assoc_block = await build_associated_context_block(tool_ctx_book)
            inject_system_prompt(messages, assoc_block)

            # ── 写作专家：按需子专家（单次调用，非管线）────────────────────────
            if modes.is_writing_expert_book and body.subagentRole:
                async for evt in _stream_writing_subagent(
                    abort=abort,
                    key=key,
                    api_provider=body.apiProvider,
                    sub_rp=sub_rp,
                    tool_ctx=tool_ctx_book,
                    messages=messages,
                    model=model,
                    subagent_role=str(body.subagentRole),
                ):
                    yield evt
                return

            # ── Agent mode: 装载全量 skills 工具列表 ───────────────────────
            # 历史名 useToolRouter 是个误导词：这里并不做语义路由，只是
            # "是否把 skills/<name>/SKILL.md 解析出的工具一股脑塞给 LLM"开关。
            # 真正的工具选择由 LLM 自行基于 schema + description 决定。
            agent_tools: list[dict] = []
            if body.enableAgentTools and body.bookId and not request_params.get("tools"):
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

            is_collab = modes.is_collab
            if is_collab and agent_tools:
                from utils.collab_prompt import filter_collab_tools

                last_user = last_user_message_text(body.messages)
                agent_tools = filter_collab_tools(list(agent_tools), last_user)
                request_params["tools"] = agent_tools
                logger.info("[agent][collab] 工具过滤后 %d 个", len(agent_tools))

            tool_exec_ctx = {
                "bookId": body.bookId,
                "chapterId": body.chapterId,
                "currentChapterTitle": body.currentChapterTitle,
                "writingChapters": list(body.writingChapters or []),
                "availableOutlines": list(body.availableOutlines or []),
                "associatedChapterIds": list(body.associatedChapterIds or []),
                "associatedOutlineIds": list(body.associatedOutlineIds or []),
                "collabWriting": is_collab,
            }

            # ── 会话绑定说明（原前端 systemSuffix，文案权收归后端）────────────
            # 写作专家模式跳过：其 system prompt 的 tooling appendix 已含同等规则。
            if not modes.should_inject_writing_prompt:
                binding = build_session_binding_prompt(
                    tool_ctx_book,
                    tools_enabled=bool(body.enableAgentTools and body.bookId),
                )
                inject_system_prompt(messages, binding)

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
                inject_system_prompt(messages, collab_inject)

            if modes.should_inject_writing_prompt:
                from utils.writing_prompt import build_writing_main_system_prompt

                # Layer 1：强制注入风格基调
                try:
                    from database.crud.book_style import get_book_style
                    from dependencies import get_db as _get_db_for_style
                    tool_ctx_book["bookStyle"] = await get_book_style(
                        _get_db_for_style(), str(body.bookId)
                    )
                except Exception:
                    logger.exception("[ai/chat/stream] load book_style failed")
                    tool_ctx_book["bookStyle"] = None

                writing_inject = build_writing_main_system_prompt(tool_ctx_book)
                inject_system_prompt(messages, writing_inject)

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

                acc = StreamAccumulator()
                got_tool_calls = False

                async for chunk in stream:
                    if abort.is_set():
                        break

                    outcome = acc.process_chunk(chunk)
                    for evt in outcome.events:
                        yield json.dumps(evt)
                    if not outcome.has_choice:
                        continue

                    finish_reason = outcome.finish_reason
                    if finish_reason in ("stop", "length"):
                        if acc.tool_calls:
                            if valid_named_tool_calls(acc.tool_calls):
                                finish_reason = "tool_calls"
                            else:
                                yield json.dumps({"done": True, "model": used_model})
                                return
                        else:
                            yield json.dumps({"done": True, "model": used_model})
                            return

                    if finish_reason in ("tool_calls", "function_call"):
                        valid_calls = valid_named_tool_calls(acc.tool_calls)
                        if not valid_calls:
                            yield json.dumps({"done": True, "model": used_model})
                            return

                        # Emit tool calls for frontend display
                        yield json.dumps({
                            "toolCalls": valid_calls,
                            "toolCallsInProgress": True,
                            "partialContent": acc.content,
                            "partialThinking": acc.thinking,
                            "model": used_model,
                        })

                        if not agent_tools:
                            yield json.dumps({"done": True, "model": used_model})
                            return

                        # ── Agent loop: execute tools then continue ───────
                        round_events, appended = await _run_agent_tool_round(
                            valid_calls, tool_exec_ctx, acc.content, acc.thinking,
                        )
                        for evt in round_events:
                            yield json.dumps(evt)
                        messages = messages + appended

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

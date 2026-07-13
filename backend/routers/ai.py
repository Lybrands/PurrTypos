"""AI routes — model listing, title generation, and SSE chat streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from time import perf_counter
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from schemas.ai import (
    ChatStreamRequest,
    CreateAgentRunReviewRequest,
    EvaluateAgentReleaseRequest,
    EvaluateAgentRolloutRequest,
    GenerateTitleRequest,
    ListModelsRequest,
    ResolveToolApprovalRequest,
)
from utils.chat_stream import (
    StreamAccumulator,
    build_chat_request_params,
    build_tool_results_display,
    build_tool_round_messages,
    inject_system_prompt,
    last_user_message_text,
    valid_named_tool_calls,
)
from utils.session_title import normalize_session_title
from utils.url import normalize_base_url

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)

@router.post("/ai/tool-approvals/{approval_id}")
async def resolve_pending_tool_approval(
    approval_id: str,
    body: ResolveToolApprovalRequest,
):
    """Resolve one live Human-in-the-Loop approval request exactly once."""
    from services.tool_approval_service import resolve_tool_approval

    status = resolve_tool_approval(approval_id, body.approved)
    if status is None:
        return {
            "success": False,
            "error": "确认请求不存在、已过期或已被处理。",
        }
    return {"success": True, "data": {"status": status}}


@router.get("/ai/agent-runs/{run_id}/diagnostics")
async def get_agent_run_diagnostics(run_id: str):
    """Return persisted host traces plus deterministic operational checks."""
    from dependencies import get_db
    from services.agent_run_evaluation import evaluate_agent_run
    from services.agent_run_performance import evaluate_agent_run_performance
    from services.agent_run_review import get_run_reviews
    from services.agent_run_store import get_run, get_run_events

    db = get_db()
    run = await get_run(db, run_id)
    if run is None:
        return {"success": False, "error": "Agent Run 不存在"}
    events = await get_run_events(db, run_id)
    report = evaluate_agent_run(run, events)
    report["humanReviews"] = await get_run_reviews(db, run_id)
    report["performance"] = evaluate_agent_run_performance(events)
    return {"success": True, "data": report}


@router.get("/ai/agent-run-review-rubric")
async def get_agent_run_review_rubric():
    """Expose the fixed human-review criteria before a reviewer scores a run."""
    from services.agent_run_review import get_rubric

    return {"success": True, "data": get_rubric()}


@router.get("/ai/agent-pilot-cases")
async def get_agent_pilot_cases():
    """Return the stable real-model cases used during the pilot phase."""
    from services.agent_pilot_cases import get_pilot_cases

    return {"success": True, "data": get_pilot_cases()}


@router.get("/ai/agent-runtime-regressions")
async def get_agent_runtime_regressions():
    """Run content-free operational incidents against the current evaluator."""
    from services.agent_runtime_regression import run_runtime_regression_suite

    return {"success": True, "data": run_runtime_regression_suite()}


@router.get("/ai/agent-security-redteam")
async def get_agent_security_redteam():
    """Run deterministic, content-free host security boundary checks."""
    from services.agent_security_redteam import run_agent_security_redteam_suite

    return {"success": True, "data": run_agent_security_redteam_suite()}


@router.get("/ai/agent-runs/{run_id}/reviews")
async def list_agent_run_reviews(run_id: str):
    from dependencies import get_db
    from services.agent_run_review import get_run_reviews
    from services.agent_run_store import get_run

    db = get_db()
    if await get_run(db, run_id) is None:
        return {"success": False, "error": "Agent Run 不存在"}
    return {"success": True, "data": await get_run_reviews(db, run_id)}


@router.post("/ai/agent-runs/{run_id}/reviews")
async def create_agent_run_review(run_id: str, body: CreateAgentRunReviewRequest):
    from dependencies import get_db
    from services.agent_run_review import create_review
    from services.agent_run_store import get_run

    db = get_db()
    if await get_run(db, run_id) is None:
        return {"success": False, "error": "Agent Run 不存在"}
    try:
        review = await create_review(
            db,
            run_id,
            body.scores,
            notes=body.notes,
            evaluator=body.evaluator,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "data": review}


@router.get("/ai/agent-release/config")
async def get_agent_release_config():
    """Return the release identity and deterministic gate thresholds."""
    from config import AGENT_RELEASE_VERSION, AGENT_ROLLOUT_COHORT
    from services.agent_release_control import RELEASE_POLICY

    return {
        "success": True,
        "data": {
            "version": AGENT_RELEASE_VERSION,
            "cohort": AGENT_ROLLOUT_COHORT,
            "policy": dict(RELEASE_POLICY),
        },
    }


@router.post("/ai/agent-release/gate")
async def evaluate_agent_release(body: EvaluateAgentReleaseRequest):
    """Decide whether reviewed pilot runs qualify for a canary release."""
    from dependencies import get_db
    from services.agent_release_control import evaluate_release_runs

    return {"success": True, "data": await evaluate_release_runs(get_db(), body.pilotRunIds)}


@router.post("/ai/agent-release/rollout")
async def evaluate_agent_rollout(body: EvaluateAgentRolloutRequest):
    """Compare baseline and candidate cohorts and recommend promote/hold/rollback."""
    from dependencies import get_db
    from services.agent_release_control import evaluate_rollout_runs

    overlap = sorted(set(body.baselineRunIds) & set(body.candidateRunIds))
    if overlap:
        return {
            "success": False,
            "error": "The same run cannot belong to both rollout cohorts.",
            "data": {"overlappingRunIds": overlap},
        }
    return {
        "success": True,
        "data": await evaluate_rollout_runs(
            get_db(), body.baselineRunIds, body.candidateRunIds,
        ),
    }


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

async def _run_agent_tool_round(
    valid_calls: list[dict],
    tool_exec_ctx: dict[str, Any],
    acc_content: str,
    acc_thinking: str,
    signal: asyncio.Event | None = None,
) -> AsyncIterator[tuple[dict[str, Any], list[dict] | None]]:
    """Stream one tool round while preserving its message continuation.

    A Human-in-the-Loop tool can pause ``run_tools`` for minutes. Its approval
    event must reach the SSE client before the task completes; otherwise the
    UI cannot approve it and both sides deadlock. Only the final yielded item
    carries the messages to append to the model history.
    """
    from services.tool_executor import run_tools

    progress_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    tool_task = asyncio.create_task(run_tools(
        valid_calls,
        tool_exec_ctx,
        send_chunk=progress_events.put_nowait,
        signal=signal,
    ))
    try:
        while True:
            if tool_task.done():
                while not progress_events.empty():
                    yield progress_events.get_nowait(), None
                tool_results = await tool_task
                appended = build_tool_round_messages(
                    valid_calls, acc_content, acc_thinking, tool_results,
                )
                yield {
                    "toolResults": build_tool_results_display(tool_results, valid_calls),
                }, appended
                return

            next_event = asyncio.create_task(progress_events.get())
            done, _ = await asyncio.wait(
                {tool_task, next_event},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if next_event in done:
                yield next_event.result(), None
                continue
            next_event.cancel()
            with suppress(asyncio.CancelledError):
                await next_event
    finally:
        if not tool_task.done():
            tool_task.cancel()
            with suppress(asyncio.CancelledError):
                await tool_task


def _is_tool_choice_compatibility_error(error: Exception) -> bool:
    """Return true only for provider rejections of forced tool selection."""
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status not in {400, 422}:
        return False
    text = str(error or "").lower()
    return any(marker in text for marker in (
        "tool_choice",
        "tool choice",
        "forced tool",
        "thinking mode",
        "extended thinking",
    ))


def _classify_tool_round_messages(messages: list[dict]) -> tuple[str, str | None]:
    """Classify persisted tool results without treating user rejection as a crash."""
    approval_statuses: set[str] = set()
    errors: list[str] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            continue
        approval_status = str(payload.get("approvalStatus") or "").strip()
        if approval_status:
            approval_statuses.add(approval_status)
        error = str(payload.get("error") or "").strip()
        if error and approval_status != "rejected":
            errors.append(error)

    if "canceled" in approval_statuses:
        return "canceled", "工具批准流程已随请求中止。"
    if approval_statuses & {"timed_out", "unavailable"}:
        return "failed", "工具批准请求超时或当前不可用，未执行操作。"
    if errors:
        return "failed", "工具执行失败，Agent 已停止；可在运行诊断中查看失败阶段。"
    if "rejected" in approval_statuses:
        return "declined", None
    return "completed", None


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
    # Caller-owned selection semantics stay on the compatibility path until
    # Core exposes the full provider tool_choice contract (including named
    # choices). Capture this before plan scoping can mutate request_params.
    caller_tool_choice = request_params.get("tool_choice")

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

            tool_ctx_book: dict[str, Any] = {
                "bookId": body.bookId,
                "chapterId": body.chapterId,
                "currentChapterTitle": body.currentChapterTitle,
                "writingChapters": list(body.writingChapters or []),
                "availableOutlines": list(body.availableOutlines or []),
                "associatedChapterIds": list(body.associatedChapterIds or []),
                "associatedOutlineIds": list(body.associatedOutlineIds or []),
                "chatAgentMode": body.chatAgentMode or "",
                "contextWindow": body.contextWindow or (opts.get("context_window") if isinstance(opts, dict) else None),
            }
            sub_rp = build_chat_request_params(model, rest, base_url, temperature)

            # ── 前置上下文：勾选记忆 + 关联章节/大纲内容，宿主预取后直接注入 ──
            # （取代旧的"前端拼文案 + 命令模型自己调工具去读"两套机制）
            from utils.chat_preflight import (
                build_associated_context_block,
                frame_untrusted_context_blocks,
                build_selected_memory_block,
                build_session_binding_prompt,
            )

            messages: list[dict] = list(body.messages or [])
            latest_user_prompt = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    latest_user_prompt = str(msg.get("content") or "")
                    break
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
            budget_tools: list[dict] = list(request_params.get("tools") or [])

            tool_exec_ctx = {
                "bookId": body.bookId,
                "chapterId": body.chapterId,
                "currentChapterTitle": body.currentChapterTitle,
                "writingChapters": list(body.writingChapters or []),
                "availableOutlines": list(body.availableOutlines or []),
                "associatedChapterIds": list(body.associatedChapterIds or []),
                "associatedOutlineIds": list(body.associatedOutlineIds or []),
                "contextWindow": body.contextWindow or (opts.get("context_window") if isinstance(opts, dict) else None),
            }

            # ── 会话绑定说明（原前端 systemSuffix，文案权收归后端）────────────
            binding = build_session_binding_prompt(
                tool_ctx_book,
                tools_enabled=bool(body.enableAgentTools and body.bookId),
            )

            # ── Agent Run To-dos：后端权威 run 状态机 + SSE 事件流 ───────────
            agent_run = None
            agent_run_events: list[dict[str, Any]] = []
            execution_prompt = ""

            def _drain_agent_run_events() -> list[dict[str, Any]]:
                drained = list(agent_run_events)
                agent_run_events.clear()
                return drained

            latest_user_text = last_user_message_text(messages)
            available_tool_names = {
                str((tool.get("function") or {}).get("name") or "").strip()
                for tool in agent_tools
                if isinstance(tool, dict)
            }
            available_tool_names = {name for name in available_tool_names if name}
            try:
                from dependencies import get_db
                from application.event_sinks import LegacyChunkEventSink
                from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
                from services.agent_run_controller import AgentRunController
                from services.task_planner import should_request_task_plan

                should_run_todos = should_request_task_plan(
                    user_text=latest_user_text,
                    book_id=body.bookId,
                    enable_agent_tools=bool(body.enableAgentTools),
                    chat_agent_mode=body.chatAgentMode,
                )
                if should_run_todos:
                    agent_run = AgentRunController(
                        repository=SqliteRunRepository(get_db()),
                        event_sink=LegacyChunkEventSink(agent_run_events.append),
                    )
                    await agent_run.start(
                        session_id=body.sessionId,
                        prompt=latest_user_text,
                        mode=body.chatAgentMode,
                        key=key,
                        api_provider=body.apiProvider,
                        planner_options=sub_rp,
                        chat_agent_mode=body.chatAgentMode,
                        available_tool_names=available_tool_names,
                        signal=abort,
                        use_planner=True,
                    )
                    for evt in _drain_agent_run_events():
                        yield json.dumps(evt)
                    if agent_run.status == "failed":
                        yield json.dumps({
                            "error": agent_run.error or "Agent 计划生成失败，已停止执行。",
                        })
                        return
                    planned_tools = agent_run.allowed_tool_names()
                    execution_prompt = agent_run.execution_prompt()
                    if agent_tools:
                        planned_definitions = [
                            tool
                            for tool in agent_tools
                            if str((tool.get("function") or {}).get("name") or "").strip()
                            in planned_tools
                        ]
                        budget_tools = planned_definitions
            except Exception:
                logger.exception("[ai/chat/stream] agent run init failed")

            from services.ai_capabilities import normalize_thinking_enabled
            from services.provider_capability_cache import (
                mark_required_tool_choice_unsupported,
                provider_capability_key,
                required_tool_choice_is_unsupported,
            )

            provider_capability = provider_capability_key(
                api_provider=body.apiProvider,
                base_url=base_url,
                model=model,
                thinking_enabled=normalize_thinking_enabled(request_params),
            )
            force_tool_choice = not required_tool_choice_is_unsupported(provider_capability)

            def _apply_current_tool_scope() -> None:
                if not agent_run or not agent_tools:
                    return
                allowed_now = agent_run.allowed_tool_names_for_current_transition()
                tool_exec_ctx["allowedToolNames"] = allowed_now
                definitions = [
                    tool
                    for tool in agent_tools
                    if str((tool.get("function") or {}).get("name") or "").strip()
                    in allowed_now
                ]
                if definitions:
                    request_params["tools"] = definitions
                    if force_tool_choice:
                        request_params["tool_choice"] = "required"
                    else:
                        request_params.pop("tool_choice", None)
                else:
                    request_params.pop("tools", None)
                    request_params.pop("tool_choice", None)

            _apply_current_tool_scope()

            # One host-owned budget covers messages, generated context, tool
            # schemas, model output and future tool-round growth.
            from services.ai_capabilities import (
                build_anthropic_thinking_param,
                normalize_thinking_enabled,
            )
            from utils.context_budget import (
                allocate_context_budget,
                estimate_messages_tokens,
                estimate_text_tokens,
                trim_messages_by_turn,
            )

            context_window = body.contextWindow or (
                opts.get("context_window") if isinstance(opts, dict) else None
            )
            output_reserve = request_params.get("max_tokens")
            if body.apiProvider == "anthropic":
                _, output_reserve = build_anthropic_thinking_param(
                    normalize_thinking_enabled(request_params),
                    output_reserve,
                )
            allocation = allocate_context_budget(
                context_window=context_window,
                output_reserve_tokens=output_reserve,
                tools=budget_tools,
                has_memory=bool(body.bookId),
                has_associated_context=bool(
                    body.associatedChapterIds or body.associatedOutlineIds
                ),
            )

            memory_block = await build_selected_memory_block(
                body.selectedMemoryIds,
                body.selectedForeshadowingIds,
                book_id=body.bookId,
                user_prompt=latest_user_prompt,
                mode=body.chatAgentMode or "",
                context_window=context_window,
                memory_budget=allocation.memory_context_tokens,
            )
            memory_tokens = estimate_text_tokens(memory_block)
            unused_memory_tokens = max(
                0,
                allocation.memory_context_tokens - memory_tokens,
            )
            tool_ctx_book["associatedContextBudget"] = (
                allocation.associated_context_tokens + unused_memory_tokens
            )
            assoc_block = await build_associated_context_block(tool_ctx_book)
            associated_tokens = estimate_text_tokens(assoc_block)
            untrusted_context_block = frame_untrusted_context_blocks({
                "long_term_memory": memory_block,
                "associated_chapters_and_outlines": assoc_block,
            })

            context_blocks = [
                block
                for block in (untrusted_context_block, binding, execution_prompt)
                if block
            ]
            fixed_context_tokens = max(
                0,
                estimate_messages_tokens([{
                    "role": "system",
                    "content": "\n\n".join(reversed(context_blocks)),
                }]) - 2,
            )
            trimmed = trim_messages_by_turn(
                messages,
                max(0, allocation.provider_input_tokens - fixed_context_tokens),
            )
            messages = trimmed.messages
            inject_system_prompt(messages, untrusted_context_block)
            inject_system_prompt(messages, binding)
            inject_system_prompt(messages, execution_prompt)

            context_diagnostics = allocation.diagnostics(
                messages,
                dropped_messages=trimmed.dropped_count,
                memory_tokens=memory_tokens,
                associated_tokens=associated_tokens,
            )
            if agent_run:
                await agent_run.record_trace(
                    "context_budget",
                    (
                        "overflow"
                        if context_diagnostics["overflowTokens"] > 0 or trimmed.overflow_tokens > 0
                        else "within_budget"
                    ),
                    details=context_diagnostics,
                )
            logger.info("[agent] context budget %s", context_diagnostics)
            yield json.dumps({"contextBudget": context_diagnostics})
            if context_diagnostics["overflowTokens"] > 0 or trimmed.overflow_tokens > 0:
                budget_error = (
                    "当前问题与必要系统上下文超过了所配置的模型窗口。"
                    "请减少勾选的上下文，或选择更大的上下文窗口。"
                )
                if agent_run:
                    await agent_run.fail(error=budget_error)
                    for run_evt in _drain_agent_run_events():
                        yield json.dumps(run_evt)
                yield json.dumps({
                    "error": budget_error,
                    "contextBudget": context_diagnostics,
                })
                return

            from config import AGENT_CORE_RUNTIME_ENABLED

            if AGENT_CORE_RUNTIME_ENABLED and caller_tool_choice is None:
                from application.legacy_runtime_bridge import (
                    stream_core_runtime_as_legacy_chunks,
                )

                runtime_tool_definitions = (
                    list(agent_tools)
                    if agent_tools
                    else list(request_params.get("tools") or [])
                )
                async for runtime_chunk in stream_core_runtime_as_legacy_chunks(
                    api_key=key,
                    api_provider=body.apiProvider,
                    model=model,
                    request_options=request_params,
                    messages=messages,
                    tool_definitions=runtime_tool_definitions,
                    executable_tools=bool(agent_tools),
                    tool_execution_context=tool_exec_ctx,
                    agent_run=agent_run,
                    agent_run_events=agent_run_events,
                    session_id=body.sessionId,
                    mode=body.chatAgentMode,
                    round_input_tokens=allocation.round_input_tokens,
                    force_tool_choice=force_tool_choice,
                    on_required_tool_choice_unsupported=(
                        lambda: mark_required_tool_choice_unsupported(
                            provider_capability
                        )
                    ),
                    signal=abort,
                ):
                    yield json.dumps(runtime_chunk)
                return

            used_model = model
            _MAX_ROUNDS = 6

            for _round in range(_MAX_ROUNDS):
                if abort.is_set():
                    break

                _apply_current_tool_scope()

                if _round > 0:
                    round_trimmed = trim_messages_by_turn(
                        messages,
                        allocation.round_input_tokens,
                    )
                    messages = round_trimmed.messages
                    if round_trimmed.overflow_tokens > 0:
                        round_budget_error = (
                            "工具结果超过了剩余上下文窗口；宿主保留了最近的完整工具回合，"
                            "并已停止继续执行。"
                        )
                        if agent_run:
                            await agent_run.record_trace(
                                "context_budget",
                                "overflow_after_tool",
                                details={"round": _round + 1},
                            )
                            await agent_run.fail(error=round_budget_error)
                            for run_evt in _drain_agent_run_events():
                                yield json.dumps(run_evt)
                        yield json.dumps({"error": round_budget_error})
                        return

                model_round_started = perf_counter()
                tool_call_required = bool(
                    agent_run
                    and request_params.get("tools")
                    and request_params.get("tool_choice") == "required"
                )
                try:
                    result = await create_chat_stream(
                        key, messages, request_params, body.apiProvider, signal=abort,
                    )
                except Exception as error:
                    if not tool_call_required or not _is_tool_choice_compatibility_error(error):
                        raise
                    force_tool_choice = False
                    mark_required_tool_choice_unsupported(provider_capability)
                    request_params.pop("tool_choice", None)
                    if agent_run:
                        await agent_run.record_trace(
                            "tool_choice",
                            "provider_fallback_auto",
                            details={
                                "round": _round + 1,
                                "errorType": type(error).__name__,
                            },
                        )
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
                        # Tool-transition prose is not a user answer.  Suppress
                        # it so a provider cannot leak chain-of-thought or a
                        # textual imitation of a function call into the chat.
                        if tool_call_required and evt.get("delta"):
                            continue
                        if agent_run and evt.get("delta"):
                            await agent_run.on_model_delta()
                            for run_evt in _drain_agent_run_events():
                                yield json.dumps(run_evt)
                        yield json.dumps(evt)
                    if not outcome.has_choice:
                        continue

                    finish_reason = outcome.finish_reason
                    if agent_run and finish_reason:
                        await agent_run.record_trace(
                            "model_round",
                            str(finish_reason),
                            details={
                                "round": _round + 1,
                                "toolCallCount": len(valid_named_tool_calls(acc.tool_calls)),
                            },
                            duration_ms=round((perf_counter() - model_round_started) * 1000),
                        )
                    if finish_reason in ("stop", "length"):
                        if tool_call_required and not valid_named_tool_calls(acc.tool_calls):
                            missing_tool_error = (
                                "当前计划步骤必须调用工具，但模型没有返回结构化工具调用。"
                                "本轮已停止，未把模型生成的伪调用文本展示或执行。"
                            )
                            if agent_run:
                                await agent_run.record_trace(
                                    "tool_round",
                                    "missing_required_call",
                                    details={"round": _round + 1},
                                )
                                await agent_run.fail(error=missing_tool_error)
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            yield json.dumps({"error": missing_tool_error})
                            return
                        if acc.tool_calls:
                            if valid_named_tool_calls(acc.tool_calls):
                                finish_reason = "tool_calls"
                            else:
                                if agent_run:
                                    await agent_run.complete(final_response=acc.content)
                                    for run_evt in _drain_agent_run_events():
                                        yield json.dumps(run_evt)
                                yield json.dumps({"done": True, "model": used_model})
                                return
                        else:
                            if agent_run:
                                await agent_run.complete(final_response=acc.content)
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            yield json.dumps({"done": True, "model": used_model})
                            return

                    if finish_reason in ("tool_calls", "function_call"):
                        valid_calls = valid_named_tool_calls(acc.tool_calls)
                        if not valid_calls:
                            if agent_run:
                                await agent_run.complete(final_response=acc.content)
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            yield json.dumps({"done": True, "model": used_model})
                            return

                        if _round >= _MAX_ROUNDS - 1:
                            max_round_error = (
                                "Agent 已达到最大工具轮次；为避免执行一个无法再交给模型消费的"
                                "工具结果，本轮未继续执行。"
                            )
                            if agent_run:
                                await agent_run.fail(error=max_round_error)
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            yield json.dumps({"error": max_round_error})
                            return

                        tool_round_authorized = True
                        requested_names: set[str] = set()
                        allowed_names: set[str] = set()
                        if agent_run:
                            tool_names = [
                                str((tc.get("function") or {}).get("name") or "").strip()
                                for tc in valid_calls
                            ]
                            requested_names = {name for name in tool_names if name}
                            allowed_names = set(tool_exec_ctx.get("allowedToolNames") or set())
                            tool_round_authorized = requested_names.issubset(allowed_names)
                            if tool_round_authorized:
                                await agent_run.on_tool_calls_started(sorted(requested_names))
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)

                        # Emit tool calls for frontend display
                        yield json.dumps({
                            "toolCalls": valid_calls,
                            "toolCallsInProgress": True,
                            "partialContent": "" if tool_call_required else acc.content,
                            "partialThinking": acc.thinking,
                            "model": used_model,
                        })

                        if not agent_tools:
                            if agent_run:
                                await agent_run.complete(final_response=acc.content)
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            yield json.dumps({"done": True, "model": used_model})
                            return

                        # ── Agent loop: execute tools then continue ───────
                        appended: list[dict] = []
                        tool_round_started = perf_counter()
                        async for evt, round_messages in _run_agent_tool_round(
                            valid_calls,
                            tool_exec_ctx,
                            "" if tool_call_required else acc.content,
                            acc.thinking,
                            abort,
                        ):
                            yield json.dumps(evt)
                            if round_messages is not None:
                                appended = round_messages
                        result_outcome, result_error = _classify_tool_round_messages(appended)
                        if not tool_round_authorized:
                            result_outcome = "rejected"
                            result_error = "模型请求了当前计划未授权的工具，Agent 已停止。"
                        if agent_run:
                            await agent_run.record_trace(
                                "tool_round",
                                result_outcome,
                                details={
                                    "round": _round + 1,
                                    "requestedTools": sorted(requested_names),
                                    "allowedTools": sorted(allowed_names),
                                    "callCount": len(valid_calls),
                                },
                                duration_ms=round((perf_counter() - tool_round_started) * 1000),
                            )
                        if result_outcome == "canceled":
                            if agent_run:
                                await agent_run.cancel(reason="approval_canceled")
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            return
                        if result_error:
                            if agent_run:
                                await agent_run.fail(error=result_error)
                                for run_evt in _drain_agent_run_events():
                                    yield json.dumps(run_evt)
                            yield json.dumps({"error": result_error})
                            return
                        if agent_run and result_outcome in {"completed", "declined"}:
                            await agent_run.on_tool_round_completed()
                            for run_evt in _drain_agent_run_events():
                                yield json.dumps(run_evt)
                        messages = messages + appended

                        got_tool_calls = True
                        break  # restart with updated messages

                if not got_tool_calls:
                    if not abort.is_set():
                        if agent_run:
                            await agent_run.complete(final_response=acc.content)
                            for run_evt in _drain_agent_run_events():
                                yield json.dumps(run_evt)
                        yield json.dumps({"done": True, "model": used_model})
                    return

            # Max rounds reached
            if not abort.is_set():
                if agent_run:
                    await agent_run.complete(final_response="")
                    for run_evt in _drain_agent_run_events():
                        yield json.dumps(run_evt)
                yield json.dumps({"done": True, "model": used_model})

        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.TimeoutException) as e:
            user_msg = "模型服务流式响应中断，请检查网络或稍后重试。"
            logger.warning(
                "[ai/chat/stream] upstream stream interrupted: %s: %s",
                type(e).__name__,
                e,
            )
            if 'agent_run' in locals() and agent_run:
                await agent_run.record_trace(
                    "stream",
                    "interrupted",
                    details={"errorType": type(e).__name__},
                )
                await agent_run.fail(error=user_msg)
                for run_evt in _drain_agent_run_events():
                    yield json.dumps(run_evt)
            yield json.dumps({"error": user_msg})
        except Exception as e:
            logger.exception("[ai/chat/stream] error")
            user_msg = "Agent 运行过程中发生异常，已安全停止；请稍后重试。"
            if 'agent_run' in locals() and agent_run:
                await agent_run.record_trace(
                    "runtime_error",
                    "exception",
                    details={"errorType": type(e).__name__},
                )
                await agent_run.fail(error=user_msg)
                for run_evt in _drain_agent_run_events():
                    yield json.dumps(run_evt)
            yield json.dumps({"error": user_msg})
        finally:
            if 'agent_run' in locals() and agent_run and agent_run.status == "running":
                await agent_run.cancel(reason="client_disconnected_or_stream_canceled")
            abort.set()
            disconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await disconnect_task

    return EventSourceResponse(_event_generator(), media_type="text/event-stream")

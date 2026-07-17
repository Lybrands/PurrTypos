"""AI routes — model listing, title generation, and SSE chat streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import anyio
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from agent_core.contracts import RunProvenance
from application.request_mapping import (
    UnsupportedCallerToolContractError,
    build_chat_provider_options,
)
from schemas.ai import (
    ChatStreamRequest,
    GenerateTitleRequest,
    ListModelsRequest,
    ResolveToolApprovalRequest,
)
from utils.session_title import normalize_session_title
from utils.url import normalize_base_url

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)


class _AgentClientDisconnected(Exception):
    """A guarded ASGI send observed the client disconnect."""


class _AgentEventSourceResponse(EventSourceResponse):
    """Close the Agent iterator when ASGI 2.4 reports disconnect via send()."""

    def __init__(
        self,
        content,
        *,
        abort: asyncio.Event,
        cleanup_complete: asyncio.Event,
        **kwargs,
    ) -> None:
        self._agent_abort = abort
        self._agent_cleanup_complete = cleanup_complete
        super().__init__(content, **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        async def _guarded_send(message) -> None:
            try:
                await send(message)
            except OSError as error:
                # Mark cancellation at the exact transport observation point,
                # before sse-starlette's task group cancels the stream task.
                self._agent_abort.set()
                # If this was the body send, the iterator is suspended at a
                # yield and can be closed here. If it was a ping send, aclose
                # reports that the iterator is running; in that case the abort
                # signal lets the body task drain to its durable terminal.
                aclose = getattr(self.body_iterator, "aclose", None)
                if aclose is not None:
                    with anyio.CancelScope(shield=True):
                        try:
                            await aclose()
                        except RuntimeError:
                            pass
                        except Exception:
                            logger.exception(
                                "Agent stream cleanup failed after send disconnect"
                            )
                if not self._agent_cleanup_complete.is_set():
                    with anyio.move_on_after(5, shield=True):
                        await self._agent_cleanup_complete.wait()
                raise _AgentClientDisconnected from error

        try:
            await super().__call__(scope, receive, _guarded_send)
        except _AgentClientDisconnected:
            # ASGI spec 2.4 permits ``send`` to be the first place a closed
            # client is observed. Close the iterator explicitly even when the
            # disconnect listener never receives ``http.disconnect``.
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await aclose()
                    except Exception:
                        logger.exception(
                            "Agent stream cleanup failed after client disconnect"
                        )
            self._agent_cleanup_complete.set()
            # The peer is gone, so there is no response body left to finish.
            # Treat this as the transport's normal disconnect completion.
            return


@router.post("/ai/tool-approvals/{approval_id}")
async def resolve_pending_tool_approval(
    approval_id: str,
    body: ResolveToolApprovalRequest,
):
    """Resolve one live Human-in-the-Loop approval request exactly once."""
    from application.agent_composition import get_agent_composition

    try:
        composition = get_agent_composition()
    except RuntimeError:
        return {
            "success": False,
            "error": "Agent 当前不可用，无法处理确认请求。",
        }
    status = composition.resolve_approval(approval_id, body.approved)
    if status is None:
        return {
            "success": False,
            "error": "确认请求不存在、已过期或已被处理。",
        }
    return {
        "success": True,
        "data": {"status": getattr(status, "value", status)},
    }


@router.get("/ai/agent-runs/{run_id}/diagnostics")
async def get_agent_run_diagnostics(run_id: str):
    """Return persisted host traces plus deterministic operational checks."""
    from agent_core.evaluation import (
        evaluate_agent_run,
        evaluate_agent_run_performance,
    )
    from dependencies import get_db
    from infrastructure.persistence.run_store import get_run, get_run_events

    db = get_db()
    run = await get_run(db, run_id)
    if run is None:
        return {"success": False, "error": "Agent Run 不存在"}
    events = await get_run_events(db, run_id)
    report = evaluate_agent_run(run, events)
    report["performance"] = evaluate_agent_run_performance(events)
    return {"success": True, "data": report}


@router.get("/ai/agent-runtime-regressions")
async def get_agent_runtime_regressions():
    """Run content-free operational incidents against the current evaluator."""
    from application.operations.deterministic_checks import (
        run_runtime_regression_suite,
    )

    return {"success": True, "data": run_runtime_regression_suite()}


@router.get("/ai/agent-security-redteam")
async def get_agent_security_redteam():
    """Run deterministic, content-free host security boundary checks."""
    from application.operations.deterministic_checks import (
        run_agent_security_redteam_suite,
    )

    return {"success": True, "data": run_agent_security_redteam_suite()}


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
            from infrastructure.models.anthropic_chat import (
                generate_title as anth_title,
            )

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

        from infrastructure.models.openai_chat import generate_title as openai_title

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


async def _stream_composed_agent(
    *,
    body: ChatStreamRequest,
    api_key: str,
    provider_options: dict[str, Any],
    signal: asyncio.Event,
    provenance: RunProvenance | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the complete Agent Engine assembled by the application root."""

    from agent_core.events import AgentEvent
    from application.agent_composition import get_agent_composition
    from application.run_provenance import build_chat_run_provenance
    from application.request_mapping import (
        to_writing_agent_request,
        writing_run_options,
    )
    from application.sse_mapping import core_update_to_sse_chunk
    from infrastructure.models.capabilities import normalize_thinking_enabled

    composition = get_agent_composition()
    provider_capabilities = composition.provider_capabilities
    request = to_writing_agent_request(body, provider_options)
    provenance = provenance or build_chat_run_provenance(body)
    capability_key = provider_capabilities.key(
        api_provider=body.apiProvider,
        base_url=str(provider_options.get("baseURL") or ""),
        model=request.model.model,
        thinking_enabled=normalize_thinking_enabled(provider_options),
    )
    response_judges = composition.create_response_judges(api_key, request)
    options = writing_run_options(
        request,
        provider_options,
        force_planned_tool_choice=(
            not provider_capabilities.required_tool_choice_is_unsupported(
                capability_key
            )
        ),
        provenance=provenance,
        response_judges=response_judges,
    )
    core = composition.create_core(
        api_key,
        on_required_tool_choice_unsupported=(
            lambda: provider_capabilities.mark_required_tool_choice_unsupported(
                capability_key
            )
        ),
    )
    core_stream = core.run(request, options=options, signal=signal)
    run_id: str | None = None
    try:
        async for update in core_stream:
            if isinstance(update, AgentEvent):
                composition.observe_event(update)
                run_id = update.run_id or run_id
            else:
                run_id = update.run_id or run_id
            chunk = core_update_to_sse_chunk(update, model=request.model.model)
            if chunk is not None:
                yield chunk
    finally:
        # sse-starlette cancels its streaming task from an AnyIO cancel scope
        # when ASGI receives ``http.disconnect``.  Shield the inner generator
        # close so AgentCore can durably commit consumer_disconnected before
        # the response task exits; otherwise only the in-memory approval map
        # is cleared and the persisted Run can remain stuck at running.
        with anyio.CancelScope(shield=True):
            try:
                await core_stream.aclose()
            finally:
                if run_id:
                    composition.release_run(run_id)


@router.post("/ai/chat/stream")
async def chat_stream(body: ChatStreamRequest):
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
    request_params = build_chat_provider_options(
        model,
        rest,
        base_url,
        temperature,
    )

    # EventSourceResponse is the sole ASGI ``receive`` owner. Its disconnect
    # callback translates the transport event into the cancellation signal
    # shared by planner, model, and tool operations.
    abort = asyncio.Event()
    stream_cleanup_complete = asyncio.Event()

    async def _on_client_disconnect(_message: dict[str, Any]) -> None:
        abort.set()
        with anyio.move_on_after(5, shield=True):
            await stream_cleanup_complete.wait()

    async def _event_generator():
        composed_stream = _stream_composed_agent(
            body=body,
            api_key=key,
            provider_options=request_params,
            signal=abort,
        )
        try:
            async for composed_chunk in composed_stream:
                yield json.dumps(composed_chunk)
        except UnsupportedCallerToolContractError as error:
            logger.info("[ai/chat/stream] rejected request contract: %s", error)
            yield json.dumps({
                "error": (
                    "当前 Agent 不支持调用方自定义 tools 或 tool_choice；"
                    "请求已停止，未调用模型或执行工具。"
                ),
            })
        except Exception:
            logger.exception("[ai/chat/stream] composed Agent failed")
            yield json.dumps({
                "error": "Agent 运行过程中发生异常，已安全停止；请稍后重试。",
            })
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await composed_stream.aclose()
                finally:
                    abort.set()
                    stream_cleanup_complete.set()

    async def _transport_event_generator():
        """Drain cancellation events without writing after disconnect."""

        source = _event_generator()
        try:
            async for payload in source:
                if not abort.is_set():
                    yield payload
        finally:
            with anyio.CancelScope(shield=True):
                await source.aclose()

    return _AgentEventSourceResponse(
        _transport_event_generator(),
        abort=abort,
        cleanup_complete=stream_cleanup_complete,
        media_type="text/event-stream",
        client_close_handler_callable=_on_client_disconnect,
    )

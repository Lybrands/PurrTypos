"""AI routes — model listing, title generation, and SSE chat streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import anyio
from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from purra.contracts import (
    AgentRunResult,
    ModelRequest,
    RunProvenance,
)
from application.request_mapping import (
    UnsupportedCallerToolContractError,
    build_chat_provider_options,
    validate_writing_request_contract,
)
from schemas.ai import (
    CaptureAiErrorReportRequest,
    ChatStreamRequest,
    CreateAgentDelegationRequest,
    GenerateTitleRequest,
    ListModelsRequest,
    ResolveToolApprovalRequest,
    SubmitAiErrorReportRequest,
)
from utils.session_title import normalize_session_title
from utils.url import normalize_base_url

router = APIRouter(tags=["ai"])
logger = logging.getLogger(__name__)

_ERROR_REPORT_DIAGNOSTIC_KEYS = frozenset({
    "agentMode",
    "associatedChapterCount",
    "associatedOutlineCount",
    "contextWindow",
    "messageCount",
    "provider",
    "selectedForeshadowingCount",
    "selectedMemoryCount",
    "thinkingEnabled",
    "taskType",
    "toolsEnabled",
})
class _AgentClientDisconnected(Exception):
    """A guarded ASGI send observed the client disconnect."""


def _writing_chat_request_digest(body: ChatStreamRequest) -> str:
    from infrastructure.persistence.writing_chat_request_store import (
        writing_chat_request_digest,
    )

    payload = body.model_dump(mode="json")
    payload["baseURL"] = normalize_base_url(body.baseURL)
    return writing_chat_request_digest(payload)


def _validate_durable_writing_request(body: ChatStreamRequest) -> None:
    if body.chatAgentMode != "agent" or body.sessionId is None:
        raise HTTPException(
            status_code=400,
            detail="Durable Writing receipt requires an Agent session request",
        )
    if not str(body.apiKey or "").strip():
        raise HTTPException(status_code=400, detail="API Key 为空")
    opts = body.options or {}
    model = str(opts.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="无效的模型参数")
    temperature = opts.get("temperature")
    rest = {k: v for k, v in opts.items() if k not in ("model", "temperature")}
    request_params = build_chat_provider_options(
        model,
        rest,
        normalize_base_url(body.baseURL),
        temperature,
    )
    try:
        validate_writing_request_contract(body, request_params)
    except (UnsupportedCallerToolContractError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.put("/ai/chat/requests/{request_id}")
async def reserve_writing_chat_request(
    request_id: str,
    body: ChatStreamRequest,
):
    from dependencies import get_db
    from infrastructure.persistence.writing_chat_request_store import (
        SqliteWritingChatRequestStore,
        WritingChatRequestConflictError,
    )

    normalized = str(request_id or "").strip()
    if not normalized or normalized != str(body.streamId or "").strip():
        raise HTTPException(status_code=400, detail="requestId must match streamId")
    _validate_durable_writing_request(body)
    try:
        receipt = await SqliteWritingChatRequestStore(get_db()).reserve(
            request_id=normalized,
            session_id=int(body.sessionId),
            request_digest=_writing_chat_request_digest(body),
            book_id=body.bookId,
            chapter_id=body.chapterId,
            expected_conversation_ids=body.expectedConversationIds,
            expected_run_ids=body.expectedRunIds,
        )
    except WritingChatRequestConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"success": True, "data": receipt.to_public_dict()}


@router.post("/ai/chat/requests/{request_id}/cancel")
async def cancel_writing_chat_request(request_id: str):
    from dependencies import get_db
    from infrastructure.persistence.run_execution_store import now_ms
    from infrastructure.persistence.writing_chat_request_store import (
        SqliteWritingChatRequestStore,
    )

    store = SqliteWritingChatRequestStore(get_db())
    receipt, application_needed = await store.request_cancel_and_claim(
        request_id,
        timestamp_ms=now_ms(),
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="Writing chat request not found")
    if (
        receipt.run_id
        and application_needed
    ):
        # The receipt supplies durable pre-Run identity; once associated, the
        # existing Run cancellation plane remains the execution authority.
        await cancel_agent_run(receipt.run_id)
        receipt = await store.mark_cancel_applied(request_id, receipt.run_id)
    return {"success": True, "data": receipt.to_public_dict()}


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


@router.post("/ai/error-reports")
async def capture_ai_error_report(body: CaptureAiErrorReportRequest):
    """Persist a content-free local index over an AI stream failure."""

    from dependencies import get_db
    from infrastructure.persistence.error_report_store import (
        capture_error_report,
    )

    diagnostics = {
        key: value
        for key, value in body.diagnostics.items()
        if key in _ERROR_REPORT_DIAGNOSTIC_KEYS
        and isinstance(value, (str, int, float, bool))
    }
    report = await capture_error_report(
        get_db(),
        stream_id=body.streamId,
        agent_run_id=body.agentRunId,
        session_id=body.sessionId,
        conversation_id=body.conversationId,
        book_id=body.bookId,
        chapter_id=body.chapterId,
        source=body.source[:80] or "ai_chat_stream",
        error_code=(body.errorCode or "")[:160] or None,
        error_message=body.errorMessage[:2000],
        model_name=(body.model or "")[:200] or None,
        diagnostics=diagnostics,
    )
    return {"success": True, "data": report}


@router.get("/ai/error-reports")
async def list_ai_error_reports(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    from dependencies import get_db
    from infrastructure.persistence.error_report_store import (
        ERROR_REPORT_STATUSES,
        list_error_reports,
    )

    normalized_status = str(status or "").strip() or None
    if normalized_status and normalized_status not in ERROR_REPORT_STATUSES:
        return {"success": False, "error": "错误报告状态无效"}
    reports = await list_error_reports(
        get_db(),
        status=normalized_status,
        limit=limit,
    )
    return {"success": True, "data": reports}


@router.get("/ai/error-reports/{report_id}")
async def get_ai_error_report(report_id: str):
    from dependencies import get_db
    from infrastructure.persistence.error_report_store import get_error_report

    report = await get_error_report(get_db(), report_id)
    if report is None:
        return {"success": False, "error": "错误报告不存在"}
    return {"success": True, "data": report}


@router.post("/ai/error-reports/{report_id}/submit")
async def submit_ai_error_report(
    report_id: str,
    body: SubmitAiErrorReportRequest,
):
    """Mark a captured local report as ready for developer review."""

    from dependencies import get_db
    from infrastructure.persistence.error_report_store import (
        submit_error_report,
    )

    user_note = str(body.userNote or "").strip() or None
    report = await submit_error_report(
        get_db(),
        report_id,
        user_note=user_note,
    )
    if report is None:
        return {"success": False, "error": "错误报告不存在"}
    return {"success": True, "data": report}


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
    status = await composition.resolve_approval(approval_id, body.approved)
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
    """Return diagnostics for one Run or its durable workflow tree."""
    from purra.evaluation import (
        classify_agent_run_failures,
        evaluate_agent_run,
        evaluate_agent_run_performance,
        evaluate_agent_run_recovery,
        evaluate_agent_run_stability,
    )
    from dependencies import get_db
    from application.artifact_maintenance import (
        artifact_maintenance_snapshot_view,
    )
    from infrastructure.persistence.run_store import get_run, get_run_events
    from infrastructure.persistence.sqlite_artifact_maintenance_repository import (
        SqliteArtifactMaintenanceRepository,
    )
    from infrastructure.persistence.sqlite_artifact_repository import (
        get_run_artifact_metrics,
    )

    db = get_db()
    run = await get_run(db, run_id)
    if run is None:
        return {"success": False, "error": "Agent Run 不存在"}
    root_events = await get_run_events(db, run_id)
    dispatched = any(
        str(event.get("eventType") or "") == "long_task.dispatched"
        for event in root_events
    )
    child_runs: list[dict[str, Any]] = []
    long_tasks: list[dict[str, Any]] = []
    events = [dict(event, runId=run_id) for event in root_events]
    if dispatched:
        child_runs, long_tasks = await asyncio.gather(
            db.fetch_all(
                "SELECT id, status, parent_run_id, root_run_id, agent_role, "
                "run_depth, model_provider, model_name, create_time, update_time "
                "FROM ai_agent_runs WHERE root_run_id = ? AND id <> ? "
                "ORDER BY create_time ASC, id ASC",
                [run_id, run_id],
            ),
            db.fetch_all(
                "SELECT id, kind, status, total_units, completed_units, "
                "failed_units, create_time, update_time "
                "FROM ai_agent_long_tasks WHERE created_by_run_id = ? "
                "ORDER BY create_time ASC, id ASC",
                [run_id],
            ),
        )
        child_event_groups = await asyncio.gather(*(
            get_run_events(db, str(child["id"]))
            for child in child_runs
        ))
        for child, child_events in zip(child_runs, child_event_groups):
            child_run_id = str(child["id"])
            events.extend(
                dict(event, runId=child_run_id) for event in child_events
            )
        events.sort(key=lambda event: int(event.get("id") or 0))
    workflow_status = None
    if dispatched:
        workflow_status = next((
            str(task.get("status") or "")
            for task in long_tasks
            if str(task.get("status") or "") in {"running", "pending"}
        ), None) or next((
            str(task.get("status") or "")
            for task in long_tasks
            if str(task.get("status") or "") == "paused"
        ), None) or (
            str(long_tasks[-1].get("status") or "done")
            if long_tasks else "done"
        )
    evaluation_run = dict(run)
    if workflow_status is not None:
        evaluation_run["status"] = (
            "done" if workflow_status == "completed" else workflow_status
        )
    report = evaluate_agent_run(evaluation_run, events)
    report["performance"] = evaluate_agent_run_performance(events)
    report["stability"] = evaluate_agent_run_stability(events)
    report["recovery"] = evaluate_agent_run_recovery(events)
    report["failureClassification"] = classify_agent_run_failures(
        evaluation_run,
        events,
    )
    artifact_metrics, maintenance = await asyncio.gather(
        get_run_artifact_metrics(db, run_id),
        SqliteArtifactMaintenanceRepository(db).inspect(run_id=run_id),
    )
    report["artifacts"] = artifact_metrics
    report["artifactMaintenance"] = artifact_maintenance_snapshot_view(
        maintenance
    )
    if dispatched:
        report["workflow"] = {
            "kind": "durable_long_task",
            "rootRunId": run_id,
            "status": workflow_status or "done",
            "runCount": 1 + len(child_runs),
            "childRunCount": len(child_runs),
            "activeChildRunIds": [
                str(child["id"])
                for child in child_runs
                if str(child.get("status") or "") == "running"
            ],
            "childRuns": [{
                "runId": str(child["id"]),
                "status": child.get("status"),
                "parentRunId": child.get("parent_run_id"),
                "agentRole": child.get("agent_role"),
                "depth": child.get("run_depth"),
                "modelProvider": child.get("model_provider"),
                "modelName": child.get("model_name"),
                "createTime": child.get("create_time"),
                "updateTime": child.get("update_time"),
            } for child in child_runs],
            "longTasks": [{
                "taskId": str(task["id"]),
                "kind": task.get("kind"),
                "status": task.get("status"),
                "totalUnits": task.get("total_units"),
                "completedUnits": task.get("completed_units"),
                "failedUnits": task.get("failed_units"),
                "createTime": task.get("create_time"),
                "updateTime": task.get("update_time"),
            } for task in long_tasks],
        }
    return {"success": True, "data": report}


@router.post("/ai/artifacts/maintenance")
async def maintain_agent_artifacts():
    """Safely reap invalid leases without enabling content retention GC."""

    from purra.artifacts import ArtifactMaintenancePolicy
    from application.artifact_maintenance import (
        artifact_maintenance_report_view,
        artifact_maintenance_snapshot_view,
        run_artifact_maintenance,
    )
    from dependencies import get_db
    from infrastructure.persistence.sqlite_artifact_maintenance_repository import (
        SqliteArtifactMaintenanceRepository,
    )

    repository = SqliteArtifactMaintenanceRepository(get_db())
    report = await run_artifact_maintenance(
        repository,
        ArtifactMaintenancePolicy(),
    )
    snapshot = await repository.inspect()
    return {
        "success": True,
        "data": {
            "report": artifact_maintenance_report_view(report),
            "snapshot": artifact_maintenance_snapshot_view(snapshot),
        },
    }


@router.get("/ai/agent-runs/{run_id}/stability-trend")
async def get_agent_run_stability_trend(
    run_id: str,
    scope: str = Query(
        default="auto",
        pattern="^(auto|session|book|screenplay_project|global)$",
    ),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return bounded, content-free stability trends for a Run's scope."""

    from application.agent_stability_service import (
        get_scoped_agent_stability_trend,
    )
    from dependencies import get_db
    from infrastructure.persistence.stability_query import (
        SqliteStabilityEvidenceGateway,
    )

    try:
        report = await get_scoped_agent_stability_trend(
            SqliteStabilityEvidenceGateway(get_db()),
            run_id,
            scope=scope,  # type: ignore[arg-type]
            limit=limit,
        )
    except ValueError as error:
        return {"success": False, "error": str(error)}
    if report is None:
        return {"success": False, "error": "Agent Run 不存在"}
    return {"success": True, "data": report}


@router.post("/ai/agent-runs/{run_id}/cancel")
async def cancel_agent_run(run_id: str):
    """Persist a cancellation request for the executor that owns this Run."""

    from application.agent_cancellation_service import (
        AgentCancellationService,
        RootCancellationTargetError,
    )
    from application.agent_composition import get_agent_composition
    from dependencies import get_db

    composition = get_agent_composition()
    try:
        result = await AgentCancellationService(
            get_db(),
            composition,
        ).cancel(run_id)
    except RootCancellationTargetError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result is None:
        return {"success": False, "error": "Agent Run 不存在"}
    if result["status"] not in {"canceled", "cancel_requested"}:
        return {"success": False, "error": "Agent Run 已结束"}
    return {"success": True, "data": result}


@router.post("/ai/agent-runs/{run_id}/delegations")
async def create_agent_delegation(
    run_id: str,
    body: CreateAgentDelegationRequest,
):
    from application.agent_delegation_service import AgentDelegationService
    from application.agent_composition import get_agent_composition
    from dependencies import get_db
    from infrastructure.persistence.run_store import get_run

    composition = get_agent_composition()
    db = get_db()
    try:
        parent = await get_run(db, run_id)
        if parent is None:
            raise ValueError("Agent Run does not exist")
        role_registry = await _persisted_run_role_registry(
            composition,
            db,
            parent,
        )
        if role_registry is None:
            raise ValueError("Agent profile does not support delegation")
        delegation = await AgentDelegationService(
            composition.delegation_repository,
            role_registry=role_registry,
        ).delegate(
            parent_run_id=run_id,
            agent_role=body.agentRole,
            objective=body.objective,
            input_payload=body.input,
            required=body.required,
            priority=body.priority,
        )
    except ValueError as error:
        return {"success": False, "error": str(error)}
    return {"success": True, "data": delegation}


@router.get("/ai/session-runs/latest")
async def get_latest_session_agent_run(
    session_id: int = Query(alias="sessionId", ge=1),
    request_id: str | None = Query(default=None, alias="requestId", max_length=200),
):
    """Return the latest session-owned Run for page recovery."""

    from application.agent_composition import get_agent_composition
    from application.agent_run_queries import AgentRunQueryService
    from dependencies import get_db
    from infrastructure.persistence.run_store import (
        get_latest_run_for_session,
        get_run_for_session_request,
    )
    from application.writing_proposal_read_model import (
        SqliteWritingProposalReadModel,
    )
    from infrastructure.persistence.writing_chat_request_store import (
        SqliteWritingChatRequestStore,
    )

    composition = get_agent_composition()
    normalized_request_id = str(request_id or "").strip()
    db = get_db()
    receipt_store = SqliteWritingChatRequestStore(db)
    receipt = (
        await receipt_store.get(normalized_request_id)
        if normalized_request_id
        else await receipt_store.latest_active_for_session(session_id)
    )
    if receipt is not None and receipt.session_id != session_id:
        receipt = None
    correlated_request_id = normalized_request_id or (
        receipt.request_id if receipt is not None else ""
    )
    run = (
        await get_run_for_session_request(
            db, session_id, correlated_request_id,
        )
        if correlated_request_id
        else await get_latest_run_for_session(db, session_id)
    )
    if run is not None and receipt is not None and receipt.run_id is None:
        receipt = await receipt_store.bind_run(
            receipt.request_id,
            str(run["id"]),
        )
    if run is None:
        return {
            "success": True,
            "data": (
                {
                    "request": receipt.to_public_dict(),
                    "prompt": "",
                    "snapshot": None,
                }
                if receipt is not None else None
            ),
        }
    snapshot = await AgentRunQueryService(
        composition.checkpoint_store,
        composition.output_repository,
        role_registry=await _persisted_run_role_registry(
            composition,
            db,
            run,
        ),
        product_event_query=SqliteWritingProposalReadModel(db),
    ).get_snapshot(str(run["id"]), limit=500)
    if snapshot is None:
        return {"success": True, "data": None}
    return {
        "success": True,
        "data": {
            **(
                {"request": receipt.to_public_dict()}
                if receipt is not None else {}
            ),
            "prompt": str(run.get("prompt") or ""),
            "snapshot": snapshot,
        },
    }


@router.get("/ai/agent-runs/{run_id}")
async def get_agent_run_snapshot(
    run_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Return a resumable Run snapshot and durable events after a cursor."""

    from application.agent_run_queries import AgentRunQueryService
    from application.agent_composition import get_agent_composition
    from application.writing_proposal_read_model import (
        SqliteWritingProposalReadModel,
    )
    from dependencies import get_db
    from infrastructure.persistence.run_store import get_run

    composition = get_agent_composition()
    db = get_db()
    run = await get_run(db, run_id)
    if run is None:
        return {"success": False, "error": "Agent Run 不存在"}
    snapshot = await AgentRunQueryService(
        composition.checkpoint_store,
        composition.output_repository,
        role_registry=await _persisted_run_role_registry(
            composition,
            db,
            run,
        ),
        product_event_query=SqliteWritingProposalReadModel(db),
    ).get_snapshot(
        run_id,
        after_event_id=after,
        limit=limit,
    )
    if snapshot is None:
        return {"success": False, "error": "Agent Run 不存在"}
    return {"success": True, "data": snapshot}


async def _persisted_run_role_registry(composition, db, run):
    """Resolve optional roles from the target Run or its persisted parent."""

    current = run
    visited: set[str] = set()
    resolved_profile = ""
    resolved_namespace = ""
    while current is not None:
        current_id = str(current.get("id") or "").strip()
        if current_id:
            if current_id in visited:
                raise ValueError("Agent Run parent lineage contains a cycle")
            visited.add(current_id)
        profile_id, domain_namespace = _persisted_profile_identity(current)
        inferred_namespace = await _persisted_domain_namespace(db, current)
        if (
            domain_namespace
            and inferred_namespace
            and domain_namespace != inferred_namespace
        ):
            raise ValueError(
                "persisted Agent domain namespace conflicts with Run binding"
            )
        domain_namespace = domain_namespace or inferred_namespace
        if profile_id:
            if resolved_profile and resolved_profile != profile_id:
                raise ValueError("Agent Run lineage has conflicting profiles")
            resolved_profile = profile_id
        if domain_namespace:
            if resolved_namespace and resolved_namespace != domain_namespace:
                raise ValueError("Agent Run lineage has conflicting domains")
            resolved_namespace = domain_namespace
        parent_run_id = str(current.get("parent_run_id") or "").strip()
        if not parent_run_id:
            break
        from infrastructure.persistence.run_store import get_run

        current = await get_run(db, parent_run_id)
    if not resolved_profile and not resolved_namespace:
        return None
    return composition.agent_role_registry_for_persisted_profile(
        profile_id=resolved_profile,
        domain_namespace=resolved_namespace,
    )


def _persisted_profile_identity(run) -> tuple[str, str]:
    raw = run.get("binding_attributes_json")
    if isinstance(raw, dict):
        attributes = raw
    else:
        try:
            parsed = json.loads(str(raw or "{}"))
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        attributes = parsed if isinstance(parsed, dict) else {}
    return (
        str(attributes.get("agentProfile") or "").strip(),
        str(attributes.get("domainNamespace") or "").strip(),
    )


async def _persisted_domain_namespace(db, run) -> str:
    """Infer legacy profile identity only from durable product discriminators."""

    from domains.screenplay_agent.agent_context import (
        SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
    )
    from domains.writing.contracts import WRITING_DOMAIN_NAMESPACE

    candidates: set[str] = set()
    binding_namespace = str(run.get("binding_namespace") or "").strip()
    if binding_namespace == "writing.chat.request":
        candidates.add(WRITING_DOMAIN_NAMESPACE)
    elif binding_namespace.startswith("screenplay.agent."):
        candidates.add(SCREENPLAY_AGENT_DOMAIN_NAMESPACE)

    session_id = run.get("session_id")
    if db is not None and session_id is not None:
        session = await db.fetch_one(
            "SELECT scope, chapter_id, book_id, screenplay_project_id "
            "FROM ai_sessions WHERE id = ?",
            [int(session_id)],
        )
        if session is not None:
            scope = str(session.get("scope") or "").strip()
            screenplay_owned = bool(
                scope == "screenplay"
                or str(session.get("screenplay_project_id") or "").strip()
            )
            writing_owned = bool(
                not screenplay_owned
                and (
                    scope in {"chapter", "setting"}
                    or str(session.get("chapter_id") or "").strip()
                    or str(session.get("book_id") or "").strip()
                )
            )
            if screenplay_owned:
                candidates.add(SCREENPLAY_AGENT_DOMAIN_NAMESPACE)
            elif writing_owned:
                candidates.add(WRITING_DOMAIN_NAMESPACE)

    if len(candidates) > 1:
        raise ValueError("persisted Agent profile discriminators conflict")
    return next(iter(candidates), "")


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


@router.get("/ai/agent-stability-quality-gate")
async def get_agent_stability_quality_gate():
    """Run promoted failure-classification incidents as a release gate."""

    from application.operations.deterministic_checks import (
        run_agent_stability_quality_gate,
    )

    return {"success": True, "data": run_agent_stability_quality_gate()}


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

    if body.apiProvider == "zai":
        try:
            from infrastructure.models.zai_chat import list_models as zai_models

            ids = await zai_models(key, base_url)
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

        if body.apiProvider == "zai":
            from infrastructure.models.zai_chat import generate_title as zai_title

            title = await zai_title(
                key,
                body.prompt,
                {"model": model, "baseURL": base_url},
            )
            title = (title or "").strip()
            if not title:
                title = _fallback_session_title_from_prompt(body.prompt)
                if title:
                    logger.info("[ai-generate-title] zai used prompt fallback")
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
    run_binding_lifecycle=None,
) -> AsyncIterator[dict[str, Any]]:
    """Run independently and map live updates while an SSE peer is attached."""

    from application.agent_composition import get_agent_composition
    from application.agent_run_service import AgentRunService
    from application.sse_mapping import core_update_to_sse_chunk

    composition = get_agent_composition()
    service_stream = AgentRunService(composition).run(
        body=body,
        api_key=api_key,
        provider_options=provider_options,
        signal=signal,
        provenance=provenance,
        run_binding_lifecycle=run_binding_lifecycle,
    )
    model = str(provider_options.get("model") or "")
    queue: asyncio.Queue[dict[str, Any] | Exception | object] = asyncio.Queue()
    stream_end = object()
    subscriber_attached = True

    async def _execute_run() -> None:
        nonlocal subscriber_attached
        try:
            async for update in service_stream:
                if isinstance(update, AgentRunResult) and update.run_id:
                    from infrastructure.persistence.run_conversation_store import (
                        ensure_terminal_run_conversation,
                    )

                    database = getattr(composition, "database", None)
                    if database is not None:
                        await ensure_terminal_run_conversation(
                            database,
                            update.run_id,
                        )
                chunk = core_update_to_sse_chunk(update, model=model)
                if subscriber_attached and chunk is not None:
                    await queue.put(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if run_binding_lifecycle is not None:
                await run_binding_lifecycle.on_start_failed(
                    "request_start_failed",
                )
            if subscriber_attached:
                await queue.put(error)
            else:
                logger.exception("Detached composed Agent failed")
        finally:
            await service_stream.aclose()
            if subscriber_attached:
                await queue.put(stream_end)

    execution_task = asyncio.create_task(_execute_run())
    track_background_run = getattr(composition, "track_background_run", None)
    if callable(track_background_run):
        track_background_run(execution_task)
    try:
        while True:
            item = await queue.get()
            if item is stream_end:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        # Closing the response only detaches this subscriber. The composition
        # owns execution_task until the durable Run reaches a terminal state.
        subscriber_attached = False


@router.post("/ai/chat/stream")
async def chat_stream(
    body: ChatStreamRequest,
):
    if body.requestReceiptVersion == 1:
        # Enhanced requests fail closed before any legacy SSE error branch or
        # Agent composition work. Their immutable receipt cannot be bypassed
        # by clearing mode/session/stream/key/model on the POST replay.
        _validate_durable_writing_request(body)
        if not str(body.streamId or "").strip():
            raise HTTPException(status_code=400, detail="Durable requestId is required")
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
    run_binding_lifecycle = None
    if body.chatAgentMode == "agent" and body.sessionId is not None and body.streamId:
        from application.writing_chat_request_lifecycle import (
            WritingChatRequestLifecycle,
        )
        from dependencies import get_db
        from infrastructure.persistence.writing_chat_request_store import (
            SqliteWritingChatRequestStore,
            WritingChatRequestConflictError,
        )

        store = SqliteWritingChatRequestStore(get_db())
        receipt = await store.get(body.streamId)
        if receipt is not None:
            try:
                receipt, claimed = await store.claim(
                    request_id=body.streamId,
                    session_id=int(body.sessionId),
                    request_digest=_writing_chat_request_digest(body),
                    book_id=body.bookId,
                    chapter_id=body.chapterId,
                    expected_conversation_ids=body.expectedConversationIds,
                    expected_run_ids=body.expectedRunIds,
                )
            except WritingChatRequestConflictError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if not claimed:
                async def _receipt_only():
                    terminal = receipt.status in {"canceled", "rejected"}
                    yield json.dumps({
                        "requestReceipt": receipt.to_public_dict(),
                        **(
                            {
                                "done": True,
                                "requestResult": receipt.to_public_dict(),
                                "finalResponseExpected": False,
                                **(
                                    {"aborted": True}
                                    if receipt.status == "canceled"
                                    else {
                                        "error": (
                                            receipt.rejection_code
                                            or "Writing Agent 请求未启动"
                                        )
                                    }
                                ),
                            }
                            if terminal else {}
                        ),
                    })
                return EventSourceResponse(
                    _receipt_only(),
                    media_type="text/event-stream",
                )
            run_binding_lifecycle = WritingChatRequestLifecycle(
                store,
                body.streamId,
            )
        elif body.requestReceiptVersion == 1:
            raise HTTPException(
                status_code=409,
                detail="Writing chat request was not durably reserved",
            )

    # EventSourceResponse is the sole ASGI ``receive`` owner. Transport close
    # only detaches delivery; explicit control-plane cancellation owns the Run.
    transport_closed = asyncio.Event()
    run_signal = asyncio.Event()
    stream_cleanup_complete = asyncio.Event()

    async def _on_client_disconnect(_message: dict[str, Any]) -> None:
        transport_closed.set()

    async def _event_generator():
        binding_receipt_emitted = False
        composed_stream = _stream_composed_agent(
            body=body,
            api_key=key,
            provider_options=request_params,
            signal=run_signal,
            run_binding_lifecycle=run_binding_lifecycle,
        )
        try:
            async for composed_chunk in composed_stream:
                if (
                    run_binding_lifecycle is not None
                    and not binding_receipt_emitted
                ):
                    receipt = await run_binding_lifecycle.current_receipt()
                    if receipt is not None and receipt.run_id is not None:
                        yield json.dumps({
                            "requestReceipt": receipt.to_public_dict(),
                        })
                        binding_receipt_emitted = True
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
            if run_binding_lifecycle is not None:
                receipt = await run_binding_lifecycle.on_start_failed(
                    "request_start_failed",
                )
                if receipt.run_id is None:
                    yield json.dumps({
                        "done": True,
                        "requestResult": receipt.to_public_dict(),
                        "finalResponseExpected": False,
                        **(
                            {"aborted": True}
                            if receipt.status == "canceled"
                            else {
                                "error": (
                                    receipt.rejection_code
                                    or "Writing Agent 请求未启动"
                                )
                            }
                        ),
                    })
                    return
                # A bound Run is authoritative and may continue detached. EOF
                # makes the renderer recover its journal; never synthesize a
                # transport error as a business terminal.
                logger.exception(
                    "[ai/chat/stream] detached from bound Agent Run %s",
                    receipt.run_id,
                )
                return
            logger.exception("[ai/chat/stream] composed Agent failed")
            yield json.dumps({
                "error": "Agent 运行过程中发生异常，已安全停止；请稍后重试。",
            })
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await composed_stream.aclose()
                finally:
                    if run_binding_lifecycle is None:
                        transport_closed.set()
                    stream_cleanup_complete.set()

    eager_payloads: asyncio.Queue[str | object] | None = None
    eager_end = object()
    if run_binding_lifecycle is not None:
        # Claim ownership before ASGI writes response.start. If that send is
        # the first place a dead peer is observed, the product request still
        # has a live execution owner which will bind a Run or terminalize the
        # receipt; it cannot remain orphaned in `starting` until restart.
        eager_payloads = asyncio.Queue()

        async def _pump_owned_request() -> None:
            try:
                async for payload in _event_generator():
                    if not transport_closed.is_set():
                        await eager_payloads.put(payload)
            finally:
                await eager_payloads.put(eager_end)

        asyncio.create_task(_pump_owned_request())
        await asyncio.sleep(0)

    async def _transport_event_generator():
        """Drain cancellation events without writing after disconnect."""

        if eager_payloads is not None:
            try:
                while True:
                    payload = await eager_payloads.get()
                    if payload is eager_end:
                        return
                    if not transport_closed.is_set():
                        yield payload
            finally:
                transport_closed.set()
            return

        source = _event_generator()
        try:
            async for payload in source:
                if not transport_closed.is_set():
                    yield payload
        finally:
            with anyio.CancelScope(shield=True):
                await source.aclose()

    return _AgentEventSourceResponse(
        _transport_event_generator(),
        abort=transport_closed,
        cleanup_complete=stream_cleanup_complete,
        media_type="text/event-stream",
        client_close_handler_callable=_on_client_disconnect,
    )

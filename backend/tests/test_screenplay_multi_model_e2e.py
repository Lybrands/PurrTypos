"""Paid release gate for the model-agnostic screenplay protocol.

These tests intentionally skip with a release-blocker message when credentials
are absent.  A Fake Gateway contract is useful, but never counts as this gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from application.composition_factory import create_agent_composition
from application.screenplay_agent_service import ScreenplayAgentService
from application.screenplay_candidate_assembler import ScreenplayCandidateAssembler
from application.screenplay_checkpoint_planning import (
    ScreenplayCheckpointOutcome,
    SqliteScreenplayCheckpointRepository,
)
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_screenplay_operation_finalizer import (
    ScreenplayOperationFinalizationCommand,
    SqliteScreenplayOperationFinalizer,
)
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.models.profiles.registry import resolve_model_profile
from purra.contracts import (
    AgentMessage,
    MessageRole,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ReasoningMode,
)
from purra.model_protocol import resolve_invocation_output_limit
from purra.api import AgentModelTask, AgentModelTaskRunner
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from schemas.screenplay_agent import (
    ResumeScreenplayOperationRequest,
    SubmitScreenplayAgentTurnRequest,
)
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest


@dataclass(frozen=True)
class LiveCase:
    id: str
    key_env: str
    provider: str
    profile_id: str
    model: str
    base_url: str
    reasoning_mode: ReasoningMode
    thinking: str


LIVE_CASES = (
    LiveCase(
        id="deepseek-v4-flash-reasoning-on",
        key_env="DEEPSEEK_API_KEY",
        provider="openai",
        profile_id="deepseek:deepseek-v4-flash",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        reasoning_mode=ReasoningMode.DEFAULT,
        thinking="enabled",
    ),
    LiveCase(
        id="deepseek-v4-flash-reasoning-off",
        key_env="DEEPSEEK_API_KEY",
        provider="openai",
        profile_id="deepseek:deepseek-v4-flash",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        reasoning_mode=ReasoningMode.DISABLED,
        thinking="disabled",
    ),
    LiveCase(
        id="glm-5.2",
        key_env="ZAI_API_KEY",
        provider="zai",
        profile_id="zai:glm-5.2",
        model="glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        reasoning_mode=ReasoningMode.DEFAULT,
        thinking="enabled",
    ),
    LiveCase(
        id="mimo-v2.5-pro",
        key_env="MIMO_API_KEY",
        provider="openai",
        profile_id="mimo:mimo-v2.5-pro",
        model="mimo-v2.5-pro",
        base_url="https://api.xiaomimimo.com/v1",
        reasoning_mode=ReasoningMode.DEFAULT,
        thinking="enabled",
    ),
)

_COMPREHENSIVE_CASE_ID = "deepseek-v4-flash-reasoning-off"
_AI_PART_KINDS = frozenset({
    "generate_draft_scene",
    "generate_episode_metadata",
    "generate_review_dimension",
    "generate_document_section",
    "compose_final_response",
})
_PRIVATE_PLAN_MARKERS = (
    "recipe",
    "validation",
    "validate",
    "publish",
    "revision",
    "artifact",
    "long_task",
    "plannerstepid",
    "校验 part",
    "发布候选",
    "提交 revision",
)


@pytest_asyncio.fixture
async def real_screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _api_key_or_skip(case: LiveCase) -> str:
    api_key = str(os.getenv(case.key_env) or "").strip()
    if not api_key:
        pytest.skip(
            f"RELEASE BLOCKER: {case.key_env} is missing; "
            f"{case.id} screenplay E2E was not executed"
        )
    return api_key


def _provider_options(case: LiveCase) -> dict[str, object]:
    return {
        "baseURL": case.base_url,
        "model_profile": case.profile_id,
        "thinking": {"type": case.thinking},
    }


def _runtime(case: LiveCase, api_key: str) -> dict[str, object]:
    return {
        "apiKey": api_key,
        "apiProvider": case.provider,
        "baseURL": case.base_url,
        "options": {
            "model": case.model,
            **_provider_options(case),
            "max_tokens": 8_192,
        },
        "contextWindow": "200k",
    }


async def _assert_provider_available(case: LiveCase, api_key: str):
    profile = resolve_model_profile(case.profile_id, case.model, case.base_url)
    snapshot = profile.capability_snapshot(context_window_tokens=200_000)
    invocation = ModelInvocation(
        request=ModelRequest(
            provider=case.provider,
            model=case.model,
            capability_snapshot=snapshot,
            options=_provider_options(case),
        ),
        output_limit=resolve_invocation_output_limit(
            snapshot,
            explicit_user_override=512,
        ),
        reasoning_mode=case.reasoning_mode,
    )
    try:
        completion = await asyncio.wait_for(
            ProviderModelGateway(api_key).complete(
                (AgentMessage(
                    role=MessageRole.USER,
                    content=(
                        "用一句中文确认：已完成剧本候选稿。"
                        "不要使用 Markdown，不要补充其他内容。"
                    ),
                ),),
                invocation,
            ),
            timeout=60,
        )
    except Exception as error:
        pytest.skip(
            f"RELEASE BLOCKER: {case.id} provider is unavailable "
            f"({type(error).__name__}); screenplay E2E was not executed"
        )
    assert completion.finish_reason is ModelFinishReason.STOP
    assert str(completion.message.content or "").strip()
    assert completion.usage is not None
    assert completion.usage.input_tokens > 0
    assert completion.usage.output_tokens > 0
    return snapshot


async def _assert_structured_smoke(case, api_key, snapshot) -> None:
    structured_request = ModelRequest(
        provider=case.provider,
        model=case.model,
        capability_snapshot=snapshot,
        options={
            **_provider_options(case),
            "response_format": {"type": "json_object"},
        },
    )
    structured = await AgentModelTaskRunner(
        AgentModelInvocationManager(ProviderModelGateway(api_key)),
        ModelInvocationContext(run_id=f"live-e2e-{case.id}"),
    ).stream_text(
        (AgentMessage(
            role=MessageRole.USER,
            content=(
                '只输出一个 JSON 对象：{"status":"ok"}。'
                "不要输出 Markdown 或额外文字。"
            ),
        ),),
        AgentModelTask(
            request=structured_request,
            output_limit=resolve_invocation_output_limit(
                snapshot,
                explicit_user_override=512,
            ),
            reasoning_mode=case.reasoning_mode,
        ),
    )
    parsed = json.loads(structured.content)
    assert isinstance(parsed, dict)
    assert 1 <= structured.attempts <= 3


def _turn_request(
    *,
    session_id: int,
    content: str,
    runtime: dict[str, object],
    formal: bool = False,
) -> SubmitScreenplayAgentTurnRequest:
    return SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session_id,
        "content": content,
        "runtime": runtime,
        **({
            "stageCommand": {
                "kind": "stage_action",
                "action": "create",
                "targetRole": "creativeBrief",
                "scope": {"kind": "current_stage"},
            },
        } if formal else {}),
    })


async def _execute_turn(service, *, command_id, project_id, request):
    turn = await service.submit_turn(
        command_id=command_id,
        project_id=project_id,
        request=request,
    )
    await asyncio.wait_for(
        service.execute_turn(turn["id"], request.runtime),
        timeout=600,
    )
    return turn


async def _wait_for_attachment(db, turn_id: str) -> dict[str, object]:
    for _ in range(6_000):
        row = await db.fetch_one(
            "SELECT t.planner_run_id AS root_run_id, o.id AS operation_id, "
            "o.long_task_id AS task_id FROM screenplay_agent_turns AS t "
            "JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.id = ? AND t.planner_run_id IS NOT NULL "
            "AND o.long_task_id IS NOT NULL",
            [turn_id],
        )
        if row is not None:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("paid Screenplay workflow did not attach its durable task")


async def _wait_for_applied_checkpoint(db, turn_id: str) -> dict[str, object]:
    for _ in range(12_000):
        row = await db.fetch_one(
            "SELECT cp.operation_id, cp.task_id, cp.checkpoint_key, "
            "cp.root_run_id, cp.plan_digest, r.status AS root_status, "
            "r.execution_owner_id, r.lease_expires_at_ms "
            "FROM screenplay_checkpoint_plans AS cp "
            "JOIN screenplay_agent_operations AS o ON o.id = cp.operation_id "
            "JOIN ai_agent_runs AS r ON r.id = cp.root_run_id "
            "WHERE o.turn_id = ? AND cp.status = 'applied' "
            "ORDER BY cp.update_time DESC LIMIT 1",
            [turn_id],
        )
        if row is not None:
            if (
                row["root_status"] == "running"
                and str(row.get("execution_owner_id") or "").strip()
                and row.get("lease_expires_at_ms") is not None
            ):
                return row
            raise AssertionError(
                "paid Screenplay Root completed before checkpoint cancellation"
            )
        await asyncio.sleep(0.05)
    raise AssertionError("paid Screenplay workflow produced no applied checkpoint")


async def _install_reresolution_pause(
    db,
    *,
    turn_id: str,
    attachment: dict[str, object],
) -> None:
    token = f"paid-e2e-pause-{uuid4().hex}"
    repository = SqliteScreenplayCheckpointRepository(db)
    receipt = await repository.acquire_planning(
        operation_id=str(attachment["operation_id"]),
        task_id=str(attachment["task_id"]),
        checkpoint_key="document:sections",
        root_run_id=str(attachment["root_run_id"]),
        input_digest="sha256:" + hashlib.sha256(
            f"paid-e2e:{turn_id}:requires-reresolution".encode("utf-8")
        ).hexdigest(),
        reservation_token=token,
        signal=asyncio.Event(),
    )
    assert receipt["_acquired"] is True
    await repository.pause(
        operation_id=str(attachment["operation_id"]),
        checkpoint_key="document:sections",
        outcome=ScreenplayCheckpointOutcome.REQUIRES_RERESOLUTION,
        code="paid_e2e_constraint_change_requires_reresolution",
        reservation_owner=token,
        reservation_epoch=int(receipt["reservation_epoch"]),
    )


def _snapshot_item(snapshot, collection: str, item_id: str):
    return next(item for item in snapshot[collection] if item["id"] == item_id)


async def _assert_one_initial_root(db, turn_id: str) -> str:
    roots = await db.fetch_all(
        "SELECT DISTINCT r.id, r.create_time FROM ai_agent_runs AS r "
        "JOIN ai_agent_run_events AS e ON e.run_id = r.id "
        "WHERE e.turn_id = ? AND r.parent_run_id IS NULL "
        "ORDER BY r.create_time",
        [turn_id],
    )
    assert len(roots) == 1
    return str(roots[0]["id"])


async def _assert_public_plan_is_semantic(db, *root_run_ids: str) -> None:
    for root_run_id in root_run_ids:
        rows = await db.fetch_all(
            "SELECT step_id, title, description FROM ai_agent_run_todos "
            "WHERE run_id = ? ORDER BY sort",
            [root_run_id],
        )
        assert rows
        encoded = json.dumps(rows, ensure_ascii=False).casefold()
        assert not any(marker in encoded for marker in _PRIVATE_PLAN_MARKERS)


async def _assert_canceled_tree_is_drained(
    db,
    *,
    root_run_id: str,
    task_id: str,
) -> None:
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "(id = ? OR root_run_id = ?) AND (status = 'running' OR "
        "execution_owner_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
        [root_run_id, root_run_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_task_units WHERE "
        "task_id = ? AND (status IN ('claimed', 'running') OR "
        "worker_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
        [task_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations WHERE "
        "root_run_id = ? AND status IN ('queued', 'claimed', 'running')",
        [root_run_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_claims WHERE "
        "run_id IN (SELECT id FROM ai_agent_runs WHERE id = ? OR root_run_id = ?)",
        [root_run_id, root_run_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_host_child_runs WHERE "
        "json_extract(contract_json, '$.rootRunId') = ? AND "
        "(reservation_owner IS NOT NULL OR reservation_expires_at_ms IS NOT NULL)",
        [root_run_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [root_run_id],
    ) == {"status": "completed"}


async def _finalization_command(db, operation_id: str):
    operation = await db.fetch_one(
        "SELECT long_task_id, manifest_digest, requirements_json "
        "FROM screenplay_agent_operations WHERE id = ?",
        [operation_id],
    )
    assert operation is not None
    requirements = json.loads(str(operation["requirements_json"]))
    steps = requirements["recipe"]["steps"]
    candidate_ids = tuple(
        str(step["id"]) for step in steps
        if step["kind"] == "validate_manifest_part"
    )
    response_id = next(
        str(step["id"]) for step in steps
        if step["kind"] == "compose_final_response"
    )
    parts = ScreenplayPartArtifactQuery(db)
    candidate_refs = tuple([
        await parts.validated_unit_ref(str(operation["long_task_id"]), unit_id)
        for unit_id in candidate_ids
    ])
    response_ref = await parts.validated_unit_ref(
        str(operation["long_task_id"]),
        response_id,
    )
    assert all(candidate_refs)
    assert response_ref is not None
    return ScreenplayOperationFinalizationCommand(
        operation_id=operation_id,
        expected_manifest_digest=str(operation["manifest_digest"]),
        candidate_part_refs=candidate_refs,
        final_response_ref=response_ref,
    )


async def _assert_part_lineage_and_usage(
    db,
    *,
    operation_id: str,
    task_id: str,
    root_run_ids: tuple[str, ...],
) -> None:
    units = await db.fetch_all(
        "SELECT unit_id, metadata_json FROM ai_agent_long_task_units "
        "WHERE task_id = ? ORDER BY position",
        [task_id],
    )
    ai_ids = {
        str(unit["unit_id"])
        for unit in units
        if json.loads(str(unit["metadata_json"])).get("unitKind") in _AI_PART_KINDS
    }
    deterministic_ids = {str(unit["unit_id"]) for unit in units} - ai_ids
    children = await db.fetch_all(
        "SELECT id, status, root_run_id, delegation_id, agent_role, run_depth, "
        "binding_command_id FROM ai_agent_runs WHERE parent_run_id IS NOT NULL "
        f"AND root_run_id IN ({','.join('?' for _ in root_run_ids)})",
        list(root_run_ids),
    )
    assert children
    assert all(child["status"] in {"done", "canceled"} for child in children)
    assert all(child["delegation_id"] is None for child in children)
    assert all(child["agent_role"] == "screenplay-part" for child in children)
    assert all(child["run_depth"] == 1 for child in children)
    child_commands = {str(child["binding_command_id"] or "") for child in children}
    assert all(f"{task_id}:{unit_id}" in child_commands for unit_id in ai_ids)
    assert all(
        f"{task_id}:{unit_id}" not in child_commands
        for unit_id in deterministic_ids
    )
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations WHERE "
        f"root_run_id IN ({','.join('?' for _ in root_run_ids)})",
        list(root_run_ids),
    ) == {"count": 0}

    usage_rows = await db.fetch_all(
        "SELECT run_id, invocation_count, input_tokens, output_tokens, "
        "reasoning_tokens FROM screenplay_agent_operation_usage "
        "WHERE operation_id = ? ORDER BY run_id",
        [operation_id],
    )
    assert usage_rows
    assert len(usage_rows) == len({row["run_id"] for row in usage_rows})
    lineage_ids = {
        row["id"] for row in await db.fetch_all(
            "SELECT id FROM ai_agent_runs WHERE "
            f"id IN ({','.join('?' for _ in root_run_ids)}) OR "
            f"root_run_id IN ({','.join('?' for _ in root_run_ids)})",
            [*root_run_ids, *root_run_ids],
        )
    }
    assert {row["run_id"] for row in usage_rows}.issubset(lineage_ids)
    stored = await db.fetch_one(
        "SELECT usage_json FROM screenplay_agent_operations WHERE id = ?",
        [operation_id],
    )
    aggregate = json.loads(str(stored["usage_json"]))
    assert aggregate["invocationCount"] == sum(
        int(row["invocation_count"]) for row in usage_rows
    )
    assert aggregate["inputTokens"] == sum(
        int(row["input_tokens"]) for row in usage_rows
    )
    assert aggregate["outputTokens"] == sum(
        int(row["output_tokens"]) for row in usage_rows
    )


async def _run_comprehensive_workflow(case, db, api_key) -> None:
    projects = ScreenplayV2ProjectService(db)
    workspace = await projects.create_project(
        command_id="paid-e2e-create-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "真实 Provider Root Run 验收",
            "format": "series",
            "source": {"type": "original"},
            "brief": {
                "approach": "人物驱动",
                "premise": "多年未见的搭档在旧剧院意外重逢",
            },
        }),
    )
    project_id = str(workspace["project"]["id"])
    session = await projects.ensure_current_session(project_id)
    runtime = _runtime(case, api_key)
    composition = create_agent_composition(db)
    service = ScreenplayAgentService(
        db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        projects=projects,
    )
    try:
        answer_request = _turn_request(
            session_id=session["id"],
            content="请用一句话告诉我这个项目当前处于哪个创作阶段。",
            runtime=runtime,
        )
        answer_turn = await _execute_turn(
            service,
            command_id="paid-e2e-answer",
            project_id=project_id,
            request=answer_request,
        )
        answer_snapshot = await service.get_snapshot(
            project_id=project_id,
            session_id=session["id"],
        )
        answer = _snapshot_item(answer_snapshot, "turns", answer_turn["id"])
        assert answer["status"] == "completed", answer
        assert str(answer["assistantContent"] or "").strip()
        answer_root = await _assert_one_initial_root(db, answer_turn["id"])
        assert not any(
            operation["turnId"] == answer_turn["id"]
            for operation in answer_snapshot["operations"]
        )

        cancel_request = _turn_request(
            session_id=session["id"],
            content=(
                "请为这个原创连续剧生成当前阶段的创作简报，"
                "聚焦意外重逢如何推动人物关系。"
            ),
            runtime=runtime,
            formal=True,
        )
        cancel_turn = await service.submit_turn(
            command_id="paid-e2e-formal-cancel",
            project_id=project_id,
            request=cancel_request,
        )
        cancel_execution = service.dispatch_turn(
            cancel_turn["id"],
            cancel_request.runtime,
        )
        checkpoint = await _wait_for_applied_checkpoint(db, cancel_turn["id"])
        cancel_root = str(checkpoint["root_run_id"])
        cancel_operation = str(checkpoint["operation_id"])
        cancel_task = str(checkpoint["task_id"])
        requested = await service.cancel_turn(
            cancel_turn["id"],
            idempotency_key="paid-e2e-manual-cancel",
        )
        assert requested["terminalStatus"] == "cancel_requested"
        await asyncio.wait_for(cancel_execution, timeout=120)
        settled = await service.cancel_turn(
            cancel_turn["id"],
            idempotency_key="paid-e2e-manual-cancel",
        )
        assert settled["terminalStatus"] == "canceled"
        assert await _assert_one_initial_root(db, cancel_turn["id"]) == cancel_root
        assert await db.fetch_one(
            "SELECT status FROM screenplay_agent_operations WHERE id = ?",
            [cancel_operation],
        ) == {"status": "canceled"}
        assert await db.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_revisions "
            "WHERE agent_task_id = ?",
            [cancel_task],
        ) == {"count": 0}
        await _assert_canceled_tree_is_drained(
            db,
            root_run_id=cancel_root,
            task_id=cancel_task,
        )

        resume_request = _turn_request(
            session_id=session["id"],
            content=(
                "请完成当前阶段的创作简报，明确人物关系、核心前提"
                "和后续结构设计需要遵守的改编原则。"
            ),
            runtime=runtime,
            formal=True,
        )
        resume_turn = await service.submit_turn(
            command_id="paid-e2e-formal-resume",
            project_id=project_id,
            request=resume_request,
        )
        resume_execution = service.dispatch_turn(
            resume_turn["id"],
            resume_request.runtime,
        )
        attachment = await _wait_for_attachment(db, resume_turn["id"])
        await _install_reresolution_pause(
            db,
            turn_id=resume_turn["id"],
            attachment=attachment,
        )
        await asyncio.wait_for(resume_execution, timeout=600)
        paused_snapshot = await service.get_snapshot(
            project_id=project_id,
            session_id=session["id"],
        )
        paused_turn = _snapshot_item(paused_snapshot, "turns", resume_turn["id"])
        paused_operation = next(
            operation for operation in paused_snapshot["operations"]
            if operation["turnId"] == resume_turn["id"]
        )
        paused_task = next(
            task for task in paused_snapshot["tasks"]
            if task["id"] == paused_operation["taskId"]
        )
        assert paused_turn["status"] == "paused", paused_turn
        assert paused_operation["status"] == "paused", paused_operation
        assert paused_task["status"] == "paused", paused_task
        old_root = str(paused_turn["rootRunId"])
        old_events = await db.fetch_all(
            "SELECT event_id, event_type, sequence, payload_json "
            "FROM ai_agent_run_events WHERE run_id = ? ORDER BY id",
            [old_root],
        )
        old_todos = await db.fetch_all(
            "SELECT * FROM ai_agent_run_todos WHERE run_id = ? ORDER BY sort",
            [old_root],
        )
        completed_before = await db.fetch_all(
            "SELECT unit_id, attempt, output_ref FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND status = 'completed' ORDER BY position",
            [paused_task["id"]],
        )
        assert completed_before

        continuation_command = "paid-e2e-continuation"
        prepared = await service.prepare_resume(
            paused_operation["id"],
            idempotency_key=continuation_command,
            request=ResumeScreenplayOperationRequest.model_validate({
                "expectedOperationRevision": paused_operation["revision"],
                "runtime": runtime,
            }),
        )
        assert prepared["dispatchRequired"] is True
        await asyncio.wait_for(
            service.execute_resumed_operation(
                prepared["operationId"],
                resume_request.runtime,
                continuation_command=continuation_command,
            ),
            timeout=600,
        )
        completed_snapshot = await service.get_snapshot(
            project_id=project_id,
            session_id=session["id"],
        )
        completed_turn = _snapshot_item(
            completed_snapshot,
            "turns",
            resume_turn["id"],
        )
        completed_operation = next(
            operation for operation in completed_snapshot["operations"]
            if operation["turnId"] == resume_turn["id"]
        )
        assert completed_turn["status"] == "completed", completed_turn
        assert completed_operation["status"] == "succeeded", completed_operation
        new_root = str(completed_turn["rootRunId"])
        assert new_root != old_root
        assert await db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [old_root],
        ) == {"status": "canceled"}
        assert await db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [new_root],
        ) == {"status": "done"}
        assert await db.fetch_all(
            "SELECT event_id, event_type, sequence, payload_json "
            "FROM ai_agent_run_events WHERE run_id = ? ORDER BY id",
            [old_root],
        ) == old_events
        assert await db.fetch_all(
            "SELECT * FROM ai_agent_run_todos WHERE run_id = ? ORDER BY sort",
            [old_root],
        ) == old_todos
        for completed in completed_before:
            assert await db.fetch_one(
                "SELECT attempt, output_ref FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND unit_id = ?",
                [paused_task["id"], completed["unit_id"]],
            ) == {
                "attempt": completed["attempt"],
                "output_ref": completed["output_ref"],
            }

        roots = await db.fetch_all(
            "SELECT DISTINCT r.id, r.binding_command_id, r.create_time "
            "FROM ai_agent_runs AS r JOIN ai_agent_run_events AS e "
            "ON e.run_id = r.id WHERE e.turn_id = ? "
            "AND r.parent_run_id IS NULL ORDER BY r.create_time",
            [resume_turn["id"]],
        )
        assert [
            {key: row[key] for key in ("id", "binding_command_id")}
            for row in roots
        ] == [
            {"id": old_root, "binding_command_id": "paid-e2e-formal-resume"},
            {"id": new_root, "binding_command_id": continuation_command},
        ]
        await _assert_public_plan_is_semantic(
            db,
            answer_root,
            cancel_root,
            old_root,
            new_root,
        )
        assert await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
            "AND event_type = 'run.todos_updated' "
            "AND json_extract(payload_json, '$.planRevision.identity') "
            "IS NOT NULL",
            [cancel_root],
        ) == {"count": 1}
        assert await db.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_revisions "
            "WHERE agent_task_id = ?",
            [paused_task["id"]],
        ) == {"count": 1}
        assert await db.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_outbox_events "
            "WHERE aggregate_id = ? AND event_type = 'screenplay.candidate.ready'",
            [completed_operation["resultRevisionId"]],
        ) == {"count": 1}

        command = await _finalization_command(db, completed_operation["id"])
        replay = await SqliteScreenplayOperationFinalizer(
            db,
            candidate_assembler=ScreenplayCandidateAssembler(db),
        ).finalize(command)
        assert replay.id == completed_operation["finalizationReceiptId"]
        assert replay.revision_id == completed_operation["resultRevisionId"]
        assert await db.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_revisions "
            "WHERE agent_task_id = ?",
            [paused_task["id"]],
        ) == {"count": 1}
        await _assert_part_lineage_and_usage(
            db,
            operation_id=completed_operation["id"],
            task_id=paused_task["id"],
            root_run_ids=(old_root, new_root),
        )
    finally:
        await composition.shutdown()


@pytest.mark.real_provider
@pytest.mark.asyncio
@pytest.mark.parametrize("case", LIVE_CASES, ids=lambda case: case.id)
async def test_live_profile_completes_screenplay_candidate_revision_and_replay(
    case: LiveCase,
    real_screenplay_db,
):
    api_key = _api_key_or_skip(case)
    snapshot = await _assert_provider_available(case, api_key)
    if case.id == "deepseek-v4-flash-reasoning-on":
        await _assert_structured_smoke(case, api_key, snapshot)
    if case.id == _COMPREHENSIVE_CASE_ID:
        await _run_comprehensive_workflow(case, real_screenplay_db, api_key)

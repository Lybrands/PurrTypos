# Screenplay Intent Recovery and Stage Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent reasoning-only structured model calls from drifting into JSON repair and preserve formal screenplay stage-button intent as a persisted, model-constrained command.

**Architecture:** PurrA will own one provider-neutral managed text finalization path for finish classification, reasoning replay, and bounded empty-response recovery. The screenplay Application will only parse and repair non-empty JSON candidates, while a new persisted `stageCommand` constrains Planner output and the Turn commit boundary; ordinary free text and the `处理审阅意见` conversation shortcut remain unconstrained conversations.

**Tech Stack:** Python 3.12, PurrA contracts/runtime, FastAPI/Pydantic, SQLite, React 18, TypeScript, Node test runner, pytest.

## Global Constraints

- Do not parse, publish, or commit model reasoning as business output.
- Do not restore long canned stage prompts or expose `stageCommand` JSON in the conversation UI.
- Only a non-empty content candidate may enter JSON repair.
- Keep the user-selected thinking mode unchanged across physical attempts.
- Formal stage commands cannot use `answer`; ordinary free text may still resolve to `answer`.
- `处理审阅意见` remains an immediate ordinary Agent conversation shortcut and creates no forced Operation.
- Do not infer commands from Chinese labels in the backend.
- PurrA must remain screenplay-agnostic; screenplay action, role, scope, Project, and Operation semantics stay outside Core.
- Add no dependency and no second Agent Runtime.
- Preserve existing truncation, cancellation, lease, usage, Artifact, Revision, and idempotency behavior.
- Use TDD for each task and commit only the files owned by that task.

---

## File Structure

- `packages/purra/src/purra/recovery/guidance.py`: shared provider-neutral empty-response retry guidance.
- `packages/purra/src/purra/model_execution.py`: managed no-tool text accumulation, terminal classification, bounded empty-response recovery, and public result contract.
- `packages/purra/src/purra/runtime/orchestrator.py`: consume the shared empty-response guidance without changing the full Runtime state machine.
- `backend/application/screenplay_structured_call.py`: project chunks, parse non-empty JSON, and perform at most one candidate repair.
- `backend/application/model_runtime.py`: declare `json_object` as the actual structured-output capability requirement.
- `backend/domains/screenplay_agent/contracts.py`: immutable `ScreenplayStageCommand` and command/Intent compatibility invariant.
- `backend/schemas/screenplay_agent.py`: optional public `stageCommand` request contract.
- `backend/database/screenplay_agent_schema.py`: nullable `stage_command_json` migration.
- `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py`: persist, compare, reload, and project the command with each Turn.
- `backend/application/screenplay_agent_planner.py`: pass the required command to the model and validate initial/repaired Intent against it.
- `backend/application/screenplay_agent_service.py`: preserve the command through planning and reject incompatible completion before answer/Operation settlement.
- `src/types.ts`: shared renderer request, Turn, and `ScreenplayStageCommand` types.
- `src/services/backendApi.ts`: serialize optional `stageCommand` with the existing Turn request.
- `src/ScreenplayAgentPage/stageAgentAction.ts`: produce a short label plus an optional typed formal command from Workspace state.
- `src/ScreenplayAgentPage/index.tsx`: carry commands through direct and queued submission without attaching them to edited/free-text messages.
- Existing PurrA, screenplay backend, route, schema, durable-service, frontend, and live-provider tests: protect every boundary and the real incident sequence.

---

### Task 1: Add Core-Owned Managed Text Empty-Response Recovery

**Files:**

- Create: `packages/purra/src/purra/recovery/guidance.py`
- Modify: `packages/purra/src/purra/recovery/__init__.py`
- Modify: `packages/purra/src/purra/model_execution.py`
- Modify: `packages/purra/src/purra/runtime/orchestrator.py:180-186,1573-1629`
- Test: `packages/purra/tests/test_model_execution.py`
- Test: `backend/tests/test_purra_runtime.py:410-469`

**Interfaces:**

- Consumes: `ManagedModelCall`, `ModelStreamChunk`, `RecoveryLedger`, `RecoveryPolicy`, `RecoveryCause.EMPTY_MODEL_RESPONSE`, `ReasoningReplayPolicy`, and the existing validated stream termination.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class ManagedModelTextResult:
    content: str
    reasoning: str
    finish_reason: ModelFinishReason
    usage: ModelTokenUsage | None
    attempts: int

ManagedChunkObserver = Callable[[ModelStreamChunk], Awaitable[None]]

async def ManagedModelExecutor.stream_text(
    self,
    messages: Sequence[AgentMessage],
    call: ManagedModelCall,
    signal: CancellationSignal | None = None,
    *,
    on_attempt: Callable[[Mapping[str, object]], Awaitable[None]] | None = None,
    on_chunk: ManagedChunkObserver | None = None,
    recovery_policy: RecoveryPolicy = RecoveryPolicy(),
) -> ManagedModelTextResult
```

- Produces shared `EMPTY_RESPONSE_RETRY_GUIDANCE` exported from `purra.recovery`; it contains no screenplay, JSON, tool name, or product terminology.

- [ ] **Step 1: Write failing managed-text recovery tests**

Extend `packages/purra/tests/test_model_execution.py` with a scripted gateway that records message rounds and emits separate physical streams:

```python
def test_stream_text_retries_reasoning_only_without_changing_mode():
    reasoning_only = [
        ModelStreamChunk(reasoning_delta='{"answer":"hidden"}'),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]
    visible = [
        ModelStreamChunk(content_delta='{"answer":"visible"}'),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]

    async def run():
        gateway = _ScriptedStreamGateway([reasoning_only, visible])
        chunks = []
        result = await ManagedModelExecutor(gateway).stream_text(
            (AgentMessage(role='user', content='return JSON'),),
            _call(),
            on_chunk=lambda chunk: _append(chunks, chunk),
        )
        assert result.content == '{"answer":"visible"}'
        assert result.reasoning == ''
        assert result.attempts == 2
        assert [item.reasoning_mode for item in gateway.invocations] == [
            ReasoningMode.DISABLED,
            ReasoningMode.DISABLED,
        ]
        assert gateway.message_rounds[1][-2].reasoning == '{"answer":"hidden"}'
        assert 'reasoning alone' in gateway.message_rounds[1][-1].content

    asyncio.run(run())
```

Add three more exact cases:

```python
def test_stream_text_rejects_repeated_reasoning_only_as_empty_model_response(): ...
def test_stream_text_rejects_completely_empty_round_with_the_same_budget(): ...
def test_stream_text_never_retries_length_termination(): ...
```

The repeated-empty cases assert three physical invocations under the standard policy and `ModelGatewayError.code == 'empty_model_response'`. The length case asserts one invocation and `model_output_truncated`.

- [ ] **Step 2: Run the focused Core tests and confirm RED**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  ../packages/purra/tests/test_model_execution.py -q
```

Expected: FAIL because `ManagedModelExecutor.stream_text` and `ManagedModelTextResult` do not exist.

- [ ] **Step 3: Extract shared empty-response guidance**

Create `purra/recovery/guidance.py`:

```python
EMPTY_RESPONSE_RETRY_GUIDANCE = (
    "Your preceding model round ended after internal reasoning without any "
    "official response content. Continue the same request now and return the "
    "complete response through the requested output protocol. Do not return "
    "reasoning alone."
)

__all__ = ["EMPTY_RESPONSE_RETRY_GUIDANCE"]
```

Re-export it from `purra.recovery.__init__` and replace the private constant in `runtime/orchestrator.py` with the shared import. Do not change the Runtime retry branch, budget, trace names, or test expectations beyond the guidance substring.

- [ ] **Step 4: Implement managed stream accumulation and bounded recovery**

In `model_execution.py`, add the result dataclass and `stream_text()`. Each physical attempt must call the existing `stream()` method so output-limit, capability, cancellation, tool-call rejection, and finish classification stay single-sourced.

Use a fresh `RecoveryLedger(recovery_policy)` per logical `stream_text()` call. Accumulate one physical attempt, notify `on_chunk`, and return only when `content.strip()` is non-empty:

```python
if content.strip():
    return ManagedModelTextResult(
        content=content,
        reasoning=reasoning,
        finish_reason=finish_reason,
        usage=usage,
        attempts=attempt,
    )

decision = ledger.decide(RecoveryRequest(
    cause=RecoveryCause.EMPTY_MODEL_RESPONSE,
    action=RecoveryAction.RETRY_MODEL,
    remaining_model_rounds=(
        recovery_policy.max_attempts(RecoveryCause.EMPTY_MODEL_RESPONSE)
        - ledger.attempts(RecoveryCause.EMPTY_MODEL_RESPONSE)
        + 1
    ),
))
if not decision.allowed:
    raise ModelGatewayError(
        "model returned no official response content",
        code="empty_model_response",
        retryable=False,
    )
active_messages = (*messages, AgentMessage(
    role=MessageRole.ASSISTANT,
    content="",
    reasoning=reasoning or None,
), AgentMessage(
    role=MessageRole.DEVELOPER,
    content=EMPTY_RESPONSE_RETRY_GUIDANCE,
))
```

Track the terminal `finish_reason` from the chunks and fail defensively if it is absent, although `_validated_chunks()` already rejects that stream. Keep the last physical attempt's `usage` in the result; all attempt usage remains observable through `on_chunk`.

- [ ] **Step 5: Run Core tests and the existing Runtime reasoning-only regressions**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  ../packages/purra/tests/test_model_execution.py \
  tests/test_purra_runtime.py::test_runtime_retries_reasoning_only_round_and_returns_visible_answer \
  tests/test_purra_runtime.py::test_runtime_rejects_repeated_reasoning_only_responses \
  tests/test_purra_runtime.py::test_reasoning_only_truncation_fails_without_replaying_request \
  -q
```

Expected: PASS. The full Runtime still owns its tool-aware loop, and both paths share the empty-response cause, budget, and guidance.

- [ ] **Step 6: Commit the Core recovery boundary**

```bash
git add \
  packages/purra/src/purra/recovery/guidance.py \
  packages/purra/src/purra/recovery/__init__.py \
  packages/purra/src/purra/model_execution.py \
  packages/purra/src/purra/runtime/orchestrator.py \
  packages/purra/tests/test_model_execution.py \
  backend/tests/test_purra_runtime.py
git commit -m "fix(purra): unify managed empty response recovery"
```

---

### Task 2: Separate Structured Candidate Repair from Model Recovery

**Files:**

- Modify: `backend/application/screenplay_structured_call.py:45-365`
- Modify: `backend/application/model_runtime.py:28-70`
- Test: `backend/tests/test_screenplay_agent_rewrite.py:835-1360`
- Test: `backend/tests/test_purra_model_protocol.py`
- Test: `backend/tests/test_agent_refactor_boundaries.py`

**Interfaces:**

- Consumes: `ManagedModelExecutor.stream_text()` and `ManagedModelTextResult` from Task 1.
- Produces: one explicit original call and at most one repair call; repair accepts only the previous non-empty candidate.
- Produces: `StructuredChunkProjection.observe(chunk: ModelStreamChunk) -> Awaitable[None]` and `close() -> Awaitable[None]`; it projects diagnostics, usage, and optional public JSON fields but does not own model recovery or business text accumulation.
- Produces: `model_request_from_runtime(..., json_object_output=True)` with `TaskCapabilityRequirements.structured_output_level == 'json_object'`.

- [ ] **Step 1: Replace the incomplete reasoning-only test with exact failing state-transition tests**

In `test_screenplay_agent_rewrite.py`, replace the monkeypatch that returns empty content for both calls with gateways that exercise physical attempts:

```python
async def test_reasoning_only_structured_output_retries_original_not_repair(...):
    gateway = _ScriptedGateway([
        _reasoning_only('{"answer":"private"}'),
        _content('{"answer":"ok"}'),
    ])
    result = await service.run_json(...)
    assert result.value == {"answer": "ok"}
    assert [event["phase"] for event in _model_call_events(db)] == [
        "screenplay_test",
        "screenplay_test",
    ]
    assert not any(event["phase"].endswith("_repair") for event in events)
```

Add:

```python
async def test_empty_structured_output_never_sends_an_empty_repair_candidate(...): ...
async def test_non_empty_invalid_json_still_repairs_once(...): ...
async def test_repair_that_is_still_invalid_fails_as_structured_output_invalid(...): ...
```

The empty test asserts `empty_model_response`, three original-phase attempts, zero repair phases, and no `delta` output. The malformed test asserts the second call's user content equals the exact malformed non-empty candidate.

Extend the JSON-mode test:

```python
assert structured.protocol_capabilities.json_schema_level == "json_object"
```

and monkeypatch `preflight_capabilities` to assert the received requirements use `structured_output_level == 'json_object'`.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_agent_rewrite.py \
  tests/test_purra_model_protocol.py -q
```

Expected: the new reasoning-only test observes `_repair`, and the capability test observes `structured_output_level='none'`.

- [ ] **Step 3: Replace `_stream_text` with projection-only state**

Move progress buffering, `reasoningDelta`, `modelContentDelta`, and `CONTEXT_USAGE_RECORDED` emission into `StructuredChunkProjection`. Its observer follows this shape:

```python
async def observe(self, chunk: ModelStreamChunk) -> None:
    if chunk.reasoning_delta:
        await self._emit_model_diagnostic({
            "reasoningDelta": chunk.reasoning_delta,
        })
    if chunk.content_delta:
        await self._emit_model_diagnostic({
            "modelContentDelta": chunk.content_delta,
        })
        await self._project_structured(chunk.content_delta)
    if chunk.usage is not None:
        await self._controller.record_event(
            CoreEventType.CONTEXT_USAGE_RECORDED,
            _usage_payload(chunk.usage),
        )
```

`close()` flushes the public progress buffer. Delete `StreamedModelText`; Core now owns content/reasoning accumulation.

- [ ] **Step 4: Implement the explicit JSON call/repair flow**

Create a local `call(active_messages, call_phase)` helper that builds a fresh projection, invokes `model_executor.stream_text(...)`, always closes the projection, and returns `ManagedModelTextResult.content`.

Use one original call, then parse and validate:

```python
candidate = await call(messages, phase)
try:
    value = _parse_and_validate(candidate, validate)
except (TypeError, ValueError, json.JSONDecodeError) as first_error:
    if not candidate.strip():
        raise RuntimeError("Core returned an empty structured candidate")
    repaired = await call(
        _repair_messages(system_instruction, repair_instruction, candidate),
        f"{phase}_repair",
    )
    try:
        value = _parse_and_validate(repaired, validate)
    except (TypeError, ValueError, json.JSONDecodeError) as repair_error:
        raise ModelGatewayError(
            str(repair_error) or str(first_error),
            code="structured_output_invalid",
            retryable=False,
        ) from repair_error
```

Do not catch `ModelGatewayError` from `stream_text`; empty, truncation, interruption, and capability failures retain their original codes. Keep the existing success projection and `controller.complete()` behavior.

- [ ] **Step 5: Make JSON output an actual capability requirement**

In `model_request_from_runtime`, derive the default requirement from `json_object_output`:

```python
structured_output_level = "json_object" if json_object_output else "none"
preflight_capabilities(
    snapshot,
    requirements or TaskCapabilityRequirements(
        reasoning_mode=reasoning_mode,
        tool_calling=FeatureRequirement.OPTIONAL,
        structured_output_level=structured_output_level,
        streaming_required=True,
        cancellation_required=True,
    ),
)
```

Only attach `response_format={"type":"json_object"}` after this preflight succeeds.

- [ ] **Step 6: Add the architecture ratchet and run focused GREEN checks**

In `test_agent_refactor_boundaries.py`, assert `screenplay_structured_call.py` does not import `RecoveryLedger`, `RecoveryCause`, or `EMPTY_MODEL_RESPONSE`; Application may consume `ManagedModelExecutor.stream_text` but cannot implement the Core recovery budget.

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_agent_rewrite.py \
  tests/test_purra_model_protocol.py \
  tests/test_agent_refactor_boundaries.py -q
```

Expected: PASS; malformed non-empty JSON still repairs once, while empty/reasoning-only never enters repair.

- [ ] **Step 7: Commit the structured-call separation**

```bash
git add \
  backend/application/screenplay_structured_call.py \
  backend/application/model_runtime.py \
  backend/tests/test_screenplay_agent_rewrite.py \
  backend/tests/test_purra_model_protocol.py \
  backend/tests/test_agent_refactor_boundaries.py
git commit -m "fix(screenplay): separate model recovery from JSON repair"
```

---

### Task 3: Persist a Typed Formal Stage Command with the Turn

**Files:**

- Modify: `backend/domains/screenplay_agent/contracts.py:1-155`
- Modify: `backend/domains/screenplay_agent/__init__.py`
- Modify: `backend/schemas/screenplay_agent.py:1-55`
- Modify: `backend/database/screenplay_agent_schema.py:110-158`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py:20-85,580-620,778-800`
- Modify: `backend/application/screenplay_agent_service.py:80-130`
- Test: `backend/tests/test_screenplay_v2_schema.py`
- Test: `backend/tests/test_screenplay_agent_routes.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`

**Interfaces:**

- Produces:

```python
class ScreenplayIntentCommandMismatchError(ValueError):
    code = "screenplay_intent_command_mismatch"

@dataclass(frozen=True, slots=True)
class ScreenplayStageCommand:
    action: ScreenplayIntentAction
    target_role: str
    scope: ScreenplayIntentScope
    kind: str = "stage_action"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayStageCommand": ...
    def to_mapping(self) -> dict[str, Any]: ...
    def require_compatible(self, intent: ScreenplayIntent) -> None: ...
```

- Produces public request field `stageCommand: ScreenplayStageCommandRequest | None` and Turn projection field `stageCommand: dict | None`.
- Changes `SqliteScreenplayAgentRepository.begin_turn(..., stage_command: Mapping[str, Any] | None)`; every call site must pass the explicit value.

- [ ] **Step 1: Write failing domain compatibility tests**

Add pure tests to `test_screenplay_agent_rewrite.py`:

```python
def test_stage_command_accepts_only_the_same_action_role_and_scope():
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    })
    command.require_compatible(ScreenplayIntent(
        action=ScreenplayIntentAction.REVIEW,
        instruction="审阅当前完整剧本",
        requested_deliverable="review",
    ))
    with pytest.raises(
        ScreenplayIntentCommandMismatchError,
        match="stage command",
    ):
        command.require_compatible(ScreenplayIntent(
            action=ScreenplayIntentAction.ANSWER,
            instruction="说明没有 JSON",
            reply="没有 JSON",
        ))
```

Parametrize mismatches for action, targetRole, next-episode count, and explicit episode numbers. Add construction tests rejecting `answer`, duplicate/non-positive episode numbers, and `review` paired with a non-review target.

- [ ] **Step 2: Write failing schema, persistence, and route tests**

In `test_screenplay_v2_schema.py`, initialize a database that predates the new column, reopen it, and assert `stage_command_json` exists and existing rows project null.

In `test_screenplay_agent_routes.py`, keep the test rejecting the obsolete top-level `operation` field and add:

```python
async def test_turn_endpoint_accepts_a_typed_stage_command(monkeypatch):
    response = await _post(app, _body(stageCommand={
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    }))
    assert response.status_code == 202
    assert service.submissions[0]["request"].stageCommand.targetRole == "review"
```

Add route cases rejecting `action='answer'`, invalid role/action pairs, and scope fields incompatible with their kind.

Add repository tests proving the same idempotency key plus a different stage command returns 409, and Snapshot returns the persisted command.

- [ ] **Step 3: Run focused tests and confirm RED**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_v2_schema.py \
  tests/test_screenplay_agent_routes.py \
  tests/test_screenplay_agent_rewrite.py -q
```

Expected: FAIL because the domain type, request field, database column, and repository parameter do not exist.

- [ ] **Step 4: Implement the domain command and compatibility invariant**

In `contracts.py`, normalize the command through existing `ScreenplayIntentAction`, `ScreenplayIntentScope`, and `SCREENPLAY_DELIVERABLE_ROLES`. Enforce these exact pairs:

```python
if action is ScreenplayIntentAction.ANSWER:
    raise ValueError("stage command cannot be answer")
if action is ScreenplayIntentAction.REVIEW and target_role != "review":
    raise ValueError("review stage command requires review target")
if action is not ScreenplayIntentAction.REVIEW and target_role == "review":
    raise ValueError("review target requires review action")
```

`require_compatible()` compares action, requested deliverable, `scope.kind`, `count`, and `episode_numbers` exactly and raises `ScreenplayIntentCommandMismatchError` on any difference.

- [ ] **Step 5: Add the wire model and nullable migration**

Define nested Pydantic models in `schemas/screenplay_agent.py` with `extra='forbid'` inherited from `ScreenplayV2Model`. Reuse the same enum literals and conditional scope validation as the domain contract. Add:

```python
stageCommand: ScreenplayStageCommandRequest | None = None
```

Add `stage_command_json TEXT DEFAULT NULL` to the table definition and idempotent column loop in `screenplay_agent_schema.py`. Do not backfill old rows from `user_content`.

- [ ] **Step 6: Persist and compare the command atomically**

Change `begin_turn()` to serialize the optional mapping in the same INSERT as `user_content`. On idempotent replay compare canonical parsed mappings in addition to session and content:

```python
existing_command = _object(existing.get("stage_command_json")) or None
requested_command = dict(stage_command) if stage_command is not None else None
if (
    int(existing["session_id"]) != int(session_id)
    or str(existing["user_content"]) != content
    or existing_command != requested_command
):
    raise AppError("同一个对话命令对应了不同请求", 409)
```

Return `stageCommand` from `_turn_view()`. In `ScreenplayAgentService.submit_turn()`, convert `request.stageCommand` with `model_dump(mode='json')` and pass it to the repository.

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_v2_schema.py \
  tests/test_screenplay_agent_routes.py \
  tests/test_screenplay_agent_rewrite.py -q
```

Expected: PASS, including old rows, idempotent replay, and invalid request rejection.

Commit:

```bash
git add \
  backend/domains/screenplay_agent/contracts.py \
  backend/domains/screenplay_agent/__init__.py \
  backend/schemas/screenplay_agent.py \
  backend/database/screenplay_agent_schema.py \
  backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py \
  backend/application/screenplay_agent_service.py \
  backend/tests/test_screenplay_v2_schema.py \
  backend/tests/test_screenplay_agent_routes.py \
  backend/tests/test_screenplay_agent_rewrite.py
git commit -m "feat(screenplay): persist formal stage commands"
```

---

### Task 4: Constrain Planner Output and Guard Turn Settlement

**Files:**

- Modify: `backend/application/screenplay_agent_service.py:80-335,570-605,760-780`
- Modify: `backend/application/screenplay_agent_planner.py:25-105,287-293`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `backend/tests/test_screenplay_agent_durable_service.py`

**Interfaces:**

- Changes `ScreenplayIntentPlanner.plan(..., stage_command: ScreenplayStageCommand | None) -> PlannedScreenplayIntent`.
- Produces `_validate_intent(value, stage_command) -> dict[str, Any]` that validates both initial and repaired candidates.
- Produces Service settlement invariant: only a Turn without `stageCommand` may call `complete_answer()`.

- [ ] **Step 1: Write failing Planner compatibility tests**

In `test_screenplay_agent_rewrite.py`, add a Planner gateway whose first non-empty content is a schema-valid answer despite a review command:

```python
async def test_planner_repairs_schema_valid_intent_that_violates_stage_command(...):
    gateway = _ScriptedGateway([
        _content(_answer_intent("没有待修复 JSON")),
        _content(_review_intent("审阅当前完整剧本")),
    ])
    planned = await planner.plan(
        workspace=workspace,
        history=(),
        user_content="开始审阅",
        stage_command=ScreenplayStageCommand.from_mapping(REVIEW_COMMAND),
        runtime=runtime,
        session_id=session_id,
        turn_id="turn-review-command",
    )
    assert planned.intent.action is ScreenplayIntentAction.REVIEW
    assert len(gateway.invocations) == 2
```

Add a second case where repair returns another answer and assert `ScreenplayIntentCommandMismatchError` rather than a completed answer.

Assert `requiredStageCommand` is present in the first Planner user payload but absent when `stage_command=None`.

- [ ] **Step 2: Write a failing Service defense-in-depth test**

In `test_screenplay_agent_durable_service.py`, use the fake `_Planner` to bypass model validation and deliberately return answer for a persisted review command:

```python
async def test_formal_review_command_can_never_complete_as_answer(screenplay_db):
    request = _request(..., stageCommand=REVIEW_COMMAND)
    service = ScreenplayAgentService(..., planner=_Planner(answer_intent), ...)
    turn = await service.submit_turn(...)
    await service.execute_turn(turn["id"], request.runtime)
    snapshot = await service.get_snapshot(...)
    assert snapshot["turns"][0]["status"] == "failed"
    assert snapshot["turns"][0]["error"]["code"] == (
        "screenplay_intent_command_mismatch"
    )
    assert snapshot["turns"][0]["assistantContent"] == ""
    assert snapshot["operations"] == []
    assert snapshot["tasks"] == []
```

Keep and rerun the existing free-text answer test to prove ordinary consultations still complete without an Operation.

- [ ] **Step 3: Run focused tests and confirm RED**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_agent_rewrite.py \
  tests/test_screenplay_agent_durable_service.py \
  -q
```

Expected: answer-for-review currently completes and the Planner protocol has no command parameter.

- [ ] **Step 4: Pass the persisted command through Planner validation**

In `execute_turn()`, reconstruct once:

```python
stage_command = (
    ScreenplayStageCommand.from_mapping(turn["stageCommand"])
    if turn.get("stageCommand") is not None
    else None
)
```

Pass it to `planner.plan()`. In `ModelScreenplayIntentPlanner.plan()`, include `requiredStageCommand` only when non-null and bind the validator:

```python
def validate(value: dict[str, Any]) -> dict[str, Any]:
    intent = ScreenplayIntent.from_mapping(value)
    if stage_command is not None:
        stage_command.require_compatible(intent)
    return intent.to_mapping()
```

The same callable is already used after original parsing and repair, so repair cannot change the command identity.

- [ ] **Step 5: Add the Service settlement guard and stable error mapping**

Immediately after Planner returns and before `record_intent()`:

```python
if stage_command is not None:
    stage_command.require_compatible(planned.intent)
```

This protects fake/custom Planner implementations and future regressions. Change `_settle_execution_exception()` so a `ScreenplayIntentCommandMismatchError` is persisted with its own `.code`; other non-model planning failures remain `screenplay_intent_failed`.

Do not create an Operation before this check. Do not persist the model's incompatible `reply` as Assistant content.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_agent_rewrite.py \
  tests/test_screenplay_agent_durable_service.py -q
```

Expected: command-constrained Planner and Service tests pass; the existing ordinary answer test remains green.

Commit:

```bash
git add \
  backend/application/screenplay_agent_planner.py \
  backend/application/screenplay_agent_service.py \
  backend/tests/test_screenplay_agent_rewrite.py \
  backend/tests/test_screenplay_agent_durable_service.py
git commit -m "fix(screenplay): enforce formal intent commands"
```

---

### Task 5: Send Typed Commands from Formal Stage Buttons

**Files:**

- Modify: `src/types.ts:250-390,1604-1610`
- Modify: `src/services/backendApi.ts:105-113`
- Modify: `src/ScreenplayAgentPage/stageAgentAction.ts`
- Modify: `src/ScreenplayAgentPage/stageAgentAction.test.ts`
- Modify: `src/ScreenplayAgentPage/index.tsx:160-175,2129-2255,2835-2930`
- Test: `src/ScreenplayAgentPage/stageAgentAction.test.ts`
- Test: `src/ScreenplayAgentPage/conversationState.test.ts`

**Interfaces:**

- Produces:

```ts
export interface ScreenplayStageCommand {
  kind: 'stage_action'
  action: 'create' | 'revise' | 'review'
  targetRole: ScreenplayV2DeliverableRole
  scope: {
    kind: 'current_stage' | 'next_episodes' | 'episodes' | 'all_remaining'
    count?: number
    episodeNumbers?: number[]
  }
}

export interface StageAgentAction {
  label: string
  stageCommand?: ScreenplayStageCommand
}

export function stageAgentAction(input: StageAgentActionInput): StageAgentAction
```

- Changes `runAgent(promptOverride?, editMessageIndex?, runtimeOverride?, stageCommand?)` and `ScreenplayQueuedSubmission.stageCommand?: ScreenplayStageCommand`.
- Turn submit request and snapshot Turn gain optional/null `stageCommand`.

- [ ] **Step 1: Convert stage-action tests to assert label and command together**

Replace string-only expectations with exact objects. Include at least these mappings:

```ts
{
  input: { project: project({ active_stage: 'review' }), reviewState: awaiting },
  expected: {
    label: '开始审阅',
    stageCommand: {
      kind: 'stage_action',
      action: 'review',
      targetRole: 'review',
      scope: { kind: 'current_stage' },
    },
  },
},
{
  input: { project: project({ active_stage: 'review' }), reviewState: adjudicating },
  expected: { label: '处理审阅意见' },
},
{
  input: { project: project({ active_stage: 'review' }), reviewState: readyToRevise },
  expected: {
    label: '开始修订',
    stageCommand: {
      kind: 'stage_action',
      action: 'revise',
      targetRole: 'screenplayDraft',
      scope: { kind: 'current_stage' },
    },
  },
},
{
  input: {
    project: project({ active_stage: 'draft', format: '连续剧' }),
    draftScope: 'next_3_episodes',
  },
  expected: {
    label: '连续创作 3 集',
    stageCommand: {
      kind: 'stage_action',
      action: 'create',
      targetRole: 'screenplayDraft',
      scope: { kind: 'next_episodes', count: 3 },
    },
  },
}
```

Also cover source analysis, brief, structure, scene list, next one episode, all remaining, rerun review, and completed. Preserve the assertion that labels do not contain canned instructions.

- [ ] **Step 2: Run the focused frontend test and confirm RED**

Run:

```bash
node --experimental-strip-types --test \
  src/ScreenplayAgentPage/stageAgentAction.test.ts
```

Expected: FAIL because `stageAgentAction()` still returns a string.

- [ ] **Step 3: Add shared TypeScript contracts and HTTP serialization**

Add `ScreenplayStageCommand`, include `stageCommand: ScreenplayStageCommand | null` in `ScreenplayConversationTurn`, and add optional `stageCommand` to `submitScreenplayConversationTurn` input.

In `backendApi.ts`, serialize it only when present:

```ts
{
  sessionId: data.sessionId,
  content: data.content,
  runtime: data.runtime,
  ...(data.stageCommand ? { stageCommand: data.stageCommand } : {}),
}
```

Do not serialize `stageCommand: undefined` for free text.

- [ ] **Step 4: Return label and typed command from one pure mapping**

Change `stageAgentAction()` to return one object so label and command cannot drift. Use these formal mappings:

```text
orientation/book -> create/sourceAnalysis/current_stage
orientation/original or brief -> create/creativeBrief/current_stage
structure -> create/structure/current_stage
scenes -> create/sceneList/current_stage
draft next one -> create/screenplayDraft/next_episodes count=1
draft next N -> create/screenplayDraft/next_episodes count=N
draft all remaining -> create/screenplayDraft/all_remaining
review awaiting or rerun -> review/review/current_stage
review readyToRevise -> revise/screenplayDraft/current_stage
review adjudicating -> no stageCommand; keep ordinary `处理审阅意见`
project active_stage completed -> no stageCommand; keep `创作已完成`
```

Separately, if the project is still in `draft` and the existing exhausted-draft branch emits `完成剧本正文`, map it to `review/review/current_stage` because the authoritative accepted screenplayDraft is complete and the next formal deliverable is review. Add a focused test for this branch rather than relying on the label. This does not change the `active_stage === 'completed'` branch above.

- [ ] **Step 5: Carry the command through direct and queued submission**

Update `primaryStageAction` consumers to render `.label`. Call:

```ts
runAgent(
  primaryStageAction.label,
  undefined,
  undefined,
  primaryStageAction.stageCommand,
)
```

For batch actions, derive the full `StageAgentAction` from `draftScope` and pass its label and command together; do not pass `action.label` separately from its scope.

Add `stageCommand` to queued submissions and replay it when draining. Composer submission and edited historical messages call `runAgent()` without a command. `处理审阅意见` also calls `runAgent('处理审阅意见')` without a command, preserving the existing shortcut.

- [ ] **Step 6: Run focused tests and typecheck**

Run:

```bash
node --experimental-strip-types --test \
  src/ScreenplayAgentPage/stageAgentAction.test.ts \
  src/ScreenplayAgentPage/conversationState.test.ts
npm run typecheck
```

Expected: PASS; concise labels remain unchanged, formal commands are exact, and ordinary shortcuts/free text omit the command.

- [ ] **Step 7: Commit the renderer contract**

```bash
git add \
  src/types.ts \
  src/services/backendApi.ts \
  src/ScreenplayAgentPage/stageAgentAction.ts \
  src/ScreenplayAgentPage/stageAgentAction.test.ts \
  src/ScreenplayAgentPage/index.tsx \
  src/ScreenplayAgentPage/conversationState.test.ts
git commit -m "feat(screenplay): submit typed stage commands"
```

---

### Task 6: Replay the Incident, Update Contracts, and Run Release Gates

**Files:**

- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`
- Modify: `backend/tests/test_screenplay_multi_model_e2e.py`
- Modify: `docs/design/screenplay-agent-api-v2.md:175-216`
- Modify: `docs/design/2026-08-10-screenplay-agent-refactor-handoff.md:173-178`
- Modify: `docs/superpowers/specs/2026-08-11-remove-canned-stage-prompts-design.md`
- Modify: `docs/superpowers/specs/2026-08-11-screenplay-intent-recovery-and-stage-command-design.md`

**Interfaces:**

- Consumes: all previous tasks.
- Produces: one deterministic regression reproducing the exact `review JSON in reasoning -> empty content -> wrong answer repair` incident, one credential-gated DeepSeek structured-output exercise, and updated public API/design contracts.

- [ ] **Step 1: Add the complete deterministic incident replay**

Build a real `ModelScreenplayIntentPlanner` with a scripted gateway and a persisted review `stageCommand`. The physical streams are:

```python
first = [
    ModelStreamChunk(reasoning_delta=json.dumps({
        "action": "review",
        "instruction": "审阅当前完整剧本",
        "scope": {"kind": "current_stage"},
        "constraints": [],
        "preserve": [],
        "requestedDeliverable": "review",
        "reply": None,
    }, ensure_ascii=False)),
    ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
]
second = [
    ModelStreamChunk(content_delta=json.dumps({
        "action": "review",
        "instruction": "审阅当前完整剧本",
        "scope": {"kind": "current_stage"},
        "constraints": [],
        "preserve": [],
        "requestedDeliverable": "review",
        "reply": None,
    }, ensure_ascii=False)),
    ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
]
```

Execute the Turn with the existing fake Resolver/UnitExecutor fixture. Assert:

```python
assert turn["status"] == "completed"
assert operation["target_role"] == "review"
assert operation["status"] == "succeeded"
assert task is not None
assert not any(event_phase.endswith("_repair") for event_phase in phases)
assert "未收到需要修复的候选 JSON" not in turn["assistant_content"]
```

Add the negative replay where every physical attempt is reasoning-only. It must fail with `empty_model_response`, with zero Operation/Task/Revision rows.

- [ ] **Step 2: Add the credential-gated live structured call**

Extend the DeepSeek reasoning-on case in `test_screenplay_multi_model_e2e.py` to use `ManagedModelExecutor.stream_text()` with `response_format={"type":"json_object"}` and a small JSON-only prompt. Assert returned `content` parses as an object and `attempts` is between 1 and the standard bounded maximum.

Do not assert that the provider must produce reasoning-only; both a first-attempt content response and a recovered second-attempt response are valid. Keep the existing missing-key release-blocker skip text.

- [ ] **Step 3: Update the authoritative API and design documents**

In `screenplay-agent-api-v2.md`, replace the stale top-level `operation` example with a formal `stageCommand` example and state that Operation is created server-side only after command-compatible planning and Resolver validation.

In the handoff, clarify “same intent system” as:

```text
按钮与自由文本进入同一 Planner；正式按钮携带宿主不可变的 action/target/scope 边界，模型补全 instruction，但不能把正式命令降级为 answer。
```

Mark the old “不引入新的结构化按钮命令协议” line in the canned-prompt spec as superseded by the confirmed intent-recovery design. Set the new design status to `已实施` only after all gates pass.

- [ ] **Step 4: Run focused incident and acceptance tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_screenplay_agent_rewrite.py \
  tests/test_screenplay_agent_durable_service.py \
  tests/test_screenplay_agent_routes.py \
  tests/test_screenplay_v2_schema.py -q
cd .. && npm run test:screenplay-acceptance
```

Expected: all deterministic incident, route, schema, durable-service, and screenplay acceptance tests pass.

- [ ] **Step 5: Run Core, frontend, and architecture gates**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  ../packages/purra/tests/test_model_execution.py \
  tests/test_purra_runtime.py \
  tests/test_purra_model_protocol.py -q
cd .. && npm run typecheck
npm run test:unit
npm run check:agent-refactor-boundaries
git diff --check
```

Expected: all checks pass with no architecture-boundary growth or whitespace errors.

- [ ] **Step 6: Run the complete Agent gate**

Run:

```bash
npm run check:agent-refactor
```

Expected: boundary, model-contract, screenplay-acceptance, TypeScript, frontend-unit, and complete backend suites pass.

- [ ] **Step 7: Run the paid real-provider gate when credentials are available**

Run:

```bash
npm run test:screenplay-real-e2e
```

Expected: configured providers pass. Missing keys may skip only with the existing explicit `RELEASE BLOCKER` message; report those skips as unexecuted live coverage, not as a pass.

- [ ] **Step 8: Commit incident coverage and contract documentation**

```bash
git add \
  backend/tests/test_screenplay_agent_rewrite.py \
  backend/tests/test_screenplay_agent_durable_service.py \
  backend/tests/test_screenplay_multi_model_e2e.py \
  docs/design/screenplay-agent-api-v2.md \
  docs/design/2026-08-10-screenplay-agent-refactor-handoff.md \
  docs/superpowers/specs/2026-08-11-remove-canned-stage-prompts-design.md \
  docs/superpowers/specs/2026-08-11-screenplay-intent-recovery-and-stage-command-design.md
git commit -m "test(screenplay): replay reasoning-only intent incident"
```

- [ ] **Step 9: Verify final branch state**

```bash
git status --short
git log --oneline -8
```

Expected: clean worktree and the planned task commits in order. Do not push unless the user explicitly requests it.

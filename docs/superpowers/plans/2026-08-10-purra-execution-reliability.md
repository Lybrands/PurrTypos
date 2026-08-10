# PurrA Execution Reliability Implementation Plan（已失效）

> 状态：本计划基于已被否定的预算与截断恢复假设，禁止执行。待用户复核 `../specs/2026-08-11-purra-model-agnostic-long-task-execution-design.md` 后重新编写实施计划。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent recoverable model, tool, and protocol failures from terminating an entire durable screenplay task while preserving the user's reasoning-mode choice, completed work, and single final-answer semantics.

**Architecture:** Keep PurrA provider- and product-neutral. Add a typed failure-disposition boundary shared by Run recovery and durable Units, persist recoverable `blocked` state instead of amplifying it to task failure, and make model protocol capabilities explicit. The screenplay application compiles bounded evidence/generation/validation/publication steps and owns the product final answer; PurrA owns only generic execution and recovery facts.

**Tech Stack:** Python 3.12, FastAPI application services, SQLite repositories, PurrA Python package, pytest, React/TypeScript, Vitest, SSE snapshot/event projection.

## Global Constraints

- Do not add model-name or Provider-name branches to PurrA.
- Do not create a second production Runtime or restore the removed screenplay fallback.
- The main Run's requested reasoning mode is immutable; no silent enabled-to-disabled fallback.
- A failed Run is an immutable attempt fact, not automatically a failed Operation.
- Completed Units, output references, source receipts, Artifacts, and Revisions are never reset during recovery.
- Production code changes require a failing test first.
- Final assistant content is published once, after Operation finalization succeeds.
- Real-provider completion claims require both capability families, an injected interruption, replay verification, and shutdown of every test service started by this work.

---

### Task 1: Generic failure-disposition contract

**Files:**
- Modify: `packages/purra/src/purra/recovery/contracts.py`
- Create: `packages/purra/src/purra/recovery/disposition.py`
- Modify: `packages/purra/src/purra/recovery/__init__.py`
- Test: `backend/tests/test_purra_recovery.py`

**Interfaces:**
- Produces: `FailureCategory`, `FailureDisposition`, `FailureSignal`, `FailureDecision`, and `decide_failure(signal, *, attempts_remaining)`.
- Consumes: existing `RecoveryEffectState` and cancellation/effect facts; never consumes model or Provider names.

- [ ] **Step 1: Write failing contract tests**

```python
def test_retryable_failure_with_checkpoint_resumes_current_unit():
    decision = decide_failure(
        FailureSignal(
            category=FailureCategory.MODEL_OUTPUT_INVALID,
            code="tool_call_truncated",
            retryable=True,
            effect_state=RecoveryEffectState.NOT_STARTED,
            checkpoint_available=True,
        ),
        attempts_remaining=1,
    )
    assert decision.disposition is FailureDisposition.RESUME_CHECKPOINT


def test_retryable_failure_without_budget_pauses_instead_of_failing():
    decision = decide_failure(
        FailureSignal(
            category=FailureCategory.MODEL_OUTPUT_INVALID,
            code="max_model_rounds",
            retryable=True,
            effect_state=RecoveryEffectState.NOT_STARTED,
        ),
        attempts_remaining=0,
    )
    assert decision.disposition is FailureDisposition.PAUSE_RECOVERABLE


def test_unknown_effect_pauses_without_retry():
    decision = decide_failure(
        FailureSignal(
            category=FailureCategory.TOOL_EXECUTION,
            code="tool_execution_failed",
            retryable=True,
            effect_state=RecoveryEffectState.UNKNOWN,
        ),
        attempts_remaining=3,
    )
    assert decision.disposition is FailureDisposition.PAUSE_RECOVERABLE
```

- [ ] **Step 2: Run RED test**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_recovery.py -q`

Expected: import failures for the new disposition contracts.

- [ ] **Step 3: Implement minimal pure decision function**

Apply this precedence: cancellation; permanent category; unsafe/unknown effect; retry or checkpoint resume with budget; recoverable pause when budget is exhausted.

- [ ] **Step 4: Run GREEN regression**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_recovery.py tests/test_purra_runtime.py -q`

Expected: all selected tests pass.

### Task 2: Durable Unit blocked state and non-amplifying settlement

**Files:**
- Modify: `packages/purra/src/purra/long_tasks/contracts.py`
- Modify: `packages/purra/src/purra/long_tasks/ports.py`
- Modify: `packages/purra/src/purra/long_tasks/coordinator.py`
- Modify: `packages/purra/src/purra/long_tasks/dispatcher.py`
- Modify: `backend/infrastructure/persistence/sqlite_long_task_repository.py`
- Modify: `backend/database/schema.py`
- Test: `backend/tests/test_purra_long_tasks.py`
- Test: `backend/tests/test_screenplay_agent_durable_service.py`

**Interfaces:**
- Consumes: `FailureDecision` from Task 1.
- Produces: Unit statuses `WAITING_RETRY` and `BLOCKED`; `LongTaskRepository.settle_unit_failure(..., decision: FailureDecision)`; resumable paused task semantics.

- [ ] **Step 1: Write failing repository tests**

Add three independent tests:

```python
async def test_exhausted_recoverable_unit_pauses_without_canceling_completed_units():
    # Complete unit-1, block unit-2, then assert task=paused,
    # unit-1/output_ref unchanged, unit-2=blocked, failed_units=0.


async def test_resume_only_requeues_blocked_units():
    # Resume with one added attempt and assert completed units are untouched.


async def test_permanent_unit_failure_still_terminalizes_task():
    # FAIL_PERMANENT remains terminal and safely settles dependents.
```

- [ ] **Step 2: Run RED test**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_long_tasks.py tests/test_screenplay_agent_durable_service.py -q`

Expected: missing status/API failures and current task=`failed` behavior.

- [ ] **Step 3: Implement Unit settlement**

Replace boolean `retryable` settlement with `FailureDecision`. Retry/resume uses `waiting_retry`; exhausted recoverable failures use `blocked` and atomically pause the task; permanent failures retain terminal behavior. `claim_ready_unit` may claim pending/waiting Units but never blocked Units.

- [ ] **Step 4: Implement precise resume**

Allow paused tasks to add attempt budget. Reset only blocked or interrupted Units; never modify completed Units or their output refs.

- [ ] **Step 5: Run GREEN regression**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_long_tasks.py tests/test_screenplay_agent_durable_service.py tests/test_purra_sqlite_run_repository.py -q`

Expected: all selected tests pass.

### Task 3: Screenplay failure classification and paused Turn projection

**Files:**
- Modify: `backend/domains/screenplay_agent/recovery.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py`
- Modify: `backend/schemas/screenplay_agent.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `backend/tests/test_screenplay_agent_routes.py`

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces: `classify_screenplay_run_failure(error) -> FailureSignal` and idempotent `pause_task(turn_id, code, message)`.

- [ ] **Step 1: Write failing seven-code classification test**

Parameterize the seven historical error categories. Truncated/invalid/max-round errors are recoverable model-output failures; transient tool/provider errors are retryable; capability/configuration bad requests pause without mode mutation; deterministic business validation may be permanent.

- [ ] **Step 2: Write failing paused projection test**

Make the dispatcher return `LongTaskExecutionStatus.PAUSED`. Assert the service calls `pause_task`, does not call `fail_task`, and writes no formal assistant final.

- [ ] **Step 3: Run RED test**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_screenplay_agent_rewrite.py tests/test_screenplay_agent_routes.py -q`

Expected: missing classification and current paused-to-failed projection.

- [ ] **Step 4: Implement domain classification and paused persistence**

Keep code mapping outside Core. Pass typed failure facts to PurrA and emit a paused snapshot/event without fabricating a final Assistant answer.

- [ ] **Step 5: Run GREEN regression**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_screenplay_agent_rewrite.py tests/test_screenplay_agent_routes.py tests/test_screenplay_agent_durable_service.py -q`

Expected: all selected tests pass.

### Task 4: Immutable reasoning intent and model capabilities

**Files:**
- Create: `packages/purra/src/purra/model_protocol/capabilities.py`
- Modify: `packages/purra/src/purra/model_protocol/__init__.py`
- Modify: `packages/purra/src/purra/contracts/__init__.py`
- Modify: `backend/infrastructure/models/profiles/base.py`
- Modify: `backend/infrastructure/models/profiles/*.py`
- Modify: `backend/application/model_runtime.py`
- Modify: `backend/infrastructure/models/provider_model_gateway.py`
- Modify: `packages/purra/src/purra/runtime/orchestrator.py`
- Modify: `backend/database/schema.py`
- Modify: `backend/infrastructure/persistence/sqlite_run_repository.py`
- Test: `backend/tests/test_purra_contracts.py`
- Test: `backend/tests/test_purra_adapters.py`
- Test: `backend/tests/test_purra_runtime.py`
- Test: `backend/tests/test_purra_sqlite_run_repository.py`

**Interfaces:**
- Produces: `ReasoningControl`, `ReasoningReplayPolicy`, `FeatureSupport`, `ModelProtocolCapabilities`, and `RunExecutionIntent`.
- Consumes: frontend/runtime `thinking.type`; profiles declare capabilities; Core consumes only typed values.

- [ ] **Step 1: Write failing capability/provenance tests**

Assert generic profiles omit unsupported `thinking` extensions, incompatible reasoning selection fails before gateway invocation, and Run provenance persists immutable requested mode plus capability digest.

- [ ] **Step 2: Write failing no-silent-fallback test**

For a reasoning-only truncation, assert every main invocation remains `ReasoningMode.DEFAULT` when the user enabled thinking. Recovery may create a new attempt, but not a disabled main invocation.

- [ ] **Step 3: Run RED test**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_contracts.py tests/test_purra_adapters.py tests/test_purra_runtime.py tests/test_purra_sqlite_run_repository.py -q`

Expected: missing capabilities/provenance and current pinned-disabled behavior.

- [ ] **Step 4: Implement typed capability preflight**

Profiles return typed capabilities. Generic profiles use conservative defaults. Validate user intent before side effects and persist the capability digest.

- [ ] **Step 5: Remove main-Run mode mutation**

Delete the main Runtime's disabled fallback and keep recovery in the requested mode.

- [ ] **Step 6: Run GREEN regression**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_contracts.py tests/test_purra_adapters.py tests/test_purra_runtime.py tests/test_purra_sqlite_run_repository.py tests/test_deepseek_provider.py tests/test_zai_provider.py -q`

Expected: all selected tests pass without model-name branches inside PurrA.

### Task 5: Product final-output boundary

**Files:**
- Modify: `backend/application/screenplay_agent_stream.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/application/sse_mapping.py`
- Modify: `src/ScreenplayAgentPage/conversationState.ts`
- Modify: `src/ScreenplayAgentPage/conversationState.test.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/agentRun.ts`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts`
- Test: `backend/tests/test_ai_composed_sse_wire_contract.py`
- Test: `backend/tests/test_screenplay_agent_routes.py`

**Interfaces:**
- Consumes: Run reasoning/content/tool events and Operation completion.
- Produces: process-only reasoning/commentary/tool projections and exactly one product final after Revision publication.

- [ ] **Step 1: Write failing backend event tests**

Assert child content and Run-level final responses never become a product Assistant final while a task is running or paused; Operation completion emits one reference-bearing final message.

- [ ] **Step 2: Write failing frontend live/replay test**

Feed reasoning-on, public commentary, tools, paused state, resume, and completion through live and replay reducers. Assert identical state and one final message.

- [ ] **Step 3: Run RED tests**

Run: `npm test -- --run src/ScreenplayAgentPage/conversationState.test.ts src/agent-runtime/chunkReplay.test.cjs`

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_ai_composed_sse_wire_contract.py tests/test_screenplay_agent_routes.py -q`

Expected: current intermediate/final projection violates at least one new assertion.

- [ ] **Step 4: Implement the event boundary**

Route reasoning to the thinking region only when enabled, public content/tools to the work log, phase progress to the plan, and formal content only through successful Operation finalization.

- [ ] **Step 5: Run GREEN tests**

Run the same frontend and backend commands. Expected: all pass.

### Task 6: Bounded screenplay recipe

**Files:**
- Modify: `backend/domains/screenplay_agent/recipe_compiler.py`
- Modify: `backend/domains/screenplay_agent/contracts.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/screenplay_tool_calling.py`
- Modify: `backend/infrastructure/screenplay/tools/tool_catalog.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_task_output_store.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `backend/tests/test_screenplay_tool_catalog.py`
- Test: `backend/tests/test_screenplay_agent_durable_service.py`

**Interfaces:**
- Produces screenplay-owned `collect_evidence`, `generate_candidate`, `validate_candidate`, and `publish_candidate` steps using generic PurrA dependencies/effect metadata.
- Consumes: durable checkpoints, candidate Artifacts, source receipts, and dependency output refs.

- [ ] **Step 1: Write failing recipe/tool-visibility tests**

Assert formal generation compiles separate read-only evidence, candidate write, deterministic validation, and publication Units. The generate Unit consumes evidence refs and cannot access READ tools.

- [ ] **Step 2: Write failing resume tests**

Simulate truncation after evidence completion and after candidate Artifact write. Assert retry does not repeat evidence reads; a valid Artifact resumes at validation/publication; source receipts and output refs remain stable.

- [ ] **Step 3: Run RED tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_screenplay_agent_rewrite.py tests/test_screenplay_tool_catalog.py tests/test_screenplay_agent_durable_service.py -q`

Expected: the current coarse formal Unit/open tool loop fails the new assertions.

- [ ] **Step 4: Implement bounded screenplay Units**

Keep stage names and allowlists in the screenplay layer. PurrA receives only generic recipe dependencies, effect classes, and output refs.

- [ ] **Step 5: Remove the obsolete coarse formal path**

Delete the formal generation path that exposes READ and candidate-write tools in the same unrestricted model loop. Keep consultation behavior unless the shared output contract requires a change.

- [ ] **Step 6: Run GREEN tests**

Run the same three suites. Expected: all pass.

### Task 7: Historical incident replay and architecture ratchets

**Files:**
- Create: `backend/tests/fixtures/purra_incidents/2026-08-10-failure-sequences.json`
- Create: `backend/tests/test_purra_incident_replay.py`
- Modify: `backend/tests/test_agent_refactor_boundaries.py`
- Modify: `package.json`
- Modify: `docs/agent-operations-runbook.md`

**Interfaces:**
- Consumes: sanitized event/state sequences from the nine failures; excludes user prose, body payloads, and credentials.
- Produces: deterministic gates for disposition, preserved outputs, immutable reasoning mode, and one finalization.

- [ ] **Step 1: Create sanitized fixtures and failing replay assertions**

Fixture fields are limited to event type, round, error code, effect state, Unit state, attempts, checkpoint/output presence, and expected disposition/task status.

- [ ] **Step 2: Run RED replay**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_incident_replay.py -q`

Expected: pre-change terminal-failure behavior fails.

- [ ] **Step 3: Add architecture ratchets**

Forbid model/provider string checks inside `packages/purra`, main-Run reasoning mutation, and paused durable results flowing into screenplay `fail_task`.

- [ ] **Step 4: Run GREEN replay and architecture tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_purra_incident_replay.py tests/test_agent_refactor_boundaries.py -q`

Expected: all pass.

### Task 8: Full verification and controlled real-provider execution

**Files:**
- Create: `docs/design/purra-execution-reliability-verification.md`
- Modify implementation only if verification first gains a failing regression test.

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: evidence-backed completion report.

- [ ] **Step 1: Run deterministic gates**

```bash
npm run check:agent-refactor
cd backend && ../.venv/bin/python -m pytest -q
cd .. && npm test -- --run
npx tsc --noEmit
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 2: Run two capability-family smoke tests**

Use current configured credentials without logging secrets. Execute one minimal consultation and one formal candidate task with supported reasoning selections. Inject one stream interruption or bounded truncation and resume from checkpoint.

- [ ] **Step 3: Inspect persisted facts**

Verify Run intent/capability digest, failed-attempt history, paused/resumed Unit transitions, unchanged completed output refs, Artifact finalization, source receipts, Revision publication, and one final Assistant message.

- [ ] **Step 4: Stop all started services**

Record PIDs/ports before startup, stop only processes started during verification, and confirm relevant ports have no listeners.

- [ ] **Step 5: Write the verification report**

Record exact commands, pass counts, real Run IDs, injected failure, recovery transition, and unverified external conditions. Do not call the refactor complete if either capability family remains untested.

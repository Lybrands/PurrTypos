# Context Compaction Operation Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the Agent timeline emits a context-compaction Operation only when Core determines compression is required.

**Architecture:** Keep all pre-planning, post-planning, and model-call budget checks intact. Gate only the authoritative Operation lifecycle at `ContextCompressionCoordinator`, while continuing to call the application Hook for summary reuse and diagnostics below the threshold. Preserve atomic child Run attachment and make its exact coordinator confirmation idempotent inside one SQLite transaction so removing false Operation delays cannot break delegation.

**Tech Stack:** Python 3.12, pytest, PurrA context orchestration and Operation contracts.

## Global Constraints

- Do not change compression thresholds, summary policy, context budgeting, or frontend rendering.
- Preserve complete succeeded, failed, and canceled Operation lifecycles for real compression.
- Preserve application Hook execution below the threshold.
- Preserve atomic child Run creation and delegation attachment.
- Add no dependencies or new abstractions.

---

### Task 1: Gate the context-compaction Operation lifecycle

**Files:**
- Modify: `backend/tests/test_conversation_compaction.py`
- Modify: `packages/purra/src/purra/context_orchestration/compaction.py`

**Interfaces:**
- Consumes: `ContextCompressionRequest.compression_required: bool` and `AgentOperationController`.
- Produces: `ContextCompressionCoordinator.prepare()` results with unchanged request and diagnostics contracts, plus Operation events only for real compression.

- [x] **Step 1: Write the failing regression test**

Add a test using a real `AgentOperationController` and an application Hook that records its call. Pass a request with a large context window and assert:

```python
assert result.outcome == "application_no_change"
assert hook.calls == 1
assert output.events == []
```

This test catches the bug where changing or omitting the `compression_required` branch creates a user-visible Operation for a budget check.

- [x] **Step 2: Run the regression test and verify RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_conversation_compaction.py::test_below_threshold_check_does_not_create_compaction_operation -q
```

Expected: FAIL because the current coordinator emits two Operation events.

- [x] **Step 3: Implement the minimum Core fix**

Start the Operation only after Core has determined compression is required:

```python
operation_id = (
    await self._start_compaction_operation(operation_scope, snapshot.phase)
    if compression_required
    else None
)
```

This retains the existing requirement for a valid `OperationScope` whenever a real compression Operation must be started.

- [x] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2. Expected: one passing test.

- [x] **Step 5: Verify existing real-compression lifecycle coverage**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_conversation_compaction.py -q
```

Expected: all context-compaction tests pass, including the existing one-pair Operation lifecycle assertion.

### Task 2: Make exact child attachment confirmation idempotent

**Files:**
- Modify: `backend/tests/test_agent_delegation_service.py`
- Modify: `backend/infrastructure/persistence/delegation_store.py`
- Verify: `backend/tests/test_ai_composed_sse_wire_contract.py`

**Interfaces:**
- Consumes: `attach_child_run(db, delegation_id: str, child_run_id: str, worker_id: str) -> bool`.
- Produces: atomic first attachment, idempotent success for an exact existing running attachment, and rejection of mismatched lineage or ownership.

- [x] **Step 1: Write and run the failing idempotency test**

Create a child Run through `SqliteRunRepository.create()` with delegation lineage, then call `SqliteDelegationRepository.attach_child_run()` with the same identifiers:

```python
assert attached is True
```

Expected RED result: the existing adapter returns `False` after the already-atomic first attachment.

- [x] **Step 2: Implement transactional idempotency**

Wrap the full attachment read, lineage validation, conditional update, `changes()` read, and running event append in `db.transaction()`. Return `True` without another event when the stored row is already `running` with the exact child and worker.

- [x] **Step 3: Verify both focused contracts and the composed SSE flow**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_agent_delegation_service.py::test_repeated_child_attach_is_idempotent_for_same_claim -q
.venv/bin/python -m pytest backend/tests/test_conversation_compaction.py::test_below_threshold_check_does_not_create_compaction_operation -q
.venv/bin/python -m pytest backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_parent_streams_live_child_agent_lifecycle -q
```

Expected: all three tests pass without timing delays or duplicate events.

### Task 3: Run regression and architecture gates

- [x] **Step 1: Run complete related test files**

Run the context compaction, delegation persistence, delegation coordinator, and composed SSE test files. Expected: all pass.

- [x] **Step 2: Run the Agent Core architecture gate**

Run:

```bash
npm run check:agent-refactor
```

Expected: command exits zero with all available checks passing; credential-skipped live-provider checks, if reported, remain explicitly identified rather than treated as passes.

- [x] **Step 3: Review the final diff**

Run:

```bash
git diff --check
git diff -- backend/tests/test_conversation_compaction.py packages/purra/src/purra/context_orchestration/compaction.py backend/tests/test_agent_delegation_service.py backend/infrastructure/persistence/delegation_store.py
```

Expected: no whitespace errors and only the two regression tests, Operation gating, and transactional idempotent attachment.

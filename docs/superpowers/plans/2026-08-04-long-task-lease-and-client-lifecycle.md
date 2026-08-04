# Long-Task Lease and Client Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent active screenplay long-task Runs from being orphaned by shared-database contention and guarantee closure of every OpenAI-compatible client.

**Architecture:** The application lifespan owns a second `DatabaseConnection` to the same WAL database for Run lease heartbeats and orphan recovery, and injects its lease store into `AgentComposition`. The OpenAI adapter transfers client ownership into the returned stream and closes one-shot clients in `finally`; sanitized exception-chain types are persisted in traces.

**Tech Stack:** Python 3.12, asyncio, aiosqlite/SQLite WAL, OpenAI Python SDK, pytest/pytest-asyncio.

## Global Constraints

- Do not extend the 30-second Run lease as a substitute for reliable renewal.
- Do not persist API keys, request bodies, response bodies, or raw exception messages.
- Preserve constructor compatibility by falling back to the primary database when no execution database is injected.
- Preserve the existing 5-second and 20-second long-task retry backoff.

---

### Task 1: Isolate Run lease persistence

**Files:**
- Modify: `backend/application/agent_composition.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/test_run_execution_control.py`
- Modify: `backend/tests/test_main_lifespan.py`

**Interfaces:**
- Consumes: `DatabaseConnection`, `SqliteExecutionLeaseStore`, `monitor_orphaned_runs`.
- Produces: `AgentComposition(..., execution_db=None)` and a lifespan-owned control-plane connection.

- [ ] **Step 1: Write the failing isolation test**

Add a test that initializes primary and control `DatabaseConnection` instances on the same temporary path, creates a short-lease Run on the primary connection, binds `RunExecutionSession` to `SqliteExecutionLeaseStore(control_db)`, holds the primary connection lock longer than one lease interval, and asserts that `heartbeat_at_ms` advances and the signal remains unset.

- [ ] **Step 2: Run the isolation and lifespan tests to verify RED**

Run: `.venv/bin/python -m pytest -q backend/tests/test_run_execution_control.py backend/tests/test_main_lifespan.py`

Expected: the new test fails because composition/lifespan do not own a separate execution connection, and existing lifespan count assertions expose the one-connection assumption.

- [ ] **Step 3: Inject and own the control-plane connection**

Change the constructor boundary to:

```python
def __init__(self, db, *, execution_db=None, ...):
    self._execution_db = execution_db or db
    self._execution_lease_store = SqliteExecutionLeaseStore(self._execution_db)
```

In `main.lifespan`, initialize `execution_db` after the primary database, use it for `recover_orphaned_runs`, `AgentComposition(..., execution_db=execution_db)`, and `monitor_orphaned_runs`, then close it only after monitors and composition have stopped.

- [ ] **Step 4: Run the isolation and lifespan tests to verify GREEN**

Run: `.venv/bin/python -m pytest -q backend/tests/test_run_execution_control.py backend/tests/test_main_lifespan.py backend/tests/test_agent_composition.py`

Expected: all selected tests pass and both connections are closed on normal and failed startup.

### Task 2: Close OpenAI-compatible clients on every path

**Files:**
- Modify: `backend/infrastructure/models/openai_chat.py`
- Modify: `backend/tests/test_tool_choice_adapters.py`

**Interfaces:**
- Consumes: `utils.async_stream.close_async_resource` and `OwnedAsyncIterator`.
- Produces: streaming iterators that own `(raw_stream, client)` and one-shot calls that close client exactly once.

- [ ] **Step 1: Write failing client lifecycle tests**

Add observable fake clients with an async `close()` counter and tests for:

```python
result = await openai_chat.chat_stream(...)
await result["stream"].aclose()
assert client.close_calls == 1
```

Also assert closure when stream creation raises, when `chat_no_stream` succeeds, and when `generate_title` succeeds.

- [ ] **Step 2: Run lifecycle tests to verify RED**

Run: `.venv/bin/python -m pytest -q backend/tests/test_tool_choice_adapters.py -k 'client or close'`

Expected: client close counters remain zero.

- [ ] **Step 3: Implement ownership-safe closure**

Import `close_async_resource`. Wrap stream establishment in `try/except BaseException` that closes the client before re-raising; return `OwnedAsyncIterator(_generate(), raw_stream, client, ...)`. Wrap `chat_no_stream` and `generate_title` request/result normalization in `try/finally: await close_async_resource(client)`.

- [ ] **Step 4: Run adapter tests to verify GREEN**

Run: `.venv/bin/python -m pytest -q backend/tests/test_tool_choice_adapters.py backend/tests/test_async_stream.py backend/tests/test_agent_core_adapters.py`

Expected: all selected tests pass and existing raw-stream close behavior remains unchanged.

### Task 3: Persist sanitized exception-chain diagnostics

**Files:**
- Modify: `backend/agent_core/runtime.py`
- Modify: `backend/tests/test_agent_core_runtime.py`

**Interfaces:**
- Consumes: exception `__cause__`/`__context__` links.
- Produces: `errorChainTypes: list[str]` in model failure traces, bounded to eight unique type names.

- [ ] **Step 1: Write the failing trace test**

Construct `ModelGatewayError` caused by `APIConnectionError` caused by a local `OSError`, execute one model round, and assert:

```python
assert trace.details["errorChainTypes"] == [
    "ModelGatewayError",
    "APIConnectionError",
    "OSError",
]
```

Assert no exception messages occur in the trace.

- [ ] **Step 2: Run the trace test to verify RED**

Run: `.venv/bin/python -m pytest -q backend/tests/test_agent_core_runtime.py -k error_chain`

Expected: trace lacks `errorChainTypes`.

- [ ] **Step 3: Add bounded type-only chain extraction**

Add `_error_chain_types(error, limit=8)` that follows `__cause__`, then `__context__`, stops on object identity cycles, and returns only class names. Add its result to model-round and stream exception trace details while retaining existing `errorType`.

- [ ] **Step 4: Run runtime tests to verify GREEN**

Run: `.venv/bin/python -m pytest -q backend/tests/test_agent_core_runtime.py backend/tests/test_agent_core_engine.py`

Expected: all selected tests pass without storing raw exception text.

### Task 4: Full verification

**Files:**
- Verify only; no new production behavior.

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: fresh regression evidence.

- [ ] **Step 1: Run focused long-task and lifecycle regressions**

Run: `.venv/bin/python -m pytest -q backend/tests/test_run_execution_control.py backend/tests/test_main_lifespan.py backend/tests/test_agent_composition.py backend/tests/test_tool_choice_adapters.py backend/tests/test_async_stream.py backend/tests/test_agent_core_adapters.py backend/tests/test_agent_core_runtime.py backend/tests/test_agent_core_engine.py backend/tests/test_agent_core_long_tasks.py backend/tests/test_screenplay_long_task_execution.py`

- [ ] **Step 2: Run the full backend suite**

Run: `.venv/bin/python -m pytest -q backend/tests`

- [ ] **Step 3: Run static checks**

Run: `npm run typecheck`

Run: `git diff --check`

- [ ] **Step 4: Review scope**

Confirm the diff changes only execution-lease connection ownership, OpenAI client ownership, sanitized error-chain types, their tests, and these design/plan documents; preserve all unrelated user changes.

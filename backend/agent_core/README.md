# Agent Core

[简体中文](README.zh-CN.md) | English

`agent_core` is the product-neutral execution kernel. It owns contracts,
planning/runtime policy, Run lifecycle, tool authorization, approvals, and
orchestration ports. It must remain independently importable with only the
Python standard library.

## Dependency direction

```text
host / domain / infrastructure
             ↓
        application services
             ↓
          agent_core
```

Core must not import a host transport, product domain, model SDK, database
driver, or concrete persistence adapter. Static tests in
`tests/test_agent_core_boundaries.py` and `tests/test_application_boundaries.py`
enforce this rule.

## Dynamic planning

The initial plan is a tentative roadmap, not an irrevocable execution script.
After every completed or recoverably failed tool transition, Runtime gives the
planner the trusted tool-result continuation, completed-step history, and
remaining round budget. A planner that implements `DynamicTaskPlanner` can
then keep, replace, add, or remove future steps before the next model round.

Completed, blocked, and failed steps remain immutable history. The controller
atomically persists each revised future plan and its `run.todos_updated` event.
The runtime keeps all request-scoped candidate tool schemas budgeted, but
exposes only the tool authorized by the latest current step. Replanning never
bypasses tool policy, approval, scope validation, or idempotency boundaries.
Authorization rejection and approval decline are not treated as recoverable
tool failures.

## Reusable persistence ports

- `RunRepository`: atomic Run lifecycle and outbox events.
- `ExecutionLeaseStore`: execution ownership, heartbeat, and durable cancel.
- `DelegationRepository`: parent/child task queue and result propagation.
- `CheckpointStore`: durable, cursor-based Run snapshots.
- `ApprovalGateway`: one-shot human decisions.
- `ToolIdempotencyGateway`: replay-safe side-effecting tool execution.

Concrete adapters are assembled by the host composition root. The current
host provides SQLite implementations under `infrastructure/persistence`.

## Live multi-Agent delegation

The writing host registers `delegateToAgents` only for a root chat Run. The
parent may delegate one to three independent `researcher`, `reviewer`, or
`analyst` objectives. Each accepted objective is durably queued, claimed under
the parent's bounded child capacity, and executed as its own Agent Run with an
immutable `RunLineage`.

Child Runs share the parent cancellation signal, cannot delegate recursively,
and receive only the tool modes allowed by the product role registry. The
current writing roles are read-only. The parent waits for required child
results before continuing, while delegation lifecycle events are multiplexed
into the parent SSE stream. The desktop work log therefore updates through
`queued`, `claimed`, `running`, and terminal states without waiting for the
parent answer to finish. Durable checkpoint snapshots remain the recovery
source if a live stream is interrupted.

## Adding another adapter

New adapters must pass the reusable behavioral assertions in
`tests/support/agent_adapter_contracts.py`. These contracts verify ownership,
single-winner claims, cancellation, delegation capacity, checkpoint cursors,
and tool replay semantics without depending on SQLite.

Product-specific agent roles, context providers, tool catalogs, response
policies, and UI mappings stay outside this package.

The current writing product defines its role ids, display titles, delegation
descriptions, trusted instructions, and allowed tool modes in
`domains/writing/agent_roles.py`. Application services consume the injected
registry; neither Agent Core nor the desktop UI contains writing-role names.

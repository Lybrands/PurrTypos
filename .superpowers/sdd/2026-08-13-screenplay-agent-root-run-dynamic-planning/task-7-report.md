# Task 7 report: Screenplay checkpoint plan revision

Status: PASS

## Outcome

- Added `ScreenplayCheckpointPlanner` and a durable SQLite checkpoint receipt.
- Triggered planning only after episode validation, document batch validation, or the complete review aggregate.
- Kept one public Root Run. Checkpoint planning uses a private host Child and Core alone publishes `run.todos_updated`.
- Accepted only full, same-ID TaskPlans whose completed history and private execution contract are unchanged; future title, description, and dependency order remain the only mutable fields.
- Mapped re-resolution, invalid output, provider failure, missing Root state, and stale ready output to persisted pause semantics.

## Durability and privacy

- Receipt identity is `(operation_id, checkpoint_key)` with transactional CAS reservation, bounded lease expiry, canonical ready plan/digest, and applied reconciliation against the Root event log.
- Crash before the Root event replays the persisted ready plan without a model call; crash after the Root event marks the receipt applied without replaying the plan.
- Original/current plans come from the Root's authoritative first/latest todo event plus current todo snapshot, including status, result summary, error, executor, type, role, and dependencies.
- Planner input contains public plan fields, completed summaries, digest-only Artifact receipts, typed failures, constraint summaries, and remaining scope. Body content and task/run/unit/Operation/base Revision identifiers are excluded.

## Core seam

- `LongTaskExecutionUpdate` carries generic revision metadata, written into the same authoritative `run.todos_updated` event as the revision.
- Durable observer acknowledgements are typed Futures, so success, contract rejection, and caller cancellation all settle producer and dispatcher without mutual waiting.
- Root snapshots retain the TaskSpec needed to persist authoritative todo plan events.

## Verification

- Task7 targets: 210 passed.
- PurrA package: 91 passed.
- Durable dispatcher, boundaries, SQLite Run repositories, runtime cleanup, owner deletion, and query regressions: 77 passed.
- Real formal flow: three ordered episode checkpoints, one Root, one Operation, one final Revision.
- Real provider fake pause flow: one Root plus one private screenplay-part Child; re-resolution pauses LongTask/Operation/Turn and cancels the Root.
- Full `check:agent-refactor`: 1754 backend and 344 frontend passed. Five live Provider E2E cases remain skipped for missing credentials and are release blockers, not passes.

# Task 5 report

Status: PASS

## Delivered

- `ScreenplayAgentService.execute_turn()` now submits every Conversation Turn through the shared `AgentRunService`; the Turn is bound as `conversation_turn_id` and owns one public Root Run.
- Answer turns finish with one Root, zero Operations, and zero Child Runs. Formal create/revise/review turns finish with one Root and one private durable Operation; the durable final response is committed back to that same Root.
- The persisted `planner_run_id` column is exposed as `rootRunId`; `plannerRunId` remains a read-only deprecated alias, with no schema migration.
- The router no longer constructs the screenplay intent model planner, task resolver, or private dispatcher. The legacy model instructions, second answer call, fixed `_durable_plan()`, legacy phase bindings, intent `reply`, and service-owned durable execution/finalization chain were removed.
- Screenplay admission now persists the admitted intent. Its dispatcher atomically attaches the LongTask and transitions the Turn to running, then atomically maps paused, failed, canceled, and exceptional execution outcomes to the existing Operation/Turn lifecycle.
- Final assistant content remains data-driven: `compose_final_response` stores `metadata.finalResponse`, the generic recipe dispatcher returns it, and the Root publishes it. The screenplay Root completion projector verifies that authoritative Part and commits the Revision, Operation, Turn, Root terminal state, and canonical terminal event in one transaction. No screenplay-specific final text is synthesized in Core.
- Planner model invocations now inherit the Root Turn id through the generic PlanningPort, including repair and dynamic replan calls, so canonical output sequence and correlation remain unique within the Turn.
- Multi-episode recipes now use barriers derived from model-authored public step bindings projected through the validated Root `TaskPlan.steps` order. A shuffled binding array cannot reorder public execution, and private Parts cannot advance a later public Root step until every terminal Part of the preceding public step has completed.
- Manual cancellation now persists Root cancellation and lets the supervised Root signal drive durable cleanup instead of canceling the outer service coroutine; Root, LongTask, Operation, and Turn settle consistently.

## TDD evidence

- RED: the first shared-Root answer attempt collided on canonical output sequence because planner invocations lacked the Turn id.
- RED: the first multi-episode formal run put dependency-related public steps in `RUNNING` concurrently and violated the Root plan invariant.
- RED: a real manual-cancel test exposed that canceling the outer service task could strand supervisor cleanup until its Run lease expired.
- RED: shuffled valid screenplay bindings made the private recipe follow payload array order instead of the validated Root plan order.
- RED: admission-before-dispatch failure left an active queued Operation/Turn, and injected second-write failures left Operation and Turn terminal state split across transactions.
- RED: a later Root commit projector failure rolled back the Root terminal event but left the screenplay Revision, Operation, and Turn already completed by the profile dispatcher.
- GREEN: answer, formal success, multi-episode barriers, pause, runtime failure, and manual cancellation all pass through the real shared composition and `AgentRunService` path.
- GREEN: the manifest compiler orders bindings by validated Root steps; pre-dispatch terminal settlement is idempotent; paused/failed/exception settlement joins one ambient SQLite transaction and retries cleanly.
- GREEN: answer and formal product completion both join the Root terminal transaction. A later projector failure rolls back Root state, canonical event, Revision, Operation, and Turn; retry is idempotent and publishes exactly one Revision.
- GREEN: the required route/durable-service target passed (35 tests).
- GREEN: the related screenplay, persistence, composition, LongTask, WorkItem, planner, and Core-engine suites passed.
- GREEN: the focused screenplay, route, repository, transaction, cancellation, LongTask, and WorkItem suites passed (246 tests); Agent boundary guards passed (54 tests).
- GREEN: `npm run check:agent-refactor` passed, including typecheck, frontend unit/projection/session tests, and 1,650 backend tests. Five real-provider E2E cases remain skipped because their credentials are unavailable and retain their existing `RELEASE BLOCKER` markers.
- `git diff --check` and the production legacy-symbol search passed.

## Lifecycle boundary

A paused formal Root is terminal `canceled`, while its private Operation/LongTask remains resumable. Resume reuses the persisted `rootRunId` for private progress and never creates a second Root; it cannot reopen the already-terminal Root to emit another final response. A future product requirement for a public post-resume response therefore needs an explicit continuation-Run protocol rather than reviving the original Root.

# Task 6 report

Status: PASS

## Delivered

- Added a generic `AgentRunService.run_host_child()` application boundary. The
  caller supplies a complete `RunLineage`; the service owns Core submission,
  canonical stream consumption, signal/caller cancellation, terminal wait, and
  Core release.
- Host-orchestrated children reject any non-null `delegation_id` and always run
  with delegation disabled. Screenplay AI Parts use parent/root equal to the
  durable task's `created_by_run_id`, role `screenplay-part`, and depth 1.
- `ScreenplayTaskUnitExecutor` now carries the Root identity into the private
  task view. Draft scenes, episode metadata, document sections, review
  dimensions, and final-response composition run as AI Children. Evidence and
  Manifest validation remain deterministic host Parts.
- Child domain context carries the exact task id, unit id, and expected Part
  key. `ScreenplayToolLoopPolicy.should_plan()` therefore remains false for
  every internal Child; it cannot create a second public screenplay plan.
- `ScreenplayStructuredCallService` and `ScreenplayToolCallingService` no longer
  create Core instances, submit handles, renew/release leases, settle Runs, or
  implement cancellation watchers. They retain provider request shaping,
  structured response parsing, schema validation, and authoritative Candidate
  Artifact lookup.
- No Screenplay role registry was introduced. Existing Screenplay AI Parts have
  null delegation ids. Generic coverage proves the delegation adapter is only
  installed when a request-scoped role registry exists and delegation is
  explicitly enabled; planner validation already requires a legal
  `executor=agent` plus `agentRole` pair.

## TDD evidence

- RED: both signal-driven and caller-task cancellation tests failed because
  `AgentRunService` had no host-child entry.
- GREEN: both paths cancel the same Child handle, await its terminal result,
  clean the watcher, and release the Core. A real SQLite cancellation test ends
  with status `canceled` and clears execution owner/lease fields.
- GREEN: a real persisted AI Child has parent/root lineage equal to an existing
  Root, null delegation id, role `screenplay-part`, and depth 1.
- GREEN: captured Child domain context has the requested task/unit/Part key and
  the Screenplay planning policy returns false.
- GREEN: deterministic evidence leaves the `ai_agent_runs` count unchanged;
  parameterized classification covers every current private Recipe Part kind.
- GREEN: the structured/tool service source contains no direct Core submit,
  handle wait/cancel, Run repository, or lease ownership.
- GREEN: required screenplay rewrite, durable dispatcher, and Run query suites
  pass; related tool catalog, composition, delegation, planner, persistence,
  route, durable-service, boundary, and cancellation suites pass.
- GREEN: `npm run check:agent-refactor` passes, including typecheck, 344
  frontend unit tests, and 1,681 backend tests. Five real-provider E2E cases
  remain skipped because credentials are unavailable and retain their existing
  `RELEASE BLOCKER` markers.

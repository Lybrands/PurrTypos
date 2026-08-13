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

## Review corrections

- `_HostChildCancellationSignal.wait()` now cancels and awaits both internal
  `Event.wait()` tasks in `finally`. Tests cover normal owner cleanup,
  upstream signal, local/caller cancellation, and the simultaneous race with
  no pending waiter left behind.
- A validated Child result is now durable protocol data rather than validator
  process memory. The terminal Run transaction writes one private, versioned
  `run.validated_result` canonical event; `AgentRunService` reads it through
  the generic output repository only for `VALIDATED_RESULT` policy.
- Readback fails closed unless the Run is DONE and exactly one event has the
  authoritative run/turn/source/kind/channel/visibility/schema contract. The
  private event never enters session-public replay. Missing, duplicate,
  malformed, and cross-scope records are rejected.
- `ScreenplayStructuredCallService` reparses and validates the persisted value.
  A divergent in-memory validator test proves the persisted value wins, and a
  recreated service reads it without replaying the provider or projector.
- Failure injection after writing the private event proves Run terminal state
  and result journal roll back together.
- The post-review full Agent refactor gate passes with 1,700 backend tests and
  344 frontend tests. Five credential-gated real-provider E2E cases remain
  skipped with their existing `RELEASE BLOCKER` markers.

## Durable host retry correction

- Added a generic durable host-child registry keyed by an opaque stable key.
  The receipt stores only an identity digest, normalized Run contract,
  generation/attempt identity, reservation lease, bound Run id, and terminal
  status. A partial unique index on the persisted Run attempt binding closes
  the Run-begun-before-receipt-bind crash window.
- Reservation, bind, reconcile, and failed-generation advance use SQLite
  transaction/CAS rules. Concurrent same-key callers create one Child; an
  existing RUNNING Child is awaited without canceling it when a secondary
  waiter leaves; DONE is read back from canonical persisted output with zero
  provider/projector calls.
- Identity validation covers request/model/domain/tool/response contracts,
  exact profile/binding/lineage/turn scope, and the canonical started event.
  The Screenplay caller uses deterministic project/task/unit/Part identities
  to derive the opaque key and explicitly selects
  `REUSE_DONE_RETRY_FAILED`; canceled/blocked attempts remain fail-closed.
- Recovery tests cover reserve-before-Run and Run-before-bind windows, a new
  service/composition reading a crashed host's DONE Child, concurrent active
  callers, waiter cancellation, one-winner failed generation advance,
  canceled retry rejection, and request/lineage conflicts.
- Validated terminal replay is now strict: exact same replay is idempotent;
  validated-to-none, none-to-validated, a different value, or a persisted
  host-child response-policy mismatch is rejected atomically.
- Product aggregate deletion explicitly removes receipts owned by the deleted
  project because this legacy database intentionally leaves foreign keys off.
  Ordinary session unlink keeps terminal receipt/Run audit, and aggregate
  cleanup releases the opaque stable key for safe future recreation.
- Final `npm run check:agent-refactor` passes with 1,720 backend tests and 344
  frontend tests. The five credential-gated real-provider E2E cases remain
  skipped with their existing `RELEASE BLOCKER` markers.

## Candidate terminal validation correction

- Replaced the process-local, post-DONE `validate_candidate` callback with a
  persisted, versioned `candidateValidation` contract. The contract contains
  only task identity, bounded IDs and immutable content digests; Artifact
  metadata stores only its digest plus Run/Turn/task/unit scope.
- Composition injects one pure deterministic Screenplay Candidate normalizer
  into both the tool/host-capture write path and the candidate terminal
  projector. Unknown protocol/kind/fields and scope mismatches fail closed.
- The terminal projector reads the immutable Run binding, validates the
  canonical started Turn and OPEN Artifact scope, reruns the persisted
  contract, and finalizes the Artifact in the same transaction as Child DONE.
  It never trusts a callable or service-local Candidate value.
- A real tool Child test submits a generic-schema-valid but task-invalid review
  first. That generation becomes FAILED with no finalized Artifact; the same
  durable host key advances to generation 2, reruns the provider, and publishes
  only the corrected Artifact. A changed validation digest conflicts with the
  existing stable-key identity without another provider call.
- Strict contract/version and non-deterministic normalizer tests pass. Existing
  host-captured scene, structured Child, candidate rollback, durable registry,
  persisted output/replay, cancellation, Screenplay acceptance, and boundary
  suites remain green.
- Final `npm run check:agent-refactor` passes with 1,725 backend tests and 344
  frontend tests. Five credential-gated real-provider E2E cases remain skipped
  with their existing `RELEASE BLOCKER` markers.

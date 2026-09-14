# Shared legacy Agent contract inventory (archived)

> Historical snapshot from 2026-09-12. All executable legacy Agent source and
> freeze machinery were removed on 2026-09-14. “Current” below means the state
> at capture time, not today's implementation. Current ownership is defined by
> `backend/agents/README.md` and the replacement code; this file is read-only
> migration evidence and creates no compatibility promise.

## 1. Current factual contracts

### Composition and profile selection

- `application.composition_factory.create_agent_composition` installs exactly
  three product profile factories into one process-scoped `AgentComposition`:
  writing, novel analysis, and screenplay.
- `AgentProfileRegistry` requires profile IDs and domain namespaces to be
  non-empty and unique. Request selection is by domain namespace; Core creation
  is by profile ID.
- A profile supplies its adapter, request preparation, context-provider factory,
  response judges, task admission, LongTask dispatcher, and active-execution
  cleanup hook.
- `AgentComposition` owns the shared run repository, output repository and
  publisher, Run control stores, approval gateway, tool-idempotency gateway,
  Artifact claims, LongTask repository, provider capability cache, live Core
  handles, and detached background task handles.
- Product screenplay projectors are currently installed globally at the
  composition root. They inspect persisted Run bindings before deciding whether
  to act.

### Run creation and immutable authority

- A top-level Agent request is persisted as one `ai_agent_runs` row with Run
  status, model provenance, runtime authority, binding, scope, usage, terminal
  response, and execution lease fields.
- Database triggers make runtime authority, model-request provenance, Run
  binding, and tree scope immutable after creation.
- The current schema does not have first-class `implementation_id`,
  `implementation_version`, `tool_contract_version`, `recipe_version`, or
  `artifact_schema_version` columns. Some equivalent information is spread
  across binding attributes, task metadata, output/tool protocol strings, and
  Artifact schema fields, but that is not sufficient for deterministic
  implementation routing.
- `AgentRunService.run` prepares a request, resolves provider capability,
  binds the profile, creates a request-scoped Core, submits it, streams
  persisted updates, and closes/releases the Core.
- Start binding lifecycle callbacks exist before submit, after Run start, and on
  start failure. Product/router layers also invoke parts of this lifecycle,
  which means ownership is not currently single-layered.

### Durable Task and Operation identity

- `ai_agent_long_tasks` persists one recoverable recipe execution. A task binds
  to Root/continuation Runs through `ai_agent_long_task_runs`.
- `ai_agent_long_task_units` uses `(task_id, unit_id)` as its primary identity
  and stores attempt, leases, dependencies, output ref, Artifact digest,
  failure/disposition and validation receipt.
- A model-backed Unit executes as an Operation inside its owning Root Run. The
  host operation ID is conventionally `taskId:unitId:attempt`.
- Operation metadata contains `operationScopeId` and an `operationBinding`
  object. No synthetic Agent Run is created for the Unit, and a Unit result may
  not change its owning Root Run.
- Existing shared usage aggregation is keyed by `(task_id, run_id)`, not by
  operation/attempt. Product code must query canonical operation events to
  recover finer provenance.

### Canonical output and replay

- Canonical output is stored in `ai_agent_run_events`. Canonical rows carry
  immutable event ID, Run/Root/Agent identity, Turn, invocation, stream,
  sequence, source, kind, channel, visibility and source event key.
- Public replay must read the canonical output journal, filter by public
  visibility and serialize through the shared SSE mapping. Private operation
  output must not become public merely because it belongs to the same Root Run.
- `(run_id, sequence)`, canonical `event_id`, `source_event_key`, and
  `(root_run_id, root_sequence)` have uniqueness constraints in their applicable
  scopes.
- `AgentRunQueryService` exposes snapshot version 2 with Run, todos, public
  event envelopes, product events, cursor and pagination state.
- `stream_agent_pages` owns only cursor advancement, projection-version wakeups
  and committed-output notification waits. Product queries still own event
  inclusion and projection.
- Transport disconnect terminates delivery. It is not by itself a domain
  cancellation command.

### Tools, approvals and effects

- Each profile supplies a ToolCatalog; the composition root combines it with
  allowed extras and validates presentation metadata.
- Tool approvals are durable rows keyed by approval ID and associated with Run,
  tool call, risk, status and expiry.
- Tool receipts are keyed by `(run_id, tool_call_id)` and persist argument
  digest, output, effects, error code, planning/step dispositions and effect
  state.
- Existing receipt identity is Run + tool call. Replacement operations must
  ensure tool-call identity cannot collide across attempts and that an effect
  receipt can be traced back to its operation.

### Artifacts

- Generic Agent Artifacts use an immutable logical owner reference within
  `(namespace, owner_id, kind, owner_ref_kind, owner_ref_id)`.
- Artifact batches have batch and idempotency uniqueness, sequence and content
  digest. Claims fence writers; projections record a product result reference.
- Artifact lifecycle is open, append/batch, validate/finalize and project. An
  interrupted attempt may leave a durable open Artifact; recovery must inspect
  effect state instead of assuming the model call did not write.

### Cancellation and orphan recovery

- `AgentCancellationService` uses the persistent Run control store. It fences
  cancellation, attempts to claim and terminalize a Run that has no live
  executor, and completes a durable cancellation receipt.
- A cancellation receipt distinguishes draining from completed and preserves a
  competing terminal Run status.
- `AgentOrphanRecoveryService` delegates ownership decisions to PurrA's
  `OrphanRecoveryCoordinator`, then persists product-facing todo and lifecycle
  outputs.
- LongTask cancellation/pause and Root Run terminal state are related but not a
  single status dimension. Replacement code must not infer completed workflow
  solely from a terminal conversation Run.

### Principal shared entry points

| Boundary | Current entry point |
| --- | --- |
| Composition | `application/composition_factory.py` |
| Profile contract | `application/agent_profile_registry.py` |
| Core assembly | `application/agent_composition.py` |
| Root/operation execution | `application/agent_run_service.py` |
| Durable operation adapter | `application/durable_agent_run.py` |
| Run cancellation | `application/agent_cancellation_service.py` |
| Orphan recovery | `application/agent_orphan_recovery_service.py` |
| Run snapshot | `application/agent_run_queries.py` |
| Cursor transport | `application/agent_event_stream.py` |
| Canonical output store | `infrastructure/persistence/sqlite_agent_output_repository.py` |
| Run control | `infrastructure/persistence/run_execution_store.py` |
| LongTask store | `infrastructure/persistence/sqlite_long_task_repository.py` |
| Generic Artifact store | `infrastructure/persistence/sqlite_artifact_repository.py` |

## 2. Legacy behavior that is not a replacement contract

- A dispatcher failure is currently prefixed with
  `novel_analysis_dispatch_failed` even when another profile owns the Run.
- Canonical Turn identity lookup is repeated in multiple services and does not
  consistently require canonical `event_id` or reject multiple matches.
- Start-failure settlement can be invoked by Run service, product service and
  transport layers. Idempotent storage reduces damage but does not establish a
  single owner for diagnosis and public presentation.
- Product modules maintain separate process-local active-task registries. These
  are optimization handles, not durable truth, and cannot coordinate multiple
  processes.
- Some callers pass fresh `asyncio.Event` instances that no owner ever sets.
  Explicit Run control is the actual cancellation authority; the unused local
  signal shape must not be mistaken for a working second cancellation plane.
- Orphan cancellation currently invokes the cancellation service around a
  separate terminal commit. This requires characterization under competing
  terminal states before replacement; its duplicated shape is not itself a
  contract.
- Shared LongTask usage loses operation/attempt granularity.
- Product stage/commentary projection can occur after a business Artifact has
  been written. A presentation failure must not invalidate that durable effect.
- The profile preset revision is currently a literal `"1"`; it is not a
  complete implementation-routing identity.
- Current schema and binding metadata cannot safely route a resumed historical
  Run between legacy and replacement implementations without an explicit
  version projection.

## 3. Replacement decisions already made

1. New implementations live under `backend/agents/`; frozen implementation
   modules are not imported by replacement code.
2. One Run has one immutable implementation identity for create, resume,
   cancel, orphan recovery and replay.
3. New and legacy implementations never write the same Run concurrently and do
   not dual-write product Artifacts.
4. Unit-level identity is the durable Operation `(rootRunId, taskId, unitId,
   attempt)`, not a synthetic Part Run.
5. PurrA owns canonical Run/event/tool lifecycle semantics. PurrTypos owns
   product context, recipes, Artifact schemas, admissible evidence and public
   projections.
6. Shared streaming code owns transport mechanics only. Product event
   visibility and read models remain product-owned.
7. Database migrations are additive until legacy execution is retired.

## 4. Decisions still required before S2 implementation

| Decision | Options/constraint |
| --- | --- |
| Implementation identity storage | Prefer immutable first-class Run columns or one immutable, indexed identity record. Binding attributes alone are overloaded and profile-specific. |
| Historical Run classification | Missing identity must map explicitly to the dated legacy implementation; it must never default to replacement. |
| Profile IDs during rollout | Replacement needs distinct internal IDs/namespaces or a version-aware registry that can hold both without violating current uniqueness checks. External API identity should remain stable. |
| Operation persistence | Decide whether operation identity needs a dedicated table or whether canonical events + Artifact owner + indexed metadata provide sufficient atomic queries. Usage requires a durable operation dimension either way. |
| Canonical Turn resolver | Define whether a started lifecycle event must exist exactly once for every routable Run and how legacy noncanonical rows degrade. |
| Lifecycle settlement owner | Select one application layer to persist start/terminal failures; transport may only present an existing receipt. |
| Orphan cancel ordering | Characterize fence, competing completion, terminal commit failure and projector failure before choosing one settlement sequence. |
| Shared failure codes | Preserve stable public categories while allowing product-specific private detail and bounded retry policy. |
| Artifact interrupted-write policy | Prefer reconcile/finalize when the exact effect is proven; otherwise create an attempt-scoped Artifact without deleting diagnostic evidence. |
| Rollout policy persistence | The create-time decision must be stored with the Run, not recomputed from a mutable process flag on resume. |

## 5. Existing characterization evidence

Relevant current test groups include:

- `test_agent_composition.py`
- `test_main_lifespan.py`
- `test_agent_run_queries.py`
- `test_agent_event_stream.py`
- `test_ai_composed_sse_wire_contract.py`
- `test_sqlite_agent_output_repository.py`
- `test_run_execution_control.py`
- `test_long_task_run_binding.py`
- `test_durable_child_identity.py`
- `test_operation_stage_output.py`
- `test_public_commentary_output.py`
- `test_purra_sqlite_run_repository.py`

These tests cover many isolated contracts but do not yet form one replacement
conformance suite. F1 is complete only after the following missing fixtures are
added to the plan for S1-S6:

- legacy Run with no implementation identity;
- replacement Run with immutable implementation identity;
- resume/cancel/replay dispatch by persisted identity after process restart;
- two concurrent operations in one Root with distinct usage, reads and
  Artifacts;
- write succeeded followed by presentation/settlement interruption;
- canonical started Turn missing, duplicated or noncanonical;
- start failure at each ownership boundary, proving one settlement;
- transport disconnect versus explicit cancellation;
- orphan recovery racing normal completion;
- replacement profile disabled after a Run was already created.

## 6. Acceptance boundary

F1 documentation and deterministic tests do not prove real Provider or
Web/Electron acceptance. Shared replacement infrastructure is not complete
until a new Run can be created, interrupted, resumed, canceled and replayed
through the actual installed PurrA artifact, and the product-specific acceptance
matrices have also passed.

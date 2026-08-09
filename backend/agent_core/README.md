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

## Phase 1 module boundaries

The historical `runtime.py`, `engine.py`, `contracts.py`, and `ports.py` entry
points are now same-name packages; existing imports remain compatible. New
code should import the narrowest responsibility module:

- Runtime orchestration is separate from model-round accumulation, tool-batch
  streaming, and buffered-response finalization.
- Engine orchestration is separate from options, context assembly, planning
  validation, and durable-execution helpers.
- Contracts expose stable message, planning, context, tool, and run families,
  with foundational enums in `enums.py`.
- The ports package facade only aggregates definitions owned by the model,
  context, planning, tools, persistence, and run-lifecycle modules.

`tests/test_agent_core_phase_one_structure.py` prevents the legacy files,
already extracted definitions, or facade/orchestrator growth from returning.

## Phase 2 host-extension boundary

Core supports product hosts through three product-neutral contracts:
`RunBinding` persists an opaque aggregate/command association,
`ExecutionRecipe` validates a host-compiled mechanical DAG, and
`DomainEventProjector` lets a persistence adapter project a domain effect in
the same commit transaction. Core does not interpret a Binding, author a
product Recipe, or understand a Projector's business result.

Product request DTOs, request/event mapping, authoritative queries, and
business Run lifecycle hooks remain outside Core. The screenplay host owns
those pieces in `schemas/screenplay_agent_run.py`, `application/screenplay_agent_*`,
`domains/screenplay/query_port.py`, and
`infrastructure/screenplay/agent_query.py`; the generic `ChatStreamRequest`,
`AgentRunService`, and SSE mapper contain no screenplay fields or branches.

## Dynamic planning

The initial plan is a tentative roadmap, but successful execution follows that
compiled plan by default. Runtime invokes `DynamicTaskPlanner` only after a
recoverable failure or protocol/authorization drift, or when a host-owned tool
result explicitly returns `ToolPlanningDisposition.REPLAN` because it selected
a branch that changes the valid future path. Normal `PROGRESSED` and
`COMPLETED` batches do not spend another planner model call.

Completed, blocked, and failed steps remain immutable history. The controller
atomically persists each revised future plan and its `run.todos_updated` event.
The runtime keeps all request-scoped candidate tool schemas budgeted, but
exposes only the tool authorized by the latest current step. Replanning never
bypasses tool policy, approval, scope validation, or idempotency boundaries.
Authorization rejection and approval decline are not treated as recoverable
tool failures.

`ToolPlanningDisposition` is separate from `ToolStepDisposition`: the former
controls whether future planning authority must be revised, while the latter
only says whether the current step remains active. Both are host-owned tool
result fields and cannot be requested through model arguments.

A successful tool call is not automatically a completed planner step. Tool
adapters return `ToolStepDisposition.CONTINUE` when a bounded commit made
durable progress but the same operation still has work remaining. Core exposes
that as `ToolBatchOutcome.PROGRESSED`, keeps the current capability authorized,
and excludes the step from immutable completion history. Dependent tools become
eligible only after the adapter returns `COMPLETE`.

Runtime limits distinguish this monotonic partial progress from stalled
execution. Each `PROGRESSED` round may unlock one separately bounded progress
round through `RuntimeLimits.max_progress_rounds`; ordinary success, retries,
failures, and malformed output never earn that allowance. Large batch artifacts
therefore do not depend on a domain-specific fixed round count, while Core still
retains a hard total bound.

## Task admission and durable long tasks

The planner remains the only model-backed semantic decision point. It compiles
the user's goal into a `TaskSpec`; it does not estimate execution cost or
decide whether to create a long task. After constraint and authority checks,
but before installing the plan, Core calls an injected `TaskAdmissionEvaluator`.
Application/domain code resolves that semantic target against authoritative
state and returns `inline`, `durable`, `clarify`, or `reject`. Core therefore
does not need product concepts such as scenes, chapters, or datasets, and the
host does not need a second intent-model request.

A durable admission must explicitly name every Planner step that the durable
executor will fulfil. Core accepts the handoff only when that coverage is an
exact match for the installed plan, and completes only those covered steps
after the durable result succeeds. This generic invariant prevents an external
workflow from silently bypassing or falsely completing an unrelated Planner
step without teaching Core anything about the domain.

`long_tasks` defines product-neutral contracts for durable tasks, dependency
units, leases, checkpoints, retries, pause, resume, and cancellation. The Core
coordinator understands only the unit DAG and execution states. Domain code
owns partitioning, unit inputs, semantic validation, and final merge;
Application creates the Work Item and Long Task and runs each unit as an
independent Agent Run. Pausing releases the active unit for immediate resume,
and a failed task receives a fresh retry allowance only after explicit resume.

The screenplay adapter admits only multi-scene draft generation to durable
execution. The shared Planner remains the AI author of the user-visible plan;
its plan selects the bounded draft capability rather than inventing an internal
child-Agent graph. After admission binds the authoritative scene range, the
screenplay host builds one stable mechanical DAG: one checkpointed Writer per
scene, ordered Writer lanes within each episode, a single global continuity
Reviewer after all Writers, conditional per-scene Rewriters, and deterministic
proposal assembly. Writer, Reviewer, and Rewriter child Runs use the exact
host-built prompt in direct-response mode with an empty tool catalog, so they
do not start another Planner or reload the whole project context. The Reviewer
returns only a compact issue report; scenes with no issues reuse their Writer
checkpoint without another model call. Only transient provider/stream failures
are retried. Truncation or invalid structured output fails the unit unchanged,
and a terminal unit failure atomically cancels every unfinished sibling so the
task cannot retain stale running children. List endpoints expose progress only;
scene text and the final proposal are loaded from task detail on demand.

Stage deliverables such as source analysis, creative briefs, structures, and
scene lists no longer enter an application path that launches a complete Agent
inside one retryable durable unit. They remain in the initiating primary Run
and use the ordinary dynamic Planner, tool events, streaming output, and
proposal-effect pipeline. Artifacts provide bounded writes and integrity, not a
second orchestration system.

## Context orchestration and compaction

Agent Core owns context budgeting, compression timing, hook invocation, and
technical validation. It derives one budget from the model window,
output/runtime reserves, tool schemas, and domain context claims, then checks
pressure at planning boundaries and before every model call. At 85% pressure
Core invokes the configured `ContextCompressionHook`; the hook receives the
complete source view and hard token limits and owns every semantic choice.

Staged providers may additionally implement `TaskContextDemandProvider`.
Their ordinary demand funds only the lightweight planning manifest; after a
`TaskSpec` is compiled, Core resolves supplemental task-specific claims and
rebuilds the final budget before retrieving content. Optional large recovery
or retrieval projections therefore consume no partition for an unrelated
request, and no domain has to encode a fixed local context window.

`context_orchestration` does not own summary schemas, persistence, retention
rules, semantic targets, or fallback summaries. When no hook is installed it
uses only a structural recent-message trimmer (20 messages, then token fitting)
that preserves complete tool exchanges. With a hook installed that fallback
does not run. Core validates privileged instructions, the current user request,
tool-call/result continuity, and the final token count. The application owns
semantic summaries, durable summary state, and any configured fallback chain.

Canonical conversation turns remain the single source used by persistence and
the UI. Compression produces a request-scoped model projection; it never
rewrites that history. The current application strategy stores only a derived
summary artifact with coverage and digest metadata, injects its repository and
summarizer through application-owned ports, bounds semantic compaction passes,
and explicitly selects its own recent-message emergency projection when a
dependency failure would otherwise leave the request above the hard budget.

## Model output and tool-data boundaries

`model_protocol` normalizes provider finish reasons. Any provider-declared
length limit makes the round incomplete: partial text cannot become a final
answer, and partial tool calls are neither executed nor written into later
model history. A request with a host-resolved task budget is not repeated with
the same allowance; legacy direct Runtime callers may retain one clean,
side-effect-free retry. Exhaustion preserves the `tool_call_truncated` or
`model_output_truncated` root cause and safe diagnostics.

Output sizing has three separate authorities. Infrastructure model profiles
declare provider capability ceilings, Application policies estimate and cap
one unit of product work, and `agent_core.output_budget` resolves the effective
allowance against the context window. Only that resolved allowance may become
a provider `max_tokens` parameter. The resolution, capability ceiling, limiting
factor, actual provider usage, and finish reason are emitted in durable Run
events. Chunked work must add or split execution units instead of increasing a
global model default.

Audited tools declare model-generated, host-bound, and host-derived paths with
`ToolDataContract`. Host-owned paths must stay outside the model-visible JSON
Schema. Large-output tools should prefer `delta`, `batch`, or
`resource_reference`: the model produces only new semantic data while the
host binds identity, revision, lineage, accumulated content, and completion
state.

`ToolSchema.name` remains the immutable protocol identifier. Localized
user-facing labels live separately in the host-owned `display_names` map using
language tags such as `zh-CN` and `en-US`. Core selects the request locale for
planner guidance and model narration, while provider payloads still contain
only the protocol name, description, and parameters. Runtime events carry the
complete label map so the UI can localize deterministically without asking the
model to rename a function.

`ToolExecutionLimits.max_argument_chars` is a configurable raw-JSON transport
safety envelope, not a context allocation or domain data budget. After JSON
decoding and narrowly scoped structured-value recovery, Core enforces the
registered schema again (`required`, types, enums, lengths, item counts,
numeric ranges, and additional properties). This means whitespace and escaped
Unicode cannot consume an unrelated 32K workflow budget, while domains retain
authority over useful semantic size through their versioned schemas. Failures
carry the tool name, validation stage, schema path, actual measurement, and
allowed bound without echoing the rejected payload.

## Stability evaluation

`evaluation.stability` derives content-free reliability signals from the
persisted Core event stream. It correlates tool starts, completions, and
results so explicit failures and calls that never reached a terminal result
remain distinguishable. The same report aggregates protocol error codes,
model interruption/retry evidence, context overflow, and compaction fallback
or failure outcomes. Infrastructure may attach storage-level artifact counts,
but it must not copy prompts, tool arguments, or generated content into the
stability report.

`StabilityTrendPolicy` applies caller-owned warning and failure thresholds to
a bounded newest-first Run window. Rate checks wait for a configurable minimum
sample, while consecutive-failure streaks alert immediately because hiding a
hard failure sequence behind a sample gate would be unsafe. The SQLite adapter
projects only trace counters, call identifiers, tool names, and error codes;
historical prompts, arguments, and tool-result content never enter the trend
evaluator.
User-canceled Runs are excluded from the trend window so an intentional abort
does not dilute failure rates or appear as an incomplete-tool regression.

`failure_classification` converts explicit, content-free evidence into stable
cause codes without claiming more certainty than the event stream supports.
Protocol errors, incomplete tool lifecycles, context failures, planner
contract violations, tool-handler failures, and model interruptions retain
separate classifications and remediation keys. A failed Run with no specific
evidence is reported as low-confidence observability debt instead of receiving
an invented cause.

`stability_gate` compares the newest Run window with the immediately preceding
window. It detects rate increases, failure-streak growth, and newly introduced
tool error codes. The Writing-owned promoted-incident catalogue fixes expected
classification codes in the existing runtime regression harness; the local
`check:agent-stability` command fails when any incident is no longer detected.

## Controlled recovery policy

`recovery` is the single Runtime decision layer for provider fallback, stream
interruption, truncation, tool-protocol repair, empty responses, response
repair, and tool-input correction. Every candidate action consumes a bounded
Run-scoped budget only after Core verifies cancellation state, remaining model
rounds, visible-output state, and whether tool side effects may have started.

Domain adapters may inject a `RecoveryPolicy` at the composition root, but only
Core can approve and account for attempts. JSON or Schema failures may be
repaired only when `ToolBatchResult.effect_state` proves the whole batch failed
before a handler started. An uncertain write effect blocks replay and failed-
step replanning. Allowed and denied decisions use the existing durable Run
trace path; `evaluation.recovery` exposes only cause, action, budget, and safety
reason codes, never model text or tool arguments.

## Recoverable artifact lifecycle

`artifacts` provides a domain-neutral `open → finalized/aborted` state
machine. Large results can be committed in ordered batches carrying an
idempotency key, content digest, expected revision, sequence, and coverage
keys. Core checks count, contiguous ordering, and duplicate/missing coverage
before finalization. `ArtifactValidator` keeps domain correctness outside
Core, while `ArtifactRepository` owns the transactional boundary. The SQLite
adapter atomically commits each batch, CAS revision, and replay receipt.

Multiple calls in one model round remain forbidden for ordinary write tools.
Core permits them only when every call targets the same `batch`-mode,
`PROPOSE`, cancellation-linearizable, host-durable artifact tool. The complete
call batch is still preflighted before any write. Domain adapters inject their
own `RuntimeLimits`, so Core's default no longer encodes a product-specific
batch-count assumption.

Planner and runtime tool contracts are now distinct. A registration may map a
stable business-level `planning_capability` onto one or more private runtime
tools. Core validates the public plan first, then deterministically lowers the
capability into its dependency-ordered runtime protocol. Private steps remain
durable and authorized but are omitted from public task-plan SSE; only the
business capability is shown. Host-authenticated continuation state can mark
individual private tools as already satisfied, while legacy persisted plans
that name runtime tools continue to execute unchanged.

The screenplay adapter internally uses begin/append/finalize tools for creative briefs,
structures, and scene lists. The Planner instead sees stable operations such
as `generateCreativeBrief`, `generateScreenplayStructure`, and
`generateSceneList`. A creative-brief artifact freezes the accepted
source-analysis version, evidence manifest, source limitations, target format,
and lineage. The model batches one brief-content entry plus bounded adaptation
decisions; it no longer echoes source limitations or the project format. A
structure artifact then freezes the accepted creative-brief version,
project-derived structure kind, unit count, and adaptation-decision manifest.
The model batches only structure units and each decision's landing references.
Screenplay reviews also use a summary entry plus bounded issue and verification
batches. For rereviews, the host freezes prior issue ordering and reconstructs
issue identifiers, acceptance criteria, and resolution provenance; the model
submits only the verification status and new evidence. Complete revisions use
separate batches for affected scenes and review-issue resolutions. The host
assembles final content, execution trace, lineage, and artifact references from
accepted state.

Source-range analysis uses the same lifecycle. The model declares summaries
and per-category item counts, then batches characters, events, conflicts,
adaptation assets, risks, questions, and evidence. The host derives selected,
fully read, sampled, and unread chapter coverage from durable source receipts
for the current Run. Evidence may cite only that frozen receipt manifest, so
the model no longer returns chapter-coverage identifier arrays.

## Work Items and Artifact scope

`work_items` defines durable task identity separately from a single execution
attempt. A Work Item has a small content lifecycle (`open → completed/canceled`)
and append-only Run links (`created`, `continuation`, or `reference`). Run links
do not advance the Work Item content revision, so a read-only reference cannot
invalidate an active writer's task snapshot. The creator link is immutable and
must be committed atomically with Work Item creation by the repository.

Artifact continuation contracts distinguish `run` and `work_item` scope. A
Run-scoped Artifact remains private to its creating Run. A Work Item-scoped
Artifact may be read by a linked Run, but every write requires an atomic,
expiring, exclusive claim after Core verifies Artifact revision, status, Work
Item identity, and Work Item status. These are execution-safety decisions;
natural-language intent and domain compatibility remain application/domain
responsibilities.

SQLite now persists Work Items, immutable Run links, Artifact scope, and
exclusive claims. Migration preserves existing Artifacts as Run-scoped and
backfills their original `run_id` as creator provenance. Run and Work Item
scopes have distinct unique indexes and lookup paths. Creating a Work
Item-scoped Artifact validates Work Item ownership and the creator Run's write
relation; claims are competed for atomically and can only be held by a
`created` or `continuation` Run until expiry.

The screenplay application integration now discovers only open, stage-
compatible candidates linked to the same session. The existing main planner
semantically selects `continue`, `reference`, or `ignore`; Core rejects any
identifier outside the authenticated candidate set. Only a selected candidate
receives a post-planning context claim. The domain then revalidates the exact
scope, persists the new Run relation, obtains an exclusive writer claim for a
continuation, and injects a bounded projection made only of complete metadata
fields and complete batches. References are explicitly read-only. Run teardown
releases every owned writer claim, while expiry remains the crash fallback.
The screenplay begin/append/finalize tool chains now use Work Item scope. Begin
atomically creates the Work Item, immutable creator link, Artifact, and initial
writer claim. A continuation resolves the existing Artifact through the current
Run link. Every append revalidates the opaque claim inside the same SQLite
transaction as the batch commit and advances both Artifact and claim revision;
finalize atomically finalizes the Artifact, completes the Work Item, and removes
the claim. Legacy Run-scoped Artifacts remain readable and writable through the
old path for migration compatibility.

Artifact maintenance is also split by responsibility. Core defines a storage-
neutral policy/report contract; the application schedules startup and periodic
sweeps; SQLite performs each sweep in one cancellation-linearizable
transaction. Expired claims, claims owned by a non-running Run, and claims whose
Artifact/Work Item/revision/write relation is no longer valid are disposable.
Open durable state is never garbage-collected. Terminal content retention is
disabled by default and can only be enabled explicitly with
`PURRTYPOS_AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS`; even then, an open
Artifact or a Work Item linked to a running Run is retained. Structural
inconsistencies are counted and logged without deleting their evidence. Orphan
Run recovery releases its writer claims in the same transaction that
terminalizes the Run. Diagnostics expose a bounded, content-free operational
snapshot with Work Item/Artifact status and claim classifications, never claim
tokens or generated content. The host's manual maintenance endpoint is fixed
to lease cleanup only and cannot enable terminal-content retention.

## Reusable persistence ports

- `RunRepository`: atomic Run lifecycle and outbox events.
- `ExecutionLeaseStore`: execution ownership, heartbeat, and durable cancel.
- `DelegationRepository`: parent/child task queue and result propagation.
- `CheckpointStore`: durable, cursor-based Run snapshots.
- `ApprovalGateway`: one-shot human decisions.
- `ToolIdempotencyGateway`: replay-safe side-effecting tool execution.
- `ContextCompressionHook`: application-owned reduction policy; Core only
  invokes it and validates its result.
- `ArtifactRepository`: recoverable large-result batches with atomic revision,
  ordering, and idempotency receipts.
- `ArtifactValidator`: domain-supplied batch/final validation without putting
  product structure into Core.
- `WorkItemRepository`: durable task identity, lifecycle, and append-only Run
  relationships.
- `ArtifactClaimRepository`: exclusive, expiring writer ownership for Work
  Item-scoped Artifacts.
- `ArtifactMaintenanceRepository`: atomic lease cleanup, content-free
  consistency reporting, and explicitly configured terminal retention.

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

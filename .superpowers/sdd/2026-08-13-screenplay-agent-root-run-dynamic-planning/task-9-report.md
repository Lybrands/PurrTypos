# Task 9 report: retire independent Screenplay Planner identity

Status: PASS

## Outcome

- Deleted `application/screenplay_agent_planner.py`; production and tests import the
  resolver from `application.screenplay_task_resolver` directly.
- Removed the final independent Planner vocabulary and guarded against restoring
  `ModelScreenplayIntentPlanner`, `screenplay_intent_planning`, or its prompt constants.
- New Screenplay Conversation snapshots serialize only `rootRunId`. Repository,
  finalizer, continuation, cleanup, and page/controller semantics now use Root naming.
- The SQLite `planner_run_id` column remains unchanged as a physical compatibility
  adapter. A boundary test rejects application identifiers with that name.
- Protocol v1 `plannerRunId` is accepted only by one frontend input decoder, converted
  immediately to `rootRunId`, and removed from the decoded object. Protocol v2 never
  serializes the alias.

## Canonical replay acceptance

- Added a complete shared-reducer fixture covering Root todos, Root tool execution,
  context compression, explicit Child delegation with Child tool and output, checkpoint
  plan revision, authoritative todo completion summaries, and Root final. The frontend
  fixture contains only public canonical chunks; it does not fabricate Candidate or
  Recipe protocol events.
- Live chunk handling and replay converge on the same Root-owned state and final answer.
- Recipe unit titles and `plannerStepId` never enter the public Root plan.
- A real formal Screenplay run now proves Candidate authority end-to-end: the AI Part
  creates one Child Run, persists exactly one private `run.validated_result`, finalizes
  the Candidate Artifact, and records `screenplay.candidate.ready` in the business
  outbox. The private validated result maps to no SSE chunk and neither Candidate body
  nor outbox event enters the Agent public journal.
- Extended the backend persisted wire fixture to prove initial and checkpoint
  `run.todos_updated`, terminal `run.todo_updated` summaries, LongTask progress, and
  compaction preserve canonical sequence and replay cursors. Raw `long_task.progress`
  is persisted with full durable state as PRIVATE/diagnostic and is absent from public
  live SSE and replay, so Recipe titles, unit IDs, and `plannerStepId` cannot leak.
- Existing real composed Child lifecycle coverage continues to prove persisted
  delegation and Child output replay through the same canonical journal.

## Compatibility and boundaries

- Runtime cleanup reads the historical SQLite column, normalizes it immediately to
  `root_run_id`, and keeps owner-scoped cleanup semantics unchanged.
- Added the protocol v1 input/v2 output contract to
  `docs/design/screenplay-agent-api-v2.md`.
- Added ratchets for deleted Planner code, storage-only `planner_run_id`, and the single
- Expanded those ratchets across every backend production Python surface and all
  frontend TS/TSX/JS/JSX/CJS/MJS sources with exact physical-SQL and v1-fixture
  allowlists.

## Verification

- Task9 backend targets (rewrite, canonical wire, runtime cleanup, boundaries, routes,
  durable service): passed.
- Task9 frontend Conversation/reducer targets: 29 passed.
- TypeScript typecheck: passed.
- Review-focused Task9 backend suites: 304 passed; the complete PurrA package suite:
  92 passed; the architecture boundary gate: 56 passed.
- Complete frontend unit suite: 346 passed; TypeScript typecheck: passed.
- Full `check:agent-refactor`: 1846 backend passed; frontend unit/typecheck and all
  intermediate gates passed. Five live Provider E2E cases remain skipped for missing
  credentials and are release blockers, not passes.

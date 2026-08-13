# Final review correction report

## Status and scope

PASS for the focused correction wave. Implementation commit:
`a2dc968 fix(agent): authorize root resume from persisted binding`.

The change stays on the common prerequisite branch. It does not touch
`packages/purra/**`, migrations, schema, or repository contracts. It adds no
Writing/Screenplay branch to the shared frontend, no second UI ownership state,
and no new public plan source. The product factory remains exactly Writing plus
Screenplay.

## Important — paused Root continuation requires authoritative ownership

### RED

Production-shaped handler/replay regression:

```bash
node --test src/agent-runtime/chunkReplay.test.cjs
```

Observed result: exit 1, 10 passed and 1 failed. After Root `run-a` paused and
settled, a direct `foreign-child` lifecycle envelope with
`turnId=different-turn` but the live request's `streamId` changed
`agentRunId` to `foreign-child` (expected `run-a`). The test also protects
`canonicalOutput.runId`, Root response text, and the paused Root plan.

Live request binding regression:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_writing_chat_request_routes.py -q \
  -k post_claim_binds_one_run
```

Observed result: exit 1. The claimed live stream's first frame was the Run
event; the expected bound `requestReceipt` frame was absent.

Durable recovery binding regression:

```bash
node --test src/services/durableAgentStreamRecovery.test.ts
```

Observed result: exit 1, 8 passed and 2 failed. Both exact-receipt recovery and
accepted-then-bound recovery replayed the Run journal/terminal without first
emitting the persisted Root request receipt.

### GREEN

The existing Writing request receipt is now the live authoritative Root
contract:

- after `WritingChatRequestLifecycle` binds the immutable request to a Run, the
  route emits that persisted receipt before the first canonical frame;
- durable recovery emits the same receipt before recovered journal frames;
- the shared reducer stores `requestReceipt.runId` in the existing
  `conversationRunId` binding and permits a paused Root switch only when the
  new lifecycle Run equals that binding;
- persisted replay supplies `run.runId` through `AgentChunkTurnSeed.rootRunId`;
  Book uses the loaded snapshot Run and Screenplay uses the persisted Turn's
  `plannerRunId`;
- transport `streamId` remains correlation/filtering only and is never resume
  authority;
- backend transport recovery/error bookkeeping accepts canonical sequence,
  visible output, and error ownership only from the receipt-bound Root, so a
  foreign child cannot redirect the recovery cursor or diagnostic Run ID.

True Root continuation remains covered in both forms: a live request receipt
binds the resumed Run, while a persisted replay seed can update to the resumed
snapshot Root. The live and replay projections are asserted to preserve the
same Root identity, response, and paused plan under the production-shaped
foreign envelope.

Focused GREEN results:

```text
node --test src/agent-runtime/chunkReplay.test.cjs             11 passed
node --test src/services/durableAgentStreamRecovery.test.ts    10 passed
test_writing_chat_request_routes.py                            16 passed
```

Files:

- `backend/application/writing_chat_request_lifecycle.py`
- `backend/routers/ai.py`
- `backend/tests/test_writing_chat_request_routes.py`
- `src/agent-runtime/chunkHandlers/canonical.ts`
- `src/agent-runtime/chunkHandlers/index.ts`
- `src/agent-runtime/chunkReplay.ts`
- `src/agent-runtime/chunkReplay.test.cjs`
- `src/services/backendApi.ts`
- `src/services/durableAgentStreamRecovery.ts`
- `src/services/durableAgentStreamRecovery.test.ts`
- `src/Workspace/AiPanel/hooks/useChatSubmit.ts`
- `src/Workspace/AiPanel/bookConversationHydration.ts`
- `src/ScreenplayAgentPage/index.tsx`

## Minor — legacy Runs recover product profile without a default

### RED

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_run_queries.py -q \
  -k 'legacy_writing_run_without_profile_attributes or persisted_profile_conflict_between_attributes'
```

Observed result: exit 1, 2 failed. A legacy Writing Run with
`binding_namespace=writing.chat.request` and empty attributes rejected a valid
Writing delegation. A Run whose Writing binding conflicted with Screenplay
attributes returned only the generic no-delegation response instead of failing
closed on the persisted identity conflict.

### GREEN

The generic Run routes now infer only a domain namespace from existing durable
discriminators:

- `writing.chat.request` maps to the Writing domain;
- `screenplay.agent.*` maps to the Screenplay domain;
- legacy session ownership uses `scope`, `chapter_id`, `book_id`, and
  `screenplay_project_id` when binding attributes/namespaces are absent;
- attribute, binding, session, or parent-lineage disagreements raise a conflict
  instead of selecting a registry;
- no registry order or generic Writing default is used.

Legacy Writing delegation and localized role titles work again. Legacy
Screenplay parents with empty profile attributes still have no Writing roles
and reject Writing delegation.

Focused GREEN results:

```text
backend/tests/test_agent_run_queries.py                 25 passed
backend/tests/test_agent_delegation_service.py           9 passed
backend/tests/test_agent_run_service_binding_cleanup.py  4 passed
```

Files:

- `backend/routers/ai.py`
- `backend/tests/test_agent_run_queries.py`

No schema, migration, Run-store query shape, or repository change was needed.

## Consolidated verification

Frontend directly affected suites:

```bash
npm run test:unit
npm run test:proposal-projection
```

Results: 327 passed and 29 passed respectively, with zero failures.

Backend directly affected suites:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_run_queries.py -q
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_writing_chat_request_routes.py -q
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_delegation_service.py -q
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_run_service_binding_cleanup.py -q
```

Results: 25, 16, 9, and 4 passed respectively; zero failures.

Typecheck:

```bash
npm run typecheck
```

Result: exit 0.

Architecture boundary gate:

```bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python \
  npm run check:agent-refactor-boundaries
```

Result: exit 0, 53 passed in 0.90s. An initial invocation without
`PURRTYPOS_PYTHON` exited 1 because the worktree runner could not discover a
pytest interpreter; rerunning with the repository virtualenv passed.

Formatting:

```bash
git diff --check
```

Result: exit 0.

Per the correction brief, the full `check:agent-refactor` gate was not run.

## Self-review

- Foreign canonical events are rejected before canonical reduction, so they
  cannot change per-Run sequence state, canonical output, response, task plan,
  lifecycle, persistence identity, or cancellation identity.
- Neither transport `streamId` nor event-authored `turnId` can authorize a Root
  switch. Only a backend/persisted request receipt or a persisted snapshot Root
  binding can do so.
- Live receipt emission precedes the first canonical frame, and recovery receipt
  emission precedes recovered journal frames, preserving live/replay ordering.
- Existing `conversationRunId` is reused; no duplicate UI ownership state or
  business-specific shared reducer branch was introduced.
- Legacy profile inference is evidence-based and conflict-checked. It never
  indexes the profile registry or assumes Writing when durable evidence is
  absent.
- Root todo events remain the sole public task-plan producer.
- The implementation commit contains only the 14 focused production/test files
  listed above; no unrelated user changes were present.

## Concerns and release blockers

No scoped code blocker remains. The npm mirror-key warnings and Node typeless
module warnings are pre-existing and non-failing.

The credential-skipped real-provider Screenplay E2E cases documented by the
earlier verification remain RELEASE BLOCKERS, not passes. This correction wave
did not run the full gate or claim live-provider coverage.

---

# Residual Root terminal ownership correction

## Status and scope

PASS for the focused residual correction wave. Implementation commit:
`156c2ed fix(agent): reject foreign terminal envelopes`.

This wave changes only shared frontend/runtime transport behavior and focused
tests. It does not touch `packages/purra/**`, backend production code,
migrations, schema, repositories, or later Novel/Screenplay business plans. It
adds no Writing/Screenplay branch and no second UI ownership state.

## Important — terminal envelopes must obey the authoritative Root binding

### RED

The initial reducer matrix was added before the production filter:

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts
```

Observed result: exit 1, 16 passed and 7 failed. A bound Root accepted foreign
`failed`, `blocked`, `done`, and `canceled` Run results. The foreign outcomes
settled the Root, foreign final text replaced the Root response, and cancellation
changed the Root plan/terminal state. An unbound non-null `requestResult` also
failed to establish Root identity, while conflicting bound canceled/rejected
request results settled the existing Root.

Production-shaped transport and host-control regressions were then exercised:

```bash
node --test src/Workspace/AiPanel/hooks/useChatSubmit.behavior.test.mjs
```

Observed result: exit 1, 16 passed and 2 failed. `backendApi` treated a foreign
failed Run as the stream terminal, created an error report, and skipped Root
snapshot recovery. `useChatSubmit` persisted and unsubscribed the Root as failed
before its own terminal arrived.

The same-request pre-Run rule was added as a separate RED matrix:

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts
```

Observed result: exit 1, 23 passed and 2 failed. Foreign `runId=null`
`requestResult` canceled/rejected envelopes settled the current request even
though their `requestId` did not match the runtime turn/request id.

The no-receipt compatibility ownership closure was also RED before its fix:

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts
```

Observed result: exit 1, 25 passed and 1 failed. The first compatible non-null
Root `runResult` settled successfully but did not bind `conversationRunId`, so a
late child terminal could still change the accumulator identity.

Finally, the fail-closed check was tested against a mixed envelope carrying a
Root-looking canonical delta plus a conflicting non-null `requestResult`:

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts
node --test src/Workspace/AiPanel/hooks/useChatSubmit.behavior.test.mjs
```

Observed results: reducer exit 1 with 26 passed and 1 failed; transport/host exit
1 with 16 passed and 2 failed. The late terminal handlers rejected settlement,
but canonical projection/listener delivery had already exposed the mixed
envelope. This proved the ownership check had to run before all envelope
projection and transport bookkeeping.

### GREEN

One small shared contract now distinguishes two responsibilities:

- `resolveRootRunBinding` accepts the first authoritative non-terminal
  `requestReceipt.runId`, accepts exact repeats, and refuses later rebinding;
- `resolveTerminalRootOwnership` accepts a non-null `runResult` or
  `requestResult` only when it establishes an unbound Root or matches the
  existing Root;
- a `runId=null` request result is accepted only before any Run exists and only
  when its `requestId` matches the current request; a context without a known
  request id retains the legacy direct-handler compatibility path;
- a legacy Run result without a Run id remains compatible only before any Root
  is bound or observed;
- an accepted terminal Run/result establishes `conversationRunId` so late
  terminals cannot mutate identity after settlement.

The shared reducer preflights terminal ownership before request receipt mutation,
canonical reduction, host projection, or generic terminal handling. `backendApi`
does the same before cursor/error/visible-output bookkeeping, error-report
creation, `receivedTerminalChunk`, debug recording, or listener delivery.
`useChatSubmit` checks the same contract before durable stop/terminal control,
proposal refresh, persistence, or reducer dispatch. Thus a conflicting non-null
request result is ignored as an entire envelope.

Live and replay remain equivalent because `AgentChunkReplay` uses the same
preflight reducer. The production-shaped paused Root replay now includes foreign
failed/done terminals and preserves Root identity, canonical output, response,
and paused plan. A true Root terminal still settles normally.

Focused GREEN results:

```text
node --experimental-strip-types --test \
  src/agent-runtime/chunkHandlers.test.ts \
  src/agent-runtime/chunkReplay.test.cjs                    38 passed
node --test \
  src/Workspace/AiPanel/hooks/useChatSubmit.behavior.test.mjs
                                                            18 passed
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
                                                            14 passed
```

The `modelRuntime` suite intentionally preserved the legacy no-Run-id long-task
terminal path. During iteration it first failed 13 passed / 1 failed, catching
an over-strict helper revision; the final rule limits that compatibility to the
unbound state and the suite is green.

Files:

- `src/agent-runtime/rootOwnership.ts`
- `src/agent-runtime/chunkHandlers/index.ts`
- `src/agent-runtime/chunkHandlers/terminal.ts`
- `src/agent-runtime/chunkHandlers.test.ts`
- `src/agent-runtime/chunkReplay.test.cjs`
- `src/services/backendApi.ts`
- `src/Workspace/AiPanel/hooks/useChatSubmit.ts`
- `src/Workspace/AiPanel/hooks/useChatSubmit.behavior.test.mjs`

## Consolidated verification

Frontend directly affected suites:

```bash
npm run test:unit
npm run test:proposal-projection
```

Final results: 342 passed and 29 passed respectively; zero failures.

Backend compatibility suites (no backend production files changed):

```bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python \
  node scripts/run-backend-tests.cjs \
  backend/tests/test_agent_run_queries.py \
  backend/tests/test_agent_delegation_service.py \
  backend/tests/test_writing_chat_request_routes.py
```

Result: exit 0, 50 passed in 5.25s. The first invocation without
`PURRTYPOS_PYTHON` exited before test collection because the worktree runner
could not discover pytest; the explicit repository virtualenv invocation passed.

Typecheck:

```bash
npm run typecheck
```

Result: exit 0.

Architecture boundary gate:

```bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python \
  npm run check:agent-refactor-boundaries
```

Result: exit 0, 53 passed in 1.29s. As above, an initial invocation without the
explicit Python path stopped before collection; the corrected command passed.

Formatting:

```bash
git diff --check
```

Result: exit 0.

Per the correction brief, the full `check:agent-refactor` gate was not run.

## Self-review

- Foreign terminal envelopes are rejected before any canonical, persistence,
  cancellation, proposal, recovery-cursor, diagnostic, or settlement effect.
- `requestReceipt` remains a non-terminal authoritative Root binding;
  `requestResult` remains terminal and follows the explicit null/non-null matrix.
- The helper contains no transport, UI, Writing, or Screenplay state. Existing
  `conversationRunId`, request id, and persisted receipt/root bindings are reused.
- Plain generic `done`/`error` and the unbound legacy no-Run-id Ask/long-task
  paths remain compatible; once a Root exists, unidentified terminals fail
  closed.
- Root todo events remain the only public plan source. No product profile or
  router split was introduced.
- The implementation commit contains only the eight focused files listed above.

## Concerns and release blockers

No scoped code blocker remains. npm mirror-key warnings, Node typeless-module
warnings, React test warnings, and the need to point worktree backend commands at
the repository virtualenv are pre-existing and non-failing.

The credential-skipped real-provider Screenplay E2E cases remain RELEASE
BLOCKERS, not passes. This wave did not run the full gate or claim live-provider
coverage.

## Minor follow-up — null-Run request results require an exact request id

Implementation commit: `dc73fe9 fix(agent): require exact pre-run request ownership`.

RED:

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts
```

Result: exit 1, 27 passed and 2 failed. With no current request id, foreign
pre-Run canceled and rejected results incorrectly settled as canceled/failed.

The null-Run branch now accepts a request result only when both the expected and
result request ids are non-empty and exactly equal. Missing current identity and
non-matching identity both fail closed; existing legitimate tests explicitly
provide the matching request id.

GREEN verification:

```text
chunkHandlers.test.ts                              29 passed
chunkReplay.test.cjs                               11 passed
useChatSubmit.behavior.test.mjs                    18 passed
npm run typecheck                                  exit 0
git diff --check                                   exit 0
```

Files: `src/agent-runtime/rootOwnership.ts` and
`src/agent-runtime/chunkHandlers.test.ts`. No compatibility fallback, backend,
schema, repository, product branch, or UI ownership state was added.

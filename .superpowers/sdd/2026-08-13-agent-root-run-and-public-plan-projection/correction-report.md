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

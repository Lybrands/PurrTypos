# Agent Conversation Semantic Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove host-authored canned dialogue from Agent conversations, publish a real model-authored final answer only after all work and candidate publication succeed, and merge uninterrupted operational activity into one deterministic collapsible group.

**Architecture:** The backend owns semantic message boundaries and durable lifecycle ordering. Model-authored public narrative remains narrative, operational events remain structured operations, and the final answer is generated in a dedicated durable unit whose checkpoint is published to the conversation only after candidate publication succeeds. The frontend projects those semantics deterministically for both live SSE and replay; it never infers meaning from localized strings and never synthesizes success prose.

**Tech Stack:** Python 3.12, FastAPI application services, durable Agent task recipes/checkpoints, Pydantic/JSON model calls, React, TypeScript, Vitest/Node tests, pytest.

## Global Constraints

- Do not filter or classify messages by matching Chinese copy in the frontend.
- Do not add host-authored conversational prefixes, opening acknowledgements, progress narration, or success conclusions.
- Preserve model-authored public execution summaries when thinking/progress display is enabled.
- The final answer must be model-authored, bounded, contain only a concise completion summary and deliverable/revision guidance, and must not include full screenplay bodies, structured JSON, private reasoning, or raw tool traces.
- Do not expose the final answer before candidate publication succeeds. A publish retry must reuse the already persisted final-answer checkpoint rather than call the model again.
- Run boundaries, unit boundaries, stages, progress updates, reasoning updates, and SSE packet boundaries do not split operational groups. Only visible model narrative or the final answer does.
- Use the same projection and grouping functions for live updates and persisted replay.
- Preserve unrelated uncommitted work. Do not commit unless the user explicitly requests it.

---

## Task 1: Remove host-authored conversational copy at the source

**Files:**

- Modify: `backend/application/screenplay_agent_planner.py`
- Modify: `backend/application/screenplay_structured_call.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/screenplay_agent_stream.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_screenplay_structured_call.py` (or the closest existing structured-call test module)
- Modify: `backend/tests/test_agent_refactor_boundaries.py`

- [x] **Step 1: Write failing tests for semantic ownership**

Add assertions that a model-provided `executionSummary` is emitted verbatim as public narrative, without `请求理解：`, role names, episode labels, or other host prefixes. Add a structured-call test proving an empty non-answer completion does not become `结构化剧本任务已完成。`. Add an architecture gate forbidding the rejected canned success/opening strings in production Agent code.

- [x] **Step 2: Run the focused tests and confirm the current behavior fails**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_structured_call.py backend/tests/test_agent_refactor_boundaries.py -q
```

Expected: failures identify prefixed `executionSummary` values and fixed completion fallbacks.

- [x] **Step 3: Remove copy injection without removing semantic events**

Change planner/structured-call progress handling so public narrative is emitted only when the model supplied non-empty public text. Remove `reply or "结构化剧本任务已完成。"`. Remove fixed final-response metadata from `_unit_result`. Keep structured operation labels and machine-readable status fields, but prevent them from being projected as assistant prose. Where existing projection helpers prepend role or episode copy to `executionSummary`, pass the normalized model text through unchanged.

- [x] **Step 4: Make task-start metadata non-conversational**

Ensure `agentRunStarted` and plan events contain identifiers/goals/todos needed by the UI without injecting a fixed assistant utterance such as `剧本创作任务`. If a plan title is optional, omit it; if required by the wire contract, use non-dialogue structural metadata and keep it out of narrative projection.

- [x] **Step 5: Re-run focused tests**

Run the Step 2 command and confirm all tests pass.

## Task 2: Add a durable model-authored final-response unit

**Files:**

- Modify: `backend/domains/screenplay_agent/recipe_compiler.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/output_budget_policies.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`

- [x] **Step 1: Write failing recipe and executor tests**

Assert the compiled order is:

```text
collect-evidence -> generate-candidate -> validate-candidate
                 -> compose-final-response -> publish-candidate
```

Assert `publish-candidate` depends on `compose-final-response`, and that the compose unit receives only controlled facts: original request, scope/target labels, validated candidate titles/counts, public execution summaries, and preservation constraints. Explicitly assert it receives no `contentText`, `contentJson`, episode body, tool transcript, private reasoning, or raw provider output.

Add executor tests for:

- a non-empty natural-language `finalResponse` checkpoint;
- bounded JSON parsing/validation for the model call;
- compose failure pausing the task;
- publish retry reusing the compose checkpoint without a second compose model call.

- [x] **Step 2: Run focused tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_rewrite.py -q
```

- [x] **Step 3: Compile recipe version 3**

Add one `compose_final_response` unit after all validation units and before `publish_candidate`. Give it a stable ID such as `compose-final-response`, a read-only model effect, durable output evidence, and dependencies on every validated candidate. Make publication depend on the compose unit so successful publication proves the final response already exists.

- [x] **Step 4: Implement the bounded compose call**

Add `_compose_final_response(...)` to `ScreenplayTaskModelCalls.execute`. Build a small sanitized fact object from validated outputs; retain only user-visible target names, candidate/revision titles, episode numbers/counts, and model-authored public summaries. Invoke the structured-call service with a dedicated small output budget and a schema equivalent to:

```json
{"finalResponse":"2-4 sentences of natural-language completion summary"}
```

Validate non-empty text and a conservative maximum length. The prompt must require a concise result summary plus where the user can inspect/edit the generated candidate, and forbid full document content, JSON, implementation details, invented version IDs, and canned exact wording.

- [x] **Step 5: Keep compose output independent from candidate publication payloads**

Ensure `_publish(...)` ignores compose output when assembling candidate documents. Ensure `_unit_result(...)` does not elevate compose text into task metadata early. The durable checkpoint is authoritative until the application service publishes it after the candidate revision exists.

- [x] **Step 6: Re-run focused tests**

Run the Step 2 command and confirm all tests pass.

## Task 3: Publish the final answer exactly once, after candidate publication

**Files:**

- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`
- Modify: `backend/tests/test_screenplay_agent_routes.py`
- Modify: `backend/tests/test_ai_composed_sse_wire_contract.py`
- Modify: `src/ScreenplayAgentPage/conversationState.ts`
- Modify: `src/ScreenplayAgentPage/conversationState.test.ts`

- [x] **Step 1: Write failing lifecycle and replay tests**

Cover these invariants:

- no `assistantMessageDelta`/final answer is visible while compose has succeeded but publish has not;
- after publication succeeds, the service reads `compose-final-response.finalResponse`, persists it as the Turn assistant content, and emits it once;
- reconnect/replay reproduces the same final answer without duplicate text;
- completed tasks with missing assistant content do not synthesize `剧本任务已完成...` in the frontend;
- a child/structured Run completion cannot become the parent Turn final answer.

- [x] **Step 2: Run focused backend and frontend tests and confirm they fail**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_routes.py backend/tests/test_ai_composed_sse_wire_contract.py -q
npx vitest run src/ScreenplayAgentPage/conversationState.test.ts
```

- [x] **Step 3: Make publication the visibility boundary**

After the dispatcher reports completion, load both durable checkpoints by stable unit ID. Require a valid published revision and a valid compose result. Only then call `complete_task(..., assistant_content=final_response)`. Treat a missing/invalid compose checkpoint as an incomplete task rather than substituting host prose.

- [x] **Step 4: Remove frontend success synthesis**

Delete the fixed success fallback from `conversationState.ts`. Project only persisted/model-authored assistant content. Preserve explicit system errors and paused-task recovery state because those are system status, not simulated assistant speech.

- [x] **Step 5: Re-run focused tests**

Run the Step 2 command and confirm all tests pass.

## Task 4: Deterministically merge uninterrupted operations in the UI

**Files:**

- Modify: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`
- Modify: `src/agent-runtime/chunkReplay.test.cjs`

- [x] **Step 1: Write failing timeline tests**

Add pure projection cases for:

1. operations from multiple segments/runs/units with no visible narrative between them become one operation group;
2. progress, reasoning, stage, and SSE packet boundaries do not split the group;
3. real model commentary splits `operations A` and `operations B` into two groups with the commentary between them;
4. the final answer closes the work log and renders outside it;
5. the same persisted chunks produce the same grouping on replay as during live accumulation.

- [x] **Step 2: Run focused frontend tests and confirm they fail**

Run:

```bash
npx vitest run src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/agent-runtime/chunkReplay.test.cjs
```

- [x] **Step 3: Normalize timeline parts by semantics**

Keep three presentation roles: narrative, operation, and final answer. Treat tool calls/results and other displayable non-narrative work records as operations. Accumulate adjacent operations across technical boundaries. Flush the accumulator only when a visible narrative part or final-answer part is encountered. Do not classify by copy, provider, model, run ID, unit ID, or current thinking-mode setting.

- [x] **Step 4: Render one collapsible operation group per uninterrupted sequence**

Update `AssistantMessageBody` so each normalized operation group owns one disclosure control. Render real narrative as ordinary assistant work commentary between groups. Keep the final answer outside the execution disclosure. Preserve accessibility labels, stable keys, live-update behavior, and empty-state behavior.

- [x] **Step 5: Re-run focused frontend tests**

Run the Step 2 command and confirm all tests pass.

## Task 5: Regression gates and full verification

**Files:**

- Modify: `backend/tests/test_agent_refactor_boundaries.py`
- Modify: `package.json` only if an existing Agent refactor check needs the new tests included
- Verify: all files changed in Tasks 1-4

- [x] **Step 1: Add source and lifecycle gates**

Gate against the rejected fixed dialogue strings in production Agent conversation paths. Gate the recipe dependency `validate -> compose -> publish`, prohibit final response publication before revision publication, and retain the existing rule that child Run finals cannot complete the parent Turn.

- [x] **Step 2: Run deterministic backend verification**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

- [x] **Step 3: Run deterministic frontend verification**

Run:

```bash
npm test -- --run
npm run typecheck
npm run check:agent-refactor
```

If the repository uses a different full frontend test script, inspect `package.json` and run the canonical equivalent without weakening coverage.

- [x] **Step 4: Inspect the final diff and status**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Confirm only intended files changed, unrelated uncommitted work remains intact, no fixed assistant copy remains on the success path, and no service started for verification is left running.

- [x] **Step 5: Report evidence and remaining limits**

Report the exact tests and checks that passed. Distinguish deterministic coverage from any live-provider behavior not exercised. Do not claim the fix is complete if any required lifecycle, replay, typecheck, or architecture gate fails.

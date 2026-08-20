# Conversation Progress and Plan Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make send-time viewport pinning immediate, remove the active-header ellipsis, hide validation Operations while retaining compaction/delegation/tools, and show semantic screenplay task titles.

**Architecture:** Keep canonical events and durable recipes authoritative. Change only the shared UI projection for visibility and scroll intent, while the screenplay Manifest compiler supplies stable business display titles that the generic LongTask dispatcher transparently forwards.

**Tech Stack:** React, TypeScript, react-virtuoso, Node test runner, Python, pytest, SQLite-backed PurrA LongTask events.

## Global Constraints

- Do not expose private model reasoning.
- Keep commentary, tool calls, context compaction, and internal delegation visible.
- Hide only model lifecycle and validation Operations from the user timeline.
- Keep durable dependency, retry, and parallelism ownership in the screenplay host compiler, not the LLM or frontend.
- Do not add dependencies or duplicate screenplay title rules in shared frontend code.

---

### Task 1: Active log title and canonical Operation visibility

**Files:**
- Modify: `src/components/AgentConversation/ExecutionLog/index.tsx`
- Modify: `src/components/AgentConversation/Panel.behavior.test.mjs`
- Modify: `src/components/AgentConversation/AssistantOutput/timeline.ts`
- Modify: `src/components/AgentConversation/AssistantOutput/timeline.test.ts`

**Interfaces:**
- Consumes: `CanonicalOperation.kind` and `ExecutionLogProps.active`.
- Produces: `buildAssistantTimeline()` output without `validation` Operations; active ExecutionLog markup without `.a-blink-dots` in its header.

- [ ] **Step 1: Add failing visibility and markup assertions**

Add a canonical `validation` Operation beside the existing model/tool fixtures and assert that only the tool Operation remains. Render an active `ExecutionLog` directly in `Panel.behavior.test.mjs` and assert its text is `正在进行· 0秒` without `...` or `.a-blink-dots`.

- [ ] **Step 2: Run tests and verify the expected failures**

Run `node --experimental-strip-types --test src/components/AgentConversation/AssistantOutput/timeline.test.ts src/components/AgentConversation/Panel.behavior.test.mjs`.

Expected: validation remains in the timeline and the active header still contains the dots node.

- [ ] **Step 3: Apply the minimal projection changes**

In `buildAssistantTimeline()`, skip `operation.kind === "validation"` at the same canonical projection boundary that already skips `model`. Remove the two active `.a-blink-dots` render branches from `ExecutionLog`; do not alter compaction or delegation rendering.

- [ ] **Step 4: Re-run the two tests**

Run the Step 2 command. Expected: both files pass.

- [ ] **Step 5: Commit the isolated change**

Stage the four Task 1 files and commit with message `fix(agent): simplify visible execution operations`.

### Task 2: Immediate user-message viewport pinning

**Files:**
- Modify: `src/components/AgentConversation/scrollFollowPolicy.ts`
- Modify: `src/components/AgentConversation/scrollFollowPolicy.test.ts`
- Modify: `src/components/AgentConversation/ConversationViewport/index.tsx`

**Interfaces:**
- Consumes: ordered `AgentConversationMessage[]` and the previous `LiveTurnCursor`.
- Produces: `advanceLiveTurnCursor(previous, messages): LiveTurnObservation`, where a newly appended stable user Turn returns its own `anchorIndex` immediately and later Assistant updates retain the same cursor key.

- [ ] **Step 1: Rewrite policy tests around the send event**

Assert that appending `userTurn2` after a completed first Turn returns cursor `user-client:turn-2` and `anchorIndex: 2`. Then append `assistantTurn2` and assert the cursor stays unchanged with no anchor. Keep coverage that initial/restored messages only initialize the cursor and edit/resubmit pins the replacement user message.

- [ ] **Step 2: Run the policy test and verify failure**

Run `node --experimental-strip-types --test src/components/AgentConversation/scrollFollowPolicy.test.ts`.

Expected: appending a user without an Assistant does not currently return an anchor.

- [ ] **Step 3: Make the latest stable user Turn authoritative**

Replace Assistant-first discovery with a latest-user observation. Use `clientTurnId`, then `conversationId`, then `sentAt` as the stable key. Preserve the initial-observation guard and keep `ConversationViewport`'s existing one-frame `scrollToIndex({ align: 'start' })` execution path.

- [ ] **Step 4: Run scroll policy and mounted viewport behavior tests**

Run `node --experimental-strip-types --test src/components/AgentConversation/scrollFollowPolicy.test.ts src/components/AgentConversation/scrollFollowEvents.behavior.test.mjs src/components/AgentConversation/viewportSession.test.ts`.

Expected: all pass; Assistant/Run updates do not repin the same Turn.

- [ ] **Step 5: Commit the isolated change**

Stage the three Task 2 files and commit with message `fix(agent): pin new user turns immediately`.

### Task 3: Semantic screenplay LongTask titles

**Files:**
- Modify: `backend/application/screenplay_manifest_compiler.py`
- Modify: `packages/purra/src/purra/long_tasks/dispatcher.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_purra_durable_dispatcher.py`
- Modify: `src/agent-runtime/chunkReplay.test.cjs`

**Interfaces:**
- Produces: recipe-step metadata field `displayTitle: str`.
- Produces: `long_task.progress.units[].title` when `displayTitle` is present.
- Consumes: frontend `normalizeLongTaskStep()` already prioritizes `value.title` before its type fallback.

- [ ] **Step 1: Add failing compiler and dispatcher tests**

For a `sourceAnalysis` Manifest, assert ordered recipe display titles: `读取原作内容`, `分析人物`, `梳理故事`, `分析世界观`, `提炼主题`, `评估改编风险`, `检查分析结果`, `生成回复`. In the durable dispatcher test, provide `metadata={"displayTitle": "分析人物"}` and assert the emitted unit contains `"title": "分析人物"`.

- [ ] **Step 2: Run the backend tests and verify failure**

Run `.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_purra_durable_dispatcher.py -q`.

Expected: recipe metadata and progress events do not yet contain display titles.

- [ ] **Step 3: Add host-owned semantic titles and transparent forwarding**

Define source-analysis section titles in `screenplay_manifest_compiler.py`; attach `displayTitle` to every compiled Part recipe step, using stable fallbacks for evidence, validation, and final response. Bump the recipe version because persisted recipe metadata changes. In `_RecipeUnitRunner.emit_progress()`, include `title` only when the unit metadata contains a non-empty display title.

- [ ] **Step 4: Add frontend replay coverage**

Extend the LongTask progress fixture in `chunkReplay.test.cjs` with distinct `title` fields and assert the normalized task plan preserves them rather than returning the generic type fallback.

- [ ] **Step 5: Run backend and frontend projection tests**

Run `.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_purra_durable_dispatcher.py -q` and `node --experimental-strip-types --test src/agent-runtime/chunkReplay.test.cjs src/components/AgentConversation/TaskProgress/localization.test.ts`.

Expected: semantic titles survive compiler, durable event, replay, and localization.

- [ ] **Step 6: Commit the isolated change**

Stage the six Task 3 files and commit with message `fix(screenplay): expose semantic task progress titles`.

### Task 4: Full verification

**Files:**
- Verify only; do not add production behavior.

**Interfaces:**
- Consumes all changes from Tasks 1–3.
- Produces fresh verification evidence for delivery.

- [ ] **Step 1: Run formatting and diff validation**

Run `git diff --check`. Expected: exit code 0.

- [ ] **Step 2: Run the complete Agent architecture gate**

Run `npm run check:agent-refactor`.

Expected: architecture, model contracts, screenplay acceptance, TypeScript, frontend unit, proposal projection, screenplay lifecycle, and backend suites all pass; credential-skipped real-provider tests remain reported as release blockers rather than passes.

- [ ] **Step 3: Inspect the final diff against the acceptance criteria**

Confirm the diff contains no new dependencies, no frontend screenplay title mapping, no removal of compaction/delegation, and no changes to canonical event persistence.

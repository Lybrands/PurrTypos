# Execution Status Capsule Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the task-progress capsule above the composer while keeping task-step progress out of the in-conversation execution panel.

**Architecture:** Keep canonical LongTask progress projected into the existing `AiTaskPlan` read model, but let the plan's own active status keep the composer capsule alive when the Conversation Snapshot lags. Keep the execution panel as an operation log: collapsed while active, with only a timer in its header; terminal history retains operation count and details.

**Tech Stack:** React, TypeScript, Node test runner, existing canonical chunk replay and Purr UI components.

## Global Constraints

- The capsule remains in `AgentComposer.floatingContent` above the input.
- Plans with fewer than three visible steps remain hidden, and implicit `Respond` remains excluded.
- `long_task.progress` must not create Assistant text or execution-timeline rows.
- The active execution panel header must not show step ordinal or completion counts.
- Do not restore legacy screenplay chunks/events stores or add host-authored transition copy.
- Preserve unrelated worktree changes.

---

### Task 1: Make the canonical plan own capsule activity

**Files:**
- Modify: `src/Workspace/AiPanel/taskPlanSelection.ts:72-81`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs:775-850`
- Verify existing: `src/Workspace/AiPanel/hooks/chunkHandlers/canonical.ts:139-285`
- Verify existing: `src/agent-runtime/chunkReplay.test.cjs:140-212`

**Interfaces:**
- Consumes: `getActiveTaskPlan(conversations: ChatMessage[], loading: boolean)` and the current Assistant message's `AiTaskPlan.status`.
- Produces: the same `AiTaskPlan | undefined` interface; active `planned`, `running`, and `paused` plans no longer require the auxiliary `loading` flag.

- [x] **Step 1: Change the selector regression test to describe the required behavior**

```js
conversations[3] = { ...conversations[3], taskPlan: currentPlan }
assert.equal(getActiveTaskPlan(conversations, false), currentPlan)
```

Keep the existing assertions that a short plan is hidden and that a completed plan disappears when `loading` becomes false.

- [x] **Step 2: Run the focused test and verify RED**

Run: `node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`

Expected: FAIL because the selector currently returns `undefined` whenever `loading` is false.

- [x] **Step 3: Implement the smallest selector ownership change**

```ts
export function getActiveTaskPlan(
  conversations: ChatMessage[],
  loading: boolean,
): AiTaskPlan | undefined {
  const currentMessage = conversations[conversations.length - 1];
  if (currentMessage?.role !== "assistant") return undefined;
  const plan = currentMessage.taskPlan;
  if (!plan || !shouldShowTaskPlan(plan)) return undefined;
  const planIsActive = plan.status === "planned"
    || plan.status === "running"
    || plan.status === "paused";
  return loading || planIsActive ? plan : undefined;
}
```

- [x] **Step 4: Run the selector and canonical replay tests and verify GREEN**

Run: `node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/agent-runtime/chunkReplay.test.cjs`

Expected: PASS, including the existing assertion that LongTask progress stays out of the work log.

- [x] **Step 5: Commit the capsule state fix with its canonical projection**

```bash
git add src/Workspace/AiPanel/taskPlanSelection.ts \
  src/Workspace/AiPanel/hooks/modelRuntime.test.cjs \
  src/Workspace/AiPanel/hooks/chunkHandlers/canonical.ts \
  src/agent-runtime/chunkReplay.test.cjs
git commit -m "fix(agent): restore composer task progress"
```

### Task 2: Remove task progress from the active execution panel

**Files:**
- Modify: `src/Workspace/AiPanel/components/WorkLog/index.tsx:10-120`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts:145-157`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx:20-370`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs:210-420`

**Interfaces:**
- Produces: `ExecutionPanelPresentation.title: string` from the existing shared `getExecutionPanelPresentation(...)` function.
- Changes: `getExecutionPanelPresentation(...).autoOpen` is always `false`; `WorkLog` accepts the computed `title` and no longer accepts task-progress counts.
- Preserves: terminal header `执行了 X 个步骤`, duration display, operation details, and manual disclosure.

- [x] **Step 1: Add failing presentation assertions**

Change the active execution-panel expectation to include a collapsed generic title:

```js
{
  visible: true,
  active: true,
  autoOpen: false,
  stepCount: 0,
  title: '正在进行',
}
```

Change the terminal execution-panel expectation to include `title: '执行了 1 个步骤'`.

- [x] **Step 2: Run the focused test and verify RED**

Run: `node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`

Expected: FAIL because active panels currently auto-open and the shared presentation has no generic title.

- [x] **Step 3: Implement the operation-log-only presentation**

Add `title` to `ExecutionPanelPresentation`, compute `正在进行` for every active panel and `执行了 X 个步骤` for terminal panels with operations, and return `autoOpen: false`. Pass that title to `WorkLog`; remove `stepCount`, `currentStepCount`, `completedStepCount`, `parallel`, and the now-unused call-site progress computation.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run: `node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/agent-runtime/chunkReplay.test.cjs`

Expected: PASS. The active panel is collapsed with `正在进行`, while terminal history still exposes operation count.

- [x] **Step 5: Run full frontend verification**

Run: `npm run typecheck && npm run test:unit`

Expected: typecheck succeeds and all frontend unit tests pass.

- [x] **Step 6: Inspect the current local UI without submitting a model request**

Reload the existing local app, open the screenplay conversation, and verify that terminal execution history still shows `执行了 X 个步骤 · 用时`. Use the automated tests as the authoritative active-state check so verification does not spend model tokens or mutate project data.

- [x] **Step 7: Commit the execution-panel boundary fix**

```bash
git add src/Workspace/AiPanel/components/WorkLog/index.tsx \
  src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts \
  src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx \
  src/Workspace/AiPanel/hooks/modelRuntime.test.cjs \
  docs/superpowers/plans/2026-08-12-execution-status-capsule-boundary.md
git commit -m "fix(ai-panel): separate task progress from execution history"
```

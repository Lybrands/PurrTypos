# Agent Execution Panel Restoration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore one foldable, timed execution-process panel before every Agent final answer without restoring nested operation disclosures.

**Architecture:** `AssistantMessageBody` will render one restored `WorkLog` for the whole Assistant turn. A pure timeline helper will decide panel visibility and aggregate visible step count, while `WorkLog` owns timing and disclosure state; tool, context, delegation, and commentary rows render directly inside it.

**Tech Stack:** React 18, TypeScript, Base project UI components, Node test runner, Sass.

## Global Constraints

- Each Assistant turn has at most one execution-process disclosure.
- The panel appears as soon as the turn starts, including before the first tool event.
- The panel auto-collapses when the final answer becomes visible and remains before that answer.
- Direct operation rows must not reintroduce `已操作`, `正在操作`, or `准备操作` copy.
- Cached tool rows do not contribute to the visible step count.
- A user's manual expand/collapse choice wins over later automatic state changes for that turn.
- Do not change the top task-progress capsule, Agent event protocol, persistence schema, or backend state machine.
- Do not add dependencies.

---

## File Structure

- `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts`: derive timeline order, visible execution step count, and execution-panel lifecycle presentation.
- `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx`: place the single execution panel before final-answer blocks and render process rows directly inside it.
- `src/Workspace/AiPanel/components/WorkLog/index.tsx`: own the one disclosure, timer, automatic lifecycle, stable manual state, error state, and completed summary.
- `src/Workspace/AiPanel/components/WorkLog/index.scss`: style only the outer execution panel; remove obsolete inner step-group disclosure styles.
- `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`: protect visibility, aggregate step counting, streaming/terminal lifecycle, timeline ordering, and rejected nested grouping behavior.

---

### Task 1: Restore the Single Timed Execution Panel

**Files:**

- Modify: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx`
- Modify: `src/Workspace/AiPanel/components/WorkLog/index.tsx`
- Modify: `src/Workspace/AiPanel/components/WorkLog/index.scss`
- Test: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`

**Interfaces:**

- Consumes: `AssistantTimelinePart[]`, `TimelineOperationPart`, `ChatMessage.turnStartedAt`, `ChatMessage.durationMs`, streaming state, and the existing direct-row renderers.
- Produces:

```ts
export interface ExecutionPanelPresentation {
  visible: boolean
  active: boolean
  autoOpen: boolean
  stepCount: number
}

export function getExecutionPanelPresentation(
  parts: AssistantTimelinePart[],
  input: { isStreaming: boolean; durationMs?: number },
): ExecutionPanelPresentation
```

- Produces restored component props:

```ts
export interface WorkLogProps {
  logKey: string
  active: boolean
  autoOpen: boolean
  stepCount: number
  startedAt?: number
  durationMs?: number
  hasError?: boolean
  children: React.ReactNode
}
```

- [ ] **Step 1: Replace rejected grouping tests with failing single-panel lifecycle tests**

Update the assistant-timeline import in `modelRuntime.test.cjs` to include `getExecutionPanelPresentation`. Replace the tests whose contract requires `stepGroup` wrappers with this user-visible lifecycle contract:

```js
test('execution panel exists before the first operation and remains before a final answer', () => {
  assert.deepEqual(
    getExecutionPanelPresentation([], {
      isStreaming: true,
    }),
    {
      visible: true,
      active: true,
      autoOpen: true,
      stepCount: 0,
    },
  )

  const completedParts = [{
    type: 'tools',
    segmentIndex: 0,
    segment: {
      commentaryBlockIndex: null,
      labels: ['缓存读取', '读取人物资料'],
      cachedFlags: [true, false],
      completedToolCount: 2,
    },
  }]

  assert.deepEqual(
    getExecutionPanelPresentation(completedParts, {
      isStreaming: false,
      durationMs: 4200,
    }),
    {
      visible: true,
      active: false,
      autoOpen: false,
      stepCount: 1,
    },
  )
})

test('an empty historical Assistant turn does not invent an execution panel', () => {
  assert.deepEqual(
    getExecutionPanelPresentation([], {
      isStreaming: false,
    }),
    {
      visible: false,
      active: false,
      autoOpen: false,
      stepCount: 0,
    },
  )
})
```

Keep the existing literal progress assertions proving the current frontier and cached-row exclusion, but rename them from operation-group progress to execution-panel progress. Remove assertions that require `groupConsecutiveWorkSteps()` to create inner disclosures.

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
```

Expected: FAIL because `getExecutionPanelPresentation` is not a function. The new streaming-without-tools assertion must not pass against the current `hasWorkLog`-only rendering contract.

- [ ] **Step 3: Add the pure execution-panel presentation helper**

In `assistantTimeline.ts`, remove `WorkLogTimelineItem` and `groupConsecutiveWorkSteps`; the outer panel is now the only grouping boundary. Retain `getOperationGroupProgress` for its already-tested visible-step accounting and use it inside:

```ts
export interface ExecutionPanelPresentation {
  visible: boolean
  active: boolean
  autoOpen: boolean
  stepCount: number
}

function isTimelineOperationPart(
  part: AssistantTimelinePart,
): part is TimelineOperationPart {
  return part.type === 'tools'
    || part.type === 'delegations'
    || part.type === 'contextCompaction'
}

export function getExecutionPanelPresentation(
  parts: AssistantTimelinePart[],
  input: { isStreaming: boolean; durationMs?: number },
): ExecutionPanelPresentation {
  const progress = getOperationGroupProgress(parts.filter(isTimelineOperationPart))
  return {
    visible: input.isStreaming
      || parts.length > 0
      || (input.durationMs != null && input.durationMs > 0),
    active: input.isStreaming,
    autoOpen: input.isStreaming,
    stepCount: progress.total,
  }
}
```

This helper deliberately does not own React disclosure state or timing. It only exposes the lifecycle facts that previously disappeared from `AssistantMessageBody`.

- [ ] **Step 4: Restore `WorkLog` as the only disclosure**

Restore the pre-`f573306` outer `WorkLog` open-state store and timer in `WorkLog/index.tsx`, but do not restore `WorkLogStepGroup`. Add `stepCount` and use these terminal labels:

```ts
const title = active
  ? '正在进行'
  : stepCount > 0
    ? `执行了 ${stepCount} 个步骤`
    : '用时'
```

Use the prior stable manual-state rules:

```ts
type OpenState = {
  open: boolean
  manuallySet: boolean
}

const openStateStore = new Map<string, OpenState>()
```

On `autoOpen` transitions, update `open` only when `manuallySetRef.current` is false. Use `performance.now()` while active and the persisted `durationMs` after completion. A panel with no children renders a static status row until process details arrive; it must still show the live timer.

Delete `WorkLogStepGroup`, its state map, and its inner disclosure markup. Restore only the outer `.work-log__toggle`, `.work-log__duration`, `.work-log__collapsible`, `.work-log__body`, chevron, open, active, and error styles. Remove every `.work-log-step-group*` rule.

- [ ] **Step 5: Render direct process rows inside the restored panel**

In `AssistantMessageBody.tsx`:

1. Import the default `WorkLog` and `getExecutionPanelPresentation`.
2. Remove `groupConsecutiveWorkSteps`, `WorkLogStepGroup`, `operationIsActive`, and the per-group duration calculation.
3. Keep `workLogParts = timeline.filter(isVisibleWorkLogPart)` in original timeline order.
4. Derive the presentation once:

```ts
const executionPanel = getExecutionPanelPresentation(workLogParts, {
  isStreaming,
  durationMs: message.durationMs,
})
```

5. Render one panel before `answerParts`:

```tsx
{executionPanel.visible ? (
  <WorkLog
    logKey={message.agentRunId || `${index}-work-log`}
    active={executionPanel.active}
    autoOpen={executionPanel.autoOpen}
    stepCount={executionPanel.stepCount}
    startedAt={message.turnStartedAt}
    durationMs={message.durationMs}
    hasError={workLogHasError(workLogParts)}
  >
    {workLogParts.map(renderDirectWorkLogPart)}
  </WorkLog>
) : null}
```

`renderDirectWorkLogPart` renders commentary through `renderStepPart` and tools, context compaction, or delegations through `renderOperationPart`. It never creates a second disclosure. Keep final-answer rendering after this block unchanged.

- [ ] **Step 6: Run focused tests and type checking; confirm GREEN**

Run:

```bash
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
npm run typecheck
```

Expected: all model-runtime tests pass and TypeScript reports no errors.

- [ ] **Step 7: Inspect the rendered structure without sending an Agent request**

Use the existing local development page and a persisted completed Agent turn. Verify:

1. Exactly one `.work-log__toggle` appears before the final `.bubble-content`.
2. Its closed title contains `执行了` and the persisted duration.
3. Expanding it reveals direct `.bubble-tool-call-details` rows.
4. No `.work-log-step-group__toggle` or nested operation disclosure exists.
5. Manually expand, collapse, and expand again; the state remains stable during harmless page rerenders.
6. Do not click send, apply, adjudicate, archive, or any other business mutation.

- [ ] **Step 8: Commit the execution-panel restoration**

```bash
git add src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts \
  src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx \
  src/Workspace/AiPanel/components/WorkLog/index.tsx \
  src/Workspace/AiPanel/components/WorkLog/index.scss \
  src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
git commit -m "fix(ai-panel): restore execution process panel"
```

---

### Task 2: Run Regression Gates and Clean Up

**Files:**

- Verify only; no planned production edits.

**Interfaces:**

- Consumes: the completed Task 1 implementation.
- Produces: verification evidence and a clean feature branch.

- [ ] **Step 1: Run the frontend regression suite**

```bash
npm run test:unit
npm run typecheck
git diff --check
```

Expected: all frontend unit tests pass, TypeScript reports no errors, and Git reports no whitespace errors.

- [ ] **Step 2: Run the complete Agent refactor gate**

```bash
npm run check:agent-refactor
```

Expected: deterministic boundary, model-contract, screenplay-acceptance, type, frontend-unit, and backend tests pass. Real-provider tests may skip only for their existing missing-key conditions.

- [ ] **Step 3: Check rejected nesting and workspace state**

```bash
rg -n "已操作|正在操作|准备操作|WorkLogStepGroup|work-log-step-group" \
  src/Workspace/AiPanel/components \
  src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
git status --short
```

Expected: no production match for rejected operation copy or the removed inner disclosure, and the working tree is clean after the Task 1 commit.

- [ ] **Step 4: Close browser test tabs and verify process ownership**

Close every browser tab opened for verification. Do not stop the pre-existing port `5173` listener because this task did not start it. If any process was started by this task, stop it and verify its port has no listener.

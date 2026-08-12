# Shared Agent Conversation Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one shared Agent conversation panel and route both the book AI and screenplay Agent through it without changing the PurrA protocol or domain persistence.

**Architecture:** `src/agent-runtime` owns business-agnostic conversation contracts, canonical stream reduction, terminal state projection, replay, task-plan selection and context usage. `src/components/AgentConversation` owns all shared conversation UI; product modules expose a controller and three bounded render extensions for domain context and attachments.

**Tech Stack:** React 18, TypeScript, Node test runner, react-virtuoso, existing Purr components, existing SSE/services layer.

## Global Constraints

- Do not change the PurrA protocol, backend persistence schema, Provider contract, Run, Operation or LongTask semantics.
- Canonical Agent output is the only source of Assistant text, commentary, execution operations, delegation state and task progress.
- Shared runtime and shared UI must not import `Workspace/AiPanel`, `ScreenplayAgentPage` or a future novel product module.
- Product controllers may call services; shared React components may only call controller actions.
- Preserve real streamed Provider text, the single collapsible execution panel, per-operation timing, model-operation filtering, task capsule behavior, abort/recovery, editing, copying and context usage.
- Preserve the current uncommitted fixes in `assistantTimeline.ts`, `WorkLog/index.tsx`, `WorkLog/state.ts` and `modelRuntime.test.cjs` when those files move.
- Do not add a UI framework, state library, plugin system, IOC container or compatibility facade.
- There is no separate novel Agent conversation surface in the current `src`; do not invent one. The novel-facing deliverable is the controller/extensions contract that lets a future module connect without copying the panel.
- Temporary re-exports are allowed only inside the task that moves all callers; delete them before that task's commit.
- Keep unrelated dirty backend and documentation files unstaged. Every commit stages only the paths listed in its task.
- If verification starts a development process, use a free port, record its PID, stop that exact process and verify the port has no listener before handoff.

---

### Task 0: Freeze the Confirmed Execution Panel Baseline

**Files:**
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts`
- Modify: `src/Workspace/AiPanel/components/WorkLog/index.tsx`
- Modify: `src/Workspace/AiPanel/components/WorkLog/state.ts`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`

**Interfaces:**
- Produces: the already-confirmed model-operation filter and non-manual execution-log reset as a clean migration baseline.
- Does not change: any runtime protocol, panel composition or product adapter.

- [ ] **Step 1: Inspect the exact baseline diff**

Run:

```bash
git diff -- src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts src/Workspace/AiPanel/components/WorkLog/index.tsx src/Workspace/AiPanel/components/WorkLog/state.ts src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
```

Expected: only the confirmed internal-model filtering and stale automatic-open reset, plus their tests.

- [ ] **Step 2: Re-run the focused tests and typecheck**

```bash
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
npm run typecheck
```

Expected: both commands PASS before any file movement.

- [ ] **Step 3: Commit only the baseline files**

```bash
git add src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts src/Workspace/AiPanel/components/WorkLog/index.tsx src/Workspace/AiPanel/components/WorkLog/state.ts src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
git diff --cached --name-only
git commit -m "fix(agent): preserve execution panel state"
```

Expected staged names: exactly the four files above. The unrelated backend, PurrA and documentation changes remain unstaged.

---

### Task 1: Establish Shared Conversation Contracts and Pure Selectors

**Files:**
- Create: `src/agent-runtime/contracts.ts`
- Create: `src/agent-runtime/taskPlan.ts`
- Create: `src/agent-runtime/chatHistory.ts`
- Create: `src/agent-runtime/contextUsage.ts`
- Create: `src/agent-runtime/streamOptions.ts`
- Create: `src/agent-runtime/runtimeSelectors.test.ts`
- Modify: `src/agent-runtime/index.ts`
- Modify: `src/agent-runtime/chunkReplay.test.cjs`
- Modify: `src/Workspace/AiPanel/hooks/chat.types.ts`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`
- Delete: `src/Workspace/AiPanel/taskPlanSelection.ts` after switching all callers
- Delete: `src/Workspace/AiPanel/contextUsage.ts` after switching all callers
- Delete: `src/Workspace/AiPanel/hooks/chatHistory.ts` after switching all callers
- Delete: `src/Workspace/AiPanel/hooks/streamOptions.ts` after switching all callers

**Interfaces:**
- Produces: `AgentConversationMessage`, `AiTaskPlan`, `AiTaskStep`, `ToolCallSegment`, `AgentQueuedSubmission` and `AgentConversationActivity`.
- Produces: `getActiveTaskPlan(messages, running)`, `getTaskPlanProgress(plan)`, `buildHistoryConverter()`, `calculateContextUsage(input)` and `buildStreamOptions(input)`.
- Consumes: existing `CanonicalOutputState` and generic Agent-related records from `src/types.ts`.

- [ ] **Step 1: Write selector tests against the destination modules**

Create `runtimeSelectors.test.ts` with the final public names:

~~~ts
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getActiveTaskPlan,
  getTaskPlanCountLabel,
  getVisibleTaskPlanSteps,
} from './taskPlan.ts'
import { buildHistoryConverter } from './chatHistory.ts'

test('task progress hides protocol Respond and uses sequential position', () => {
  const plan = {
    title: '任务',
    status: 'running' as const,
    steps: [
      { id: 'read-1', title: '读取', type: 'read' as const, status: 'done' as const },
      { id: 'write-1', title: '写作', type: 'write' as const, status: 'running' as const },
      { id: 'review-1', title: '审阅', type: 'review' as const, status: 'pending' as const },
      { id: 'respond-1', title: 'Respond', type: 'write' as const, status: 'pending' as const },
    ],
  }
  assert.equal(getVisibleTaskPlanSteps(plan).length, 3)
  assert.equal(getTaskPlanCountLabel(plan), '第 2/3 步')
  assert.equal(getActiveTaskPlan([{ role: 'assistant', content: '', taskPlan: plan }], true), plan)
})

test('history conversion never invents Assistant prose', () => {
  const convert = buildHistoryConverter()
  assert.equal(convert({ role: 'assistant', content: '' }), null)
  assert.deepEqual(convert({ role: 'assistant', content: '模型原文' }), {
    role: 'assistant',
    content: '模型原文',
  })
})
~~~

- [ ] **Step 2: Run the destination test and verify RED**

Run:

~~~bash
node --experimental-strip-types --test src/agent-runtime/runtimeSelectors.test.ts
~~~

Expected: FAIL because `taskPlan.ts` and `chatHistory.ts` do not exist.

- [ ] **Step 3: Create the generic contracts**

Move the generic fields from `ChatMessage` into `AgentConversationMessage`, but do not copy `settingDiffCards`. Keep book-only submit parameters and setting types in `Workspace/AiPanel`.

The public shape must include these exact top-level contracts:

~~~ts
export type AgentSessionId = string | number

export interface AgentQueuedSubmission {
  id: string
  sessionId: AgentSessionId
  content: string
}

export interface AgentConversationActivity {
  state: 'running' | 'paused' | 'queued' | 'completed' | 'failed' | 'canceled'
  queuedCount: number
}

export interface AgentConversationMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  streamingContent?: string
  clientTurnId?: string
  sentAt?: string
  conversationId?: number
  agentRunId?: string
  longTaskId?: string
  isError?: boolean
  error?: string
  termination?: string
  errorReport?: AiErrorReport
  model?: string
  turnStartedAt?: number
  durationMs?: number
  commentary?: string
  commentaryStartedAt?: number
  commentaryBlocks?: string[]
  commentaryDurationsMs?: number[]
  toolCalling?: boolean
  toolCallSegments?: ToolCallSegment[]
  toolApprovals?: ToolApprovalRequest[]
  taskPlan?: AiTaskPlan
  delegations?: AiAgentDelegation[]
  subAgentActivities?: AiSubAgentActivity[]
  contextCompaction?: AiContextCompactionState
  contextBudget?: AiContextBudgetState
  canonicalOutput?: CanonicalOutputState
}
~~~

Move the current `AiTaskPlan`, `AiTaskStep`, `ToolCallSegment` and `AiSubAgentActivity` declarations byte-for-byte. Rename only the generic message from `ChatMessage` to `AgentConversationMessage`. In `Workspace/AiPanel/hooks/chat.types.ts`, keep a product type named `ChatMessage` that extends `AgentConversationMessage` with the temporary book-only `settingDiffCards` field; Task 6 removes that field and product type after the attachment store is active. Do not add aliases for the other stable type names.

- [ ] **Step 4: Move the pure selectors without behavior changes**

Move:

- `taskPlanSelection.ts` to `agent-runtime/taskPlan.ts`;
- `contextUsage.ts` to `agent-runtime/contextUsage.ts`;
- `hooks/chatHistory.ts` to `agent-runtime/chatHistory.ts`;
- `hooks/streamOptions.ts` to `agent-runtime/streamOptions.ts`.

Keep the existing context calculation and task-plan thresholds unchanged. Update their type imports to `contracts.ts`, switch every caller in this task, and delete the four old selector/history/stream files before committing. Do not leave re-export files.

- [ ] **Step 5: Move the matching tests out of the book runtime test**

Move the task-plan, context-usage and history-conversion assertions from `modelRuntime.test.cjs` into `runtimeSelectors.test.ts`. Update `chunkReplay.test.cjs` to import `taskPlan.ts` instead of `Workspace/AiPanel/taskPlanSelection.ts`. Do not duplicate assertions in both files.

- [ ] **Step 6: Run focused tests and typecheck**

Run:

~~~bash
node --experimental-strip-types --test src/agent-runtime/runtimeSelectors.test.ts src/agent-runtime/chunkReplay.test.cjs
npm run typecheck
~~~

Expected: all tests PASS; TypeScript reports zero errors.

- [ ] **Step 7: Commit Task 1 only**

~~~bash
git add src/agent-runtime src/Workspace/AiPanel/hooks/chat.types.ts src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/Workspace/AiPanel/taskPlanSelection.ts src/Workspace/AiPanel/contextUsage.ts src/Workspace/AiPanel/hooks/chatHistory.ts src/Workspace/AiPanel/hooks/streamOptions.ts
git commit -m "refactor(agent): extract shared conversation contracts"
~~~

---

### Task 2: Move Canonical Chunk Handling Behind a Business-Agnostic Host Port

**Files:**
- Create: `src/agent-runtime/chunkHandlers/types.ts`
- Create: `src/agent-runtime/chunkHandlers/canonical.ts`
- Create: `src/agent-runtime/chunkHandlers/commitScheduler.ts`
- Create: `src/agent-runtime/chunkHandlers/durableTask.ts`
- Create: `src/agent-runtime/chunkHandlers/terminal.ts`
- Create: `src/agent-runtime/chunkHandlers/index.ts`
- Create: `src/agent-runtime/runtimeStore.ts`
- Create: `src/agent-runtime/chunkHandlers.test.ts`
- Create: `src/Workspace/AiPanel/hooks/bookChunkHost.ts`
- Modify: `src/agent-runtime/chunkReplay.ts`
- Modify: `src/agent-runtime/index.ts`
- Modify: `src/Workspace/AiPanel/hooks/useChatSubmit.ts`
- Modify: `src/Workspace/AiPanel/hooks/chatRuntimeStore.ts`
- Move: `src/Workspace/AiPanel/hooks/chunkHandlers/sideEffects.ts` to `src/Workspace/AiPanel/hooks/bookChunkSideEffects.ts`
- Move: `src/Workspace/AiPanel/hooks/chunkHandlers/settingDiff.ts` to `src/Workspace/AiPanel/hooks/bookSettingDiff.ts`
- Delete: remaining generic files under `src/Workspace/AiPanel/hooks/chunkHandlers/`

**Interfaces:**
- Consumes: `AgentConversationMessage` and `CanonicalOutputState` from Task 1.
- Produces: `dispatchAgentChunk(chunk, context)`, `AgentChunkRuntimeContext`, `AgentChunkHost`, `AgentTerminalSnapshot`, `AgentConversationRuntime` and its external-store functions.
- Produces: book-only `createBookChunkHost(dependencies)` that owns services, persistence, session-title generation, setting diff and DOM product events.

- [ ] **Step 1: Write tests for the host boundary and terminal semantics**

~~~ts
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  dispatchAgentChunk,
  initialAgentAccumulator,
  type AgentChunkHost,
  type AgentTerminalSnapshot,
} from './chunkHandlers/index.ts'
import type { AgentConversationMessage } from './contracts.ts'

function createTestChunkContext(
  initialMessages: AgentConversationMessage[],
  overrides: Pick<Partial<AgentChunkHost>, 'onHostChunk' | 'onSettled'> = {},
) {
  let messages = initialMessages
  const host: AgentChunkHost = {
    readMessages: () => messages,
    replaceMessages: (next) => { messages = next },
    scheduleCommit: (updater) => { messages = updater(messages) },
    flushCommits: () => undefined,
    setRunning: () => undefined,
    isVisible: () => true,
    onHostChunk: overrides.onHostChunk,
    onSettled: overrides.onSettled ?? (() => undefined),
  }
  return {
    context: {
      acc: initialAgentAccumulator({
        sessionId: 1,
        userText: '问题',
        turnStartedAt: 0,
      }),
      sessionId: 1,
      modelIdentity: { name: 'test-model' },
      host,
      now: () => 100,
    },
    readMessages: () => messages,
  }
}

test('terminal projection calls the host once and keeps provider text unchanged', () => {
  const settled: AgentTerminalSnapshot[] = []
  const messages = [
    { role: 'user' as const, content: '问题' },
    { role: 'assistant' as const, content: '', streamingContent: '模型原文' },
  ]
  const harness = createTestChunkContext(messages, {
    onSettled: (_outcome, snapshot) => settled.push(snapshot),
  })
  harness.context.acc.response = '模型原文'

  dispatchAgentChunk({ done: true, model: 'test-model' }, harness.context)

  assert.equal(settled.length, 1)
  assert.equal(settled[0].response, '模型原文')
  assert.equal(harness.readMessages().at(-1)?.content, '模型原文')
})

test('runtime invokes injected host chunk handling without knowing book events', () => {
  const received: unknown[] = []
  const harness = createTestChunkContext([], {
    onHostChunk: (chunk) => received.push(chunk),
  })
  const chunk = { settingUpdated: { kind: 'character' } }
  dispatchAgentChunk(chunk, harness.context)
  assert.deepEqual(received, [chunk])
})
~~~

The test helper must not import React, Purr components or services.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

~~~bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts
~~~

Expected: FAIL because `agent-runtime/chunkHandlers` does not exist.

- [ ] **Step 3: Define the host port**

Use this boundary:

~~~ts
export type AgentRunOutcome =
  | 'completed'
  | 'paused'
  | 'failed'
  | 'canceled'

export interface AgentTerminalSnapshot {
  sessionId: AgentSessionId
  userText: string
  response: string
  model?: string
  agentRunId?: string
  longTaskId?: string
  taskPlan?: AiTaskPlan
  commentaryBlocks: string[]
  commentaryDurationsMs: number[]
  toolCallSegments: ToolCallSegment[]
  contextCompaction?: AiContextCompactionState
  contextBudget?: AiContextBudgetState
  durationMs: number
}

export interface AgentChunkHost {
  readMessages(): AgentConversationMessage[]
  replaceMessages(messages: AgentConversationMessage[]): void
  scheduleCommit(
    updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
  ): void
  flushCommits(): void
  setRunning(running: boolean): void
  isVisible(): boolean
  onHostChunk?(chunk: AiStreamChunk): void
  onSettled(outcome: AgentRunOutcome, snapshot: AgentTerminalSnapshot): void
}
~~~

`AgentChunkRuntimeContext` contains only the accumulator, session id, model identity, host, `persistConversation` flag and injected `now()`. Remove `bookId`, `chapterId`, `writingChapters`, `availableOutlines`, `setSessions`, `AppMessage` and `AiModelConfig` from the shared context.

- [ ] **Step 4: Move generic chunk code and keep terminal output atomic**

Move canonical reduction, durable-task projection, commit scheduling and terminal message projection into `agent-runtime/chunkHandlers`. Replace direct persistence/title/toast calls in `terminal.ts` with one `host.onSettled(outcome, snapshot)` call after the message has reached its terminal state.

The dispatcher order must remain:

~~~ts
export function dispatchAgentChunk(
  chunk: AiStreamChunk,
  context: AgentChunkRuntimeContext,
): void {
  if (handleCanonicalOutput(chunk, context)) return
  if (handleRunResultTerminal(chunk, context)) return
  if (handleError(chunk, context)) return
  context.host.onHostChunk?.(chunk)
  handleLongTaskDispatched(chunk, context)
  handleLongTaskProgress(chunk, context)
  handleDone(chunk, context)
}
~~~

Do not add model-invocation text, fallback summaries or host-generated transition prose.

- [ ] **Step 5: Move book effects and persistence into the book host**

`createBookChunkHost` must:

- dispatch `proposedChapterDiff`, `proposedSettingDiff`, `chapterCreated` and `settingUpdated` through the existing book events;
- persist the terminal snapshot with `services.conversations.saveConversation`;
- update the local conversation id after a successful save;
- generate the first session title with the existing Provider settings;
- show persistence failure through the injected book toast;
- settle the book queue and start its next queued submission.

It must not change `AgentConversationMessage.content` after the shared terminal reducer commits it.

- [ ] **Step 6: Switch live and replay callers**

Update `useChatSubmit.ts` to create the book host and call `dispatchAgentChunk`. Update `AgentChunkReplay` to use an in-memory host whose `onSettled` and `onHostChunk` are no-ops. Move the session runtime map into `agent-runtime/runtimeStore.ts`; keep the book submission payload queue in `Workspace/AiPanel` because it contains book ids, selected memories and model credentials.

Preserve the current external-store behavior under these shared names:

```ts
export interface AgentConversationRuntime {
  sessionId: AgentSessionId
  messages: AgentConversationMessage[]
  running: boolean
  activity?: AgentConversationActivity
  streamId?: string
  updatedAt: number
}

export function subscribeAgentConversationRuntime(listener: () => void): () => void
export function getAgentConversationRuntimeVersion(): number
export function getAgentConversationRuntime(
  sessionId: AgentSessionId | null | undefined,
): AgentConversationRuntime | undefined
export function replaceAgentConversationMessages(
  sessionId: AgentSessionId,
  messages: AgentConversationMessage[],
): void
export function updateAgentConversationMessages(
  sessionId: AgentSessionId,
  updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
): void
export function clearAgentConversationRuntime(sessionId: AgentSessionId): void
```

Book queue getters/setters stay in a book file and are not exported from `agent-runtime`.

- [ ] **Step 7: Run focused and regression tests**

Run:

~~~bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts src/agent-runtime/chunkReplay.test.cjs src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
npm run typecheck
~~~

Expected: all tests PASS; no file under `src/agent-runtime` imports `Workspace/AiPanel`.

- [ ] **Step 8: Commit Task 2 only**

~~~bash
git add src/agent-runtime src/Workspace/AiPanel/hooks
git commit -m "refactor(agent): centralize canonical stream runtime"
~~~

---

### Task 3: Move Assistant Output and Execution UI Into the Shared Component

**Files:**
- Move: `src/Workspace/AiPanel/components/Markdown/` to `src/components/Markdown/`
- Create: `src/components/AgentConversation/AssistantOutput/index.tsx`
- Move: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.scss` to `src/components/AgentConversation/AssistantOutput/index.scss`
- Move: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts` to `src/components/AgentConversation/AssistantOutput/timeline.ts`
- Move: `src/Workspace/AiPanel/components/WorkLog/` to `src/components/AgentConversation/ExecutionLog/`
- Move: `src/Workspace/AiPanel/components/ToolCallStatus/` to `src/components/AgentConversation/ToolCallStatus/`
- Move: `src/Workspace/AiPanel/components/SubAgentStatusList/` to `src/components/AgentConversation/DelegationStatus/`
- Move: `src/Workspace/AiPanel/components/StructuredQuestionCard/` to `src/components/AgentConversation/StructuredQuestion/`
- Move: `src/Workspace/AiPanel/components/ToolApprovalCard/` to `src/components/AgentConversation/ToolApproval/`
- Move: `src/Workspace/AiPanel/components/ChatMessageList/ErrorReportNotice.tsx` to `src/components/AgentConversation/ErrorReportNotice.tsx`
- Move: `src/Workspace/AiPanel/structuredQuestions.ts` to `src/components/AgentConversation/StructuredQuestion/parser.ts`
- Move: `src/Workspace/AiPanel/hooks/toolCallLabels.ts` to `src/components/AgentConversation/toolCallLabels.ts`
- Move: `src/Workspace/AiPanel/components/TaskPlanCard/` to `src/components/AgentConversation/TaskProgress/TaskPlanCard/`
- Create: `src/components/AgentConversation/AssistantOutput/timeline.test.ts`
- Create: `src/components/AgentConversation/ExecutionLog/state.test.ts`
- Modify: `src/components/AgentConversation/index.tsx`
- Modify: `src/components/AgentConversationTurnIndex/index.tsx`
- Modify: `src/components/AgentTaskProgress/index.tsx`
- Modify: `src/Workspace/AiPanel/index.tsx`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/index.tsx`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/ChatMessageBubble.tsx`
- Modify: `src/ScreenplayAgentPage/index.tsx`

**Interfaces:**
- Consumes: `AgentConversationMessage` and canonical output state from Tasks 1–2.
- Produces: `AssistantOutput` with callback-only approval and structured-answer actions.
- Produces: `buildAssistantTimeline`, `getExecutionPanelPresentation` and persistent user-controlled `ExecutionLog` open state.

- [ ] **Step 1: Write destination tests for the confirmed execution behavior**

Move the current uncommitted assertions into final-path tests and add this grouping assertion:

~~~ts
import assert from 'node:assert/strict'
import test from 'node:test'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts.ts'
import { initialCanonicalOutputState } from '../../../agent-runtime/canonicalOutput.ts'
import {
  buildAssistantTimeline,
  getExecutionPanelPresentation,
  groupConsecutiveWorkSteps,
} from './timeline.ts'
import {
  applyExecutionLogAutoOpen,
  getInitialExecutionLogOpenState,
} from '../ExecutionLog/state.ts'

test('canonical timeline filters model operations and groups consecutive work once', () => {
  const base = initialCanonicalOutputState()
  const operation = (operationId: string, kind: string, status: 'running' | 'succeeded') => ({
    operationId,
    runId: 'run-1',
    invocationId: null,
    kind,
    firstSequence: Number(operationId.at(-1)),
    status,
    startedAt: '2026-08-12T00:00:00Z',
    display: { labelParams: {} },
  })
  const operations = {
    'model-1': operation('model-1', 'model', 'succeeded'),
    'tool-1': operation('tool-1', 'tool', 'succeeded'),
    'tool-2': operation('tool-2', 'tool', 'running'),
  }
  const message: AgentConversationMessage = {
    role: 'assistant',
    content: '',
    canonicalOutput: {
      ...base,
      operationOrder: Object.keys(operations),
      operations,
    },
  }
  const timeline = buildAssistantTimeline(message, {
    messageIndex: 0,
    isStreaming: true,
  })
  const visible = timeline.filter((part) => part.type === 'operation')
  assert.deepEqual(visible.map((part) => part.operation.kind), ['tool', 'tool'])
  const grouped = groupConsecutiveWorkSteps(visible, 'turn-1')
  assert.equal(grouped.length, 2)
  assert.equal(getExecutionPanelPresentation(visible, {
    isStreaming: true,
  }).title, '正在进行')
})

test('stale automatic state cannot reopen a later execution batch', () => {
  assert.deepEqual(
    getInitialExecutionLogOpenState({ open: true, manuallySet: false }, false),
    { open: false, manuallySet: false },
  )
  assert.deepEqual(
    applyExecutionLogAutoOpen({ open: false, manuallySet: true }, true),
    { open: false, manuallySet: true },
  )
})
~~~

- [ ] **Step 2: Run the destination tests and verify RED**

Run:

~~~bash
node --experimental-strip-types --test src/components/AgentConversation/AssistantOutput/timeline.test.ts src/components/AgentConversation/ExecutionLog/state.test.ts
~~~

Expected: FAIL because destination modules do not exist.

- [ ] **Step 3: Move the shared presentation modules**

Move existing code rather than rewriting it. Update imports to `agent-runtime` and `components/Markdown`. Preserve:

- canonical commentary order;
- real `streamingContent` while a Provider final stream is open;
- one execution disclosure per Assistant turn;
- direct operation rows after expansion, with no nested “已操作” disclosure;
- per-operation and total duration;
- internal `operation.kind === 'model'` filtering;
- manual disclosure state across harmless rerenders;
- automatic state reset for a later non-manual batch.

- [ ] **Step 4: Remove service access from tool approval**

Use this public callback:

~~~ts
export interface ToolApprovalProps {
  approval: ToolApprovalRequest
  onResolve: (
    approvalId: string,
    approved: boolean,
  ) => Promise<{ success: boolean; error?: string }>
}
~~~

The card keeps its local submitting/resolved visual state, but calls `onResolve` instead of `services.ai.resolveAiToolApproval`. `AssistantOutput` receives `onResolveToolApproval` and passes it down.

- [ ] **Step 5: Remove book setting cards from AssistantOutput**

Delete `settingDiffCards` rendering from shared output. Until Task 6 creates the external attachment store, the temporary book `ChatMessageBubble` renders its existing cards immediately after `AssistantOutput`; this preserves the book behavior without putting the domain component back into shared output. Keep `SettingDiffCard` in `Workspace/AiPanel`. Shared output renders only execution state, canonical text, errors, termination, structured questions and approval cards.

- [ ] **Step 6: Switch existing message surfaces to the shared output**

Update both the current `AgentConversation` and the temporary book `ChatMessageBubble` to render `AssistantOutput`. Add `onStructuredAnswer` and `onResolveToolApproval` to the temporary callers: the book page calls its existing submit and `services.ai.resolveAiToolApproval`; the screenplay page calls `runAgent(answer)` and the same service action. Move Markdown imports in `RevisionLibraryModal.tsx` and `ScreenplayAgentPage/index.tsx` to `src/components/Markdown`.

- [ ] **Step 7: Run focused tests and typecheck**

Run:

~~~bash
node --experimental-strip-types --test src/components/AgentConversation/AssistantOutput/timeline.test.ts src/components/AgentConversation/ExecutionLog/state.test.ts src/components/AgentConversation/messageVisibility.test.ts src/components/AgentTaskProgress/localization.test.ts
npm run typecheck
~~~

Expected: all tests PASS; the old Assistant body is no longer imported.

- [ ] **Step 8: Commit Task 3 only**

~~~bash
git add src/components src/Workspace/AiPanel/components src/Workspace/AiPanel/structuredQuestions.ts src/Workspace/AiPanel/hooks/toolCallLabels.ts src/ScreenplayAgentPage
git commit -m "refactor(agent): share assistant output presentation"
~~~

---

### Task 4: Compose the Complete AgentConversationPanel

**Files:**
- Create: `src/components/AgentConversation/controller.ts`
- Create: `src/components/AgentConversation/extensions.ts`
- Create: `src/components/AgentConversation/Panel.tsx`
- Create: `src/components/AgentConversation/Panel.scss`
- Create: `src/components/AgentConversation/panelView.ts`
- Create: `src/components/AgentConversation/panelView.test.ts`
- Move: `src/components/AgentConversation/index.tsx` to `src/components/AgentConversation/ConversationViewport/index.tsx`
- Move: `src/components/AgentConversation/index.scss` to `src/components/AgentConversation/ConversationViewport/index.scss`
- Move: `src/components/AgentComposer/` to `src/components/AgentConversation/Composer/`
- Move: `src/components/AgentConversationIndex/` to `src/components/AgentConversation/ConversationIndex/`
- Move: `src/components/AgentConversationTurnIndex/` to `src/components/AgentConversation/TurnIndex/`
- Move: `src/components/AgentTaskProgress/index.tsx`, `index.scss`, `localization.ts` and `localization.test.ts` into the existing `src/components/AgentConversation/TaskProgress/` directory
- Move: `src/Workspace/AiPanel/components/ModelPicker/` to `src/components/AgentConversation/Composer/ModelPicker/`
- Move: `src/Workspace/AiPanel/components/ContextUsageIndicator/` to `src/components/AgentConversation/Composer/ContextUsageIndicator/`
- Move: `src/Workspace/AiPanel/components/SessionHistoryPopover/` to `src/components/AgentConversation/ConversationIndex/SessionHistory/`
- Create: `src/components/AgentConversation/index.ts`
- Modify: `src/Workspace/AiPanel/hooks/useChatScroll.ts`

**Interfaces:**
- Consumes: shared output and runtime contracts from Tasks 1–3.
- Produces: `AgentConversationPanel`, `AgentConversationController` and `AgentConversationExtensions`.
- Produces: one virtualized `ConversationViewport` used by both existing products.

- [ ] **Step 1: Write controller-derived view tests**

~~~ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { buildAgentConversationPanelView } from './panelView.ts'

test('panel shows queue mode and hides a terminal task capsule', () => {
  const view = buildAgentConversationPanelView({
    running: true,
    queuedCount: 2,
    taskPlan: { title: '完成', status: 'done', steps: [] },
    submitMode: 'queue',
  })
  assert.equal(view.submitLabel, '加入发送队列')
  assert.equal(view.queueLabel, '排队 2')
  assert.equal(view.showTaskProgress, false)
})

test('panel keeps sequential and parallel task labels in the shared selector', () => {
  const sequential = buildAgentConversationPanelView({
    running: true,
    queuedCount: 0,
    taskPlan: {
      title: '任务',
      status: 'running',
      steps: [
        { id: 'read-1', title: '读取', type: 'read', status: 'done' },
        { id: 'write-1', title: '写作', type: 'write', status: 'running' },
        { id: 'review-1', title: '审阅', type: 'review', status: 'pending' },
      ],
    },
    submitMode: 'send',
  })
  assert.equal(sequential.taskCountLabel, '第 2/3 步')
})
~~~

- [ ] **Step 2: Run the view test and verify RED**

Run:

~~~bash
node --experimental-strip-types --test src/components/AgentConversation/panelView.test.ts
~~~

Expected: FAIL because `panelView.ts` does not exist.

- [ ] **Step 3: Define the concrete controller**

Use this implementation contract:

~~~ts
export interface AgentConversationSession {
  id: AgentSessionId
  title: string
  createdAt?: string
}

export interface AgentConversationController {
  capabilities: AgentConversationCapabilities
  conversation: {
    sessions: AgentConversationSession[]
    activeSessionId: AgentSessionId | null
    messages: AgentConversationMessage[]
    activities: Record<string, AgentConversationActivity>
    queuedSubmissions: AgentQueuedSubmission[]
    initializing: boolean
    running: boolean
    stopping: boolean
    paused: boolean
    resuming: boolean
    attachmentsVersion?: string | number
    history?: {
      sessions: AgentConversationSession[]
      loading: boolean
      error?: string
    }
  }
  composer: {
    value: string
    setValue(value: string): void
    placeholder: string
    ariaLabel: string
    submitDisabled: boolean
    selectedModel: AiModelConfig | null
    modelConfigs: AiModelConfig[]
    selectModel(id: string): void
    updateModel?: (
      id: string,
      patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
    ) => void
    openModelSettings(): void
    taskPlan?: AiTaskPlan
  }
  actions: {
    selectSession(id: AgentSessionId): void | Promise<void>
    createSession(): void | Promise<void>
    closeSession(id: AgentSessionId): void | Promise<void>
    renameSession(id: AgentSessionId, title: string): void | Promise<void>
    loadSessionHistory?(): void | Promise<void>
    openHistorySession?(id: AgentSessionId): void | Promise<void>
    deleteSession?(id: AgentSessionId): void | Promise<void>
    send(content?: string): void | Promise<void>
    abort(): void | Promise<void>
    resume?(): void | Promise<void>
    editMessage(index: number, content: string): void | Promise<void>
    resolveToolApproval(
      approvalId: string,
      approved: boolean,
    ): Promise<{ success: boolean; error?: string }>
  }
}
~~~

Session history is optional because screenplay currently has no archived-history picker. Model settings and approval methods still exist when the controller has no configured model or no pending approval.

The panel itself accepts only layout state in addition to the controller and extensions:

```ts
export interface AgentConversationPanelProps {
  controller: AgentConversationController
  extensions?: AgentConversationExtensions
  indexOpen?: boolean
  onIndexOpenChange?(open: boolean): void
  className?: string
}
```

- [ ] **Step 4: Define only the approved extensions**

~~~ts
export interface AgentConversationExtensions {
  renderSessionContext?(): React.ReactNode
  renderComposerLeading?(): React.ReactNode
  renderAssistantAttachment?(
    message: AgentConversationMessage,
    index: number,
  ): React.ReactNode
}
~~~

Do not add footer, message renderer, event renderer or arbitrary panel slots. Shared copy/edit/error/approval/execution/task controls remain non-replaceable.

Refactor the moved `SessionHistory` component to consume `controller.conversation.history` and the `loadSessionHistory`, `openHistorySession` and `deleteSession` actions. Delete its direct `services` import; the product controller decides the book/chapter query scope.

- [ ] **Step 5: Build the shared viewport from the existing virtual list**

Use the already-installed `react-virtuoso` implementation from the book `ChatMessageList` as the single viewport. Move the current shared scroll-follow policy and turn index into it. It must:

- virtualize long conversations;
- stop following when the user scrolls upward by wheel, keyboard, touch or scrollbar;
- resume following only after returning to the bottom;
- expose one “回到底部” action;
- use `clientTurnId`, `conversationId`, `agentRunId` or a stable index fallback as the item key;
- render the shared user message and `AssistantOutput`;
- call controller edit and approval actions;
- render the domain attachment after its Assistant message.

- [ ] **Step 6: Build Panel composition**

`Panel.tsx` renders:

~~~tsx
<div className="agent-conversation-panel">
  <ConversationIndex />
  <main className="agent-conversation-panel__main">
    <ConversationViewport />
    <Composer
      floatingContent={view.showTaskProgress ? <TaskProgress /> : null}
      supplementaryContent={<QueuedSubmissions />}
      footer={<ComposerFooter />}
    />
  </main>
</div>
~~~

`ComposerFooter` owns model selection, context usage, queue count, resume, stop and send. `renderComposerLeading` is inserted before the model picker. The task capsule appears only above the composer and disappears after final output completion.

- [ ] **Step 7: Run shared component tests and typecheck**

Run:

~~~bash
node --experimental-strip-types --test src/components/AgentConversation/panelView.test.ts src/components/AgentConversation/scrollFollowPolicy.test.ts src/components/AgentConversation/messageVisibility.test.ts src/components/AgentConversation/TaskProgress/localization.test.ts
npm run typecheck
~~~

Expected: all tests PASS and `AgentConversationPanel` exports from `src/components/AgentConversation/index.ts`.

- [ ] **Step 8: Commit Task 4 only**

~~~bash
git add src/components/AgentConversation src/components/AgentComposer src/components/AgentConversationIndex src/components/AgentConversationTurnIndex src/components/AgentTaskProgress src/Workspace/AiPanel/components/ModelPicker src/Workspace/AiPanel/components/ContextUsageIndicator src/Workspace/AiPanel/components/SessionHistoryPopover src/Workspace/AiPanel/hooks/useChatScroll.ts
git commit -m "feat(agent): add shared conversation panel"
~~~

---

### Task 5: Adapt the Screenplay Agent to the Shared Panel

**Files:**
- Create: `src/ScreenplayAgentPage/useScreenplayConversationController.ts`
- Create: `src/ScreenplayAgentPage/ScreenplayConversationExtensions.tsx`
- Create: `src/ScreenplayAgentPage/screenplayConversationController.test.ts`
- Modify: `src/ScreenplayAgentPage/index.tsx`
- Modify: `src/ScreenplayAgentPage/index.scss`

**Interfaces:**
- Consumes: `AgentConversationController` and `AgentConversationExtensions`.
- Produces: a screenplay adapter over existing `agentMessages`, queue, session, model, abort/resume/edit and artifact actions.
- Does not move project documents, review adjudication, revision library or stage commands into shared UI.

- [ ] **Step 1: Write mapping tests**

~~~ts
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createScreenplayConversationController,
  type ScreenplayConversationBindings,
} from './useScreenplayConversationController.ts'

const noOp = () => undefined
const bindings: ScreenplayConversationBindings = {
  project: { id: 'p1', title: '剧本', status: 'active' } as ScreenplayProject,
  sessions: [],
  activeSessionId: 7,
  messages: [{ role: 'assistant', content: '' }],
  activities: {},
  queuedSubmissions: [],
  prompt: '',
  setPrompt: noOp,
  initializing: false,
  running: false,
  stopping: false,
  paused: false,
  resuming: false,
  modelConfigs: [],
  selectedModelId: '',
  setSelectedModelId: noOp,
  openModelSettings: noOp,
  actions: {
    selectSession: noOp,
    createSession: noOp,
    closeSession: noOp,
    renameSession: noOp,
    send: noOp,
    abort: noOp,
    resume: noOp,
    editMessage: noOp,
    resolveToolApproval: async () => ({ success: true }),
  },
}

test('screenplay adapter maps durable status and queue without adding prose', () => {
  const controller = createScreenplayConversationController({
    ...bindings,
    running: true,
    queuedSubmissions: [{
      id: 'q1',
      projectId: 'p1',
      sessionId: 7,
      content: '继续审阅',
      runtime: {} as ScreenplayQueuedSubmission['runtime'],
    }],
  })
  assert.equal(controller.conversation.running, true)
  assert.deepEqual(controller.conversation.queuedSubmissions, [
    { id: 'q1', sessionId: 7, content: '继续审阅' },
  ])
  assert.equal(controller.conversation.messages.at(-1)?.content, '')
})
~~~

- [ ] **Step 2: Run the adapter test and verify RED**

Run:

~~~bash
node --experimental-strip-types --test src/ScreenplayAgentPage/screenplayConversationController.test.ts
~~~

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Build a thin controller adapter**

The adapter accepts grouped existing bindings:

~~~ts
export interface ScreenplayConversationBindings {
  project: ScreenplayProject
  sessions: AiSession[]
  activeSessionId: number | null
  messages: AgentConversationMessage[]
  activities: Record<string, AgentConversationActivity>
  queuedSubmissions: ScreenplayQueuedSubmission[]
  prompt: string
  setPrompt(value: string): void
  initializing: boolean
  running: boolean
  stopping: boolean
  paused: boolean
  resuming: boolean
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  setSelectedModelId(id: string): void
  updateModel?: AgentConversationController['composer']['updateModel']
  openModelSettings(): void
  taskPlan?: AiTaskPlan
  actions: Pick<
    AgentConversationController['actions'],
    | 'selectSession'
    | 'createSession'
    | 'closeSession'
    | 'renameSession'
    | 'send'
    | 'abort'
    | 'resume'
    | 'editMessage'
    | 'resolveToolApproval'
  >
}
~~~

`createScreenplayConversationController` is a pure mapping function. `useScreenplayConversationController` wraps it in `React.useMemo`; it does not duplicate polling, replay, cancellation or persistence already owned by the page and `ScreenplayConversationClient`.

- [ ] **Step 4: Build screenplay extensions**

`renderSessionContext` renders current project title and stage. `renderAssistantAttachment` looks up `ScreenplayTurnArtifact` by message index and renders the existing proposal action panel. Do not move Revision or Artifact types into the shared component.

- [ ] **Step 5: Replace the screenplay conversation JSX**

Replace the separate `AgentConversationIndex`, `AgentConversation`, `AgentComposer`, `AgentTaskProgress`, `ModelPicker` and `ContextUsageIndicator` block with:

~~~tsx
<AgentConversationPanel
  controller={screenplayConversationController}
  extensions={screenplayConversationExtensions}
/>
~~~

Keep the studio header and surrounding project layout. Remove only CSS selectors made obsolete by the deleted inner composition.

- [ ] **Step 6: Run screenplay and shared tests**

Run:

~~~bash
node --experimental-strip-types --test src/ScreenplayAgentPage/screenplayConversationController.test.ts src/ScreenplayAgentPage/conversationState.test.ts src/ScreenplayAgentPage/sessionRestore.test.ts src/ScreenplayAgentPage/revisionProposal.test.ts src/components/AgentConversation/panelView.test.ts
npm run typecheck
~~~

Expected: all tests PASS; screenplay page imports no component from `Workspace/AiPanel`.

- [ ] **Step 7: Commit Task 5 only**

~~~bash
git add src/ScreenplayAgentPage
git commit -m "refactor(screenplay): use shared agent conversation panel"
~~~

---

### Task 6: Adapt the Book AI to the Shared Panel

**Files:**
- Create: `src/Workspace/AiPanel/useBookConversationController.ts`
- Create: `src/Workspace/AiPanel/BookConversationExtensions.tsx`
- Create: `src/Workspace/AiPanel/bookAssistantAttachments.ts`
- Create: `src/Workspace/AiPanel/bookConversationController.test.ts`
- Modify: `src/Workspace/AiPanel/index.tsx`
- Modify: `src/Workspace/AiPanel/index.scss`
- Modify: `src/Workspace/AiPanel/hooks/useChatSubmit.ts`
- Delete: `src/Workspace/AiPanel/components/ConversationSidebar/` after the switch
- Delete: `src/Workspace/AiPanel/hooks/useMessageEditing.ts` after the switch
- Keep: `src/Workspace/AiPanel/components/AiContextBar/`
- Keep: `src/Workspace/AiPanel/components/SettingDiffCard/`
- Keep: `src/Workspace/AiPanel/components/FavoritesModal/`
- Keep: `src/Workspace/AiPanel/components/MemoryModal/`
- Keep: `src/Workspace/AiPanel/components/PromptTemplatePicker/`
- Delete: `src/Workspace/AiPanel/components/ChatMessageList/` after the switch
- Delete: `src/Workspace/AiPanel/components/MessageEditor/` after the switch
- Delete: `src/Workspace/AiPanel/components/AiComposeBottom/` after the switch
- Delete: `src/Workspace/AiPanel/components/ChatEmptyState/` if no remaining import exists

**Interfaces:**
- Consumes: shared panel and existing book hooks.
- Produces: a book controller that maps book session scope, runtime queue, model, history and approval actions.
- Produces: book extensions for scope/context controls, setting diff cards and favorites.
- Produces: `toBookQueuedSubmissions(sessionId, queuedMessages)` for the display-only queue projection.

- [ ] **Step 1: Write book mapping and attachment tests**

~~~ts
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  bookAttachmentKey,
  reduceBookAssistantAttachment,
} from './bookAssistantAttachments.ts'
import { toBookQueuedSubmissions } from './useBookConversationController.ts'

test('setting diff is stored outside the generic message', () => {
  const message = { role: 'assistant' as const, content: '', clientTurnId: 'turn-1' }
  const next = reduceBookAssistantAttachment({}, bookAttachmentKey(message), {
    sessionKey: 'character:1',
    kind: 'character',
    title: '人物设定',
    status: 'pending',
  })
  assert.equal(next['client:turn-1'][0].sessionKey, 'character:1')
  assert.equal('settingDiffCards' in message, false)
})

test('book adapter exposes only display queue entries', () => {
  assert.deepEqual(
    toBookQueuedSubmissions(7, ['第一条', '第二条']),
    [
      { id: 'book-queue-7-0', sessionId: 7, content: '第一条' },
      { id: 'book-queue-7-1', sessionId: 7, content: '第二条' },
    ],
  )
})
~~~

- [ ] **Step 2: Run the adapter test and verify RED**

Run:

~~~bash
node --experimental-strip-types --test src/Workspace/AiPanel/bookConversationController.test.ts
~~~

Expected: FAIL because the book adapter and attachment store do not exist.

- [ ] **Step 3: Move setting diff state out of messages**

`bookAssistantAttachments.ts` owns `Record<string, SettingDiffCardState[]>`. Key live messages by `clientTurnId`, persisted messages by `conversationId` and replay messages by `agentRunId`. `bookSettingDiff.ts` updates this store through an injected callback; the `setting-diff-resolved` event updates the same store. Remove `settingDiffCards` from the remaining book message type.

- [ ] **Step 4: Build the book controller from existing hooks**

The controller reuses `useAiSessions`, `useChatSubmit` and `useAiModelPrefs`. The shared viewport owns the temporary edit draft; the controller only exposes `editMessage(index, content)`. It maps:

- visible sessions and history actions;
- `getAgentConversationCapabilities`;
- combined historical and current messages;
- display-only queue items with stable ids;
- selected model and update action;
- submit, structured answer, edit, abort and approval resolution;
- current task plan and context usage inputs.

Do not move associated chapters, outlines, memories, favorites or prompt templates into the controller contract.

- [ ] **Step 5: Build book extensions**

`renderSessionContext` renders the current book, chapter/global scope switch and history trigger. `renderComposerLeading` renders Agent/问答 mode and the existing `AiContextBar`. `renderAssistantAttachment` renders setting diff cards and the existing favorite action for that Assistant turn.

- [ ] **Step 6: Replace the book composition**

Replace `ConversationSidebar` + `ChatMessageList` + `AgentComposer` with:

~~~tsx
<AgentConversationPanel
  controller={bookConversationController}
  extensions={bookConversationExtensions}
/>
~~~

Keep `AiPanelHeader`, `FavoritesModal` and `MemoryModal` around the shared panel. Keep the external `conversationSidebarOpen` binding by passing it as controlled panel layout state or by preserving the existing reopen button immediately outside `AgentConversationPanel`.

- [ ] **Step 7: Delete the superseded book renderers**

Delete `ChatMessageList`, `ChatMessageBubble`, the book-only inline `MessageEditor`, `AiComposeBottom`, `ConversationSidebar` and `useMessageEditing` only after `rg` confirms no imports. Do not delete book context, setting diff, memory, favorites or prompt-template components.

- [ ] **Step 8: Run focused and unit tests**

Run:

~~~bash
node --experimental-strip-types --test src/Workspace/AiPanel/bookConversationController.test.ts src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/components/AgentConversation/panelView.test.ts src/components/AgentConversation/AssistantOutput/timeline.test.ts
npm run typecheck
npm run test:unit
~~~

Expected: all tests PASS; `Workspace/AiPanel` no longer owns a message list, Assistant body, execution log, task card, model picker or context-usage indicator.

- [ ] **Step 9: Commit Task 6 only**

~~~bash
git add src/Workspace/AiPanel src/components/AgentConversation
git commit -m "refactor(book): use shared agent conversation panel"
~~~

---

### Task 7: Delete Compatibility Paths and Add Architecture Gates

**Files:**
- Modify: `backend/tests/test_agent_refactor_boundaries.py`
- Modify: `package.json`
- Modify: `src/agent-runtime/index.ts`
- Delete: `src/Workspace/AiPanel/hooks/chat.types.ts` when no book-only declarations remain
- Delete: any temporary re-export left by Tasks 1–6

**Interfaces:**
- Consumes: final shared runtime/component imports.
- Produces: automated guards that prevent reverse dependencies and legacy component recreation.

- [ ] **Step 1: Add failing architecture assertions**

Extend `test_agent_refactor_boundaries.py`:

~~~py
SHARED_AGENT_FRONTEND_DIRS = (
    ROOT_DIR / "src" / "agent-runtime",
    ROOT_DIR / "src" / "components" / "AgentConversation",
)

LEGACY_AGENT_UI_PATHS = (
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "ChatMessageList",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "WorkLog",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "components" / "TaskPlanCard",
    ROOT_DIR / "src" / "Workspace" / "AiPanel" / "hooks" / "chat.types.ts",
)

def test_shared_agent_frontend_never_depends_on_product_directories():
    violations: list[str] = []
    for directory in SHARED_AGENT_FRONTEND_DIRS:
        for path in sorted((*directory.rglob("*.ts"), *directory.rglob("*.tsx"))):
            text = path.read_text(encoding="utf-8")
            if "Workspace/AiPanel" in text or "ScreenplayAgentPage" in text:
                violations.append(path.relative_to(ROOT_DIR).as_posix())
    assert not violations, "Shared Agent frontend imports product code:\n" + "\n".join(violations)

def test_legacy_agent_ui_paths_are_removed():
    assert not [path for path in LEGACY_AGENT_UI_PATHS if path.exists()]
~~~

Update `GENERIC_CHUNK_HANDLER_DIR` to `src/agent-runtime/chunkHandlers`.

- [ ] **Step 2: Run the boundary test and verify RED**

Run:

~~~bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python node scripts/run-backend-tests.cjs backend/tests/test_agent_refactor_boundaries.py
~~~

Expected: FAIL on the remaining product-local `chat.types.ts` compatibility path.

- [ ] **Step 3: Remove all compatibility paths**

Run `rg` before each deletion:

~~~bash
rg -n "Workspace/AiPanel/(components/(ChatMessageList|WorkLog|TaskPlanCard|Markdown|ModelPicker|ContextUsageIndicator)|hooks/(chat\\.types|chunkHandlers|chatHistory|streamOptions)|taskPlanSelection|contextUsage)" src
~~~

Update every legitimate import to `agent-runtime` or `components/AgentConversation`, then delete the old files. Do not add a second alias path.

Move `UseChatSubmitParams` and any remaining book-only submit types into `useChatSubmit.ts` before deleting `chat.types.ts`.

- [ ] **Step 4: Register final destination tests**

Replace removed paths in `package.json#test:unit` with:

- `src/agent-runtime/runtimeSelectors.test.ts`;
- `src/agent-runtime/chunkHandlers.test.ts`;
- `src/components/AgentConversation/AssistantOutput/timeline.test.ts`;
- `src/components/AgentConversation/ExecutionLog/state.test.ts`;
- `src/components/AgentConversation/panelView.test.ts`;
- screenplay and book controller tests.

- [ ] **Step 5: Run structure and type gates**

Run:

~~~bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python node scripts/run-backend-tests.cjs backend/tests/test_agent_refactor_boundaries.py
npm run typecheck
npm run test:unit
git diff --check
~~~

Expected: all commands PASS; `rg` finds no reverse shared dependency.

- [ ] **Step 6: Commit Task 7 only**

~~~bash
git add backend/tests/test_agent_refactor_boundaries.py package.json src/agent-runtime src/components/AgentConversation src/Workspace/AiPanel src/ScreenplayAgentPage
git commit -m "test(agent): enforce shared conversation boundaries"
~~~

---

### Task 8: Run Full Gates and Interaction-Faithful UI Verification

**Files:**
- Modify only if verification reveals a defect in a file already migrated by Tasks 1–7.
- Do not modify backend Agent semantics to make a frontend test pass.

**Interfaces:**
- Consumes: completed shared runtime, panel and both product controllers.
- Produces: release evidence for tests, build and both AI conversation surfaces.

- [ ] **Step 1: Run the full automated gates**

~~~bash
npm run check:agent-refactor
npm run build:web
git diff --check
~~~

Expected: every command PASS. If credential-backed real-provider tests are skipped, report them as a release blocker rather than calling them passed.

- [ ] **Step 2: Resolve the current UI process before starting anything**

~~~bash
lsof -nP -iTCP:5173 -sTCP:LISTEN
lsof -nP -iTCP:18321 -sTCP:LISTEN
~~~

For every reported PID, inspect its command and working directory. Reuse it only when it serves `/Users/liuyubin/Lybrand_project/PurrTypos` at the current commit. Otherwise leave it untouched and start the current Vite frontend on a free alternate port against the existing backend.

- [ ] **Step 3: Verify the screenplay Agent surface**

Using a persisted screenplay session and read-only navigation, verify:

- exactly one conversation index, one message viewport and one composer;
- Provider commentary/text arrives progressively;
- no hard-coded host summary appears before or after Provider text;
- consecutive operations are in one collapsed execution panel;
- expansion shows direct operation rows and individual timing;
- model operations are absent;
- sequential task progress shows `第 N/M 步` and parallel progress shows completion count;
- task capsule disappears after final output;
- stop ends active timing; resume remains available for paused durable work;
- artifact/review actions still open the screenplay domain panels.

- [ ] **Step 4: Verify the book AI surface**

Verify:

- the same shared panel markup/classes are present;
- chapter/global scope, memory/context controls, model selection and queue still work;
- a long conversation stays virtualized and scroll follow detaches/restores correctly;
- edit, copy, structured answer, approval, favorites and setting diff actions work;
- history search/open/delete works;
- execution, timing and task capsule behavior matches screenplay.

- [ ] **Step 5: Stop only processes started for verification**

Send the recorded Vite PID its normal termination signal, then verify its alternate port has no listener. Do not stop a process that existed before Task 8.

- [ ] **Step 6: Record final evidence**

~~~bash
git status --short
git log --oneline -8
~~~

Expected: only the user's unrelated pre-existing changes remain unstaged; implementation commits are present in task order.

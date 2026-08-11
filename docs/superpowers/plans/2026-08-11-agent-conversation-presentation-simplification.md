# Agent Conversation Presentation Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace screenplay Agent canned stage prose with concise user actions, make model-authored execution summaries optional, flatten operation history to one disclosure per continuous group, and make the task-progress capsule describe sequential versus parallel execution accurately before disappearing with the completed stream.

**Architecture:** The submitted User Turn records only the action the user actually clicked; Planner-owned public prose is optional and never synthesized by the host. Screenplay scope and review correctness remain backend domain contracts. The shared Agent UI projects structured operations into deterministic continuous groups with one disclosure and direct leaf rows, while task-plan visibility remains derived from the current streaming Assistant Turn.

**Tech Stack:** React 18, TypeScript, Node test runner, Python 3.12, pytest, durable screenplay Agent manifests/evidence, persisted SSE conversation replay.

## Global Constraints

- Do not hide rejected canned text with CSS or string filtering; stop creating it at the source.
- The short action submitted by an automatic button must match the visible action semantics, and must reuse the exact visible label whenever that label already contains the concrete scope such as an episode count.
- Keep the composer empty for a new or reopened empty session. Do not add another automatic prefill path.
- `executionSummary` is model-owned, optional, and capped only at the public projection boundary. Its absence must not trigger repair, failure, or host fallback prose.
- Keep `action`, `instruction`, `scope`, `constraints`, `preserve`, `requestedDeliverable`, and `reply` validation unchanged.
- Keep review correctness in Resolver, Manifest, Evidence, candidate validation, and publication. Do not move immutable-draft or five-dimension rules back into button prose.
- Every uninterrupted operation sequence, including a sequence with one operation, has exactly one disclosure. Expanding it must reveal leaf rows directly.
- Real model commentary is the only boundary between adjacent operation groups. Run IDs, units, tool batches, and replay packet boundaries are not presentation boundaries.
- Do not change the tool-event, task-plan, SSE, persistence, or replay wire contracts for this UI change.
- Determine parallel task-plan presentation only from `runningSteps.length > 1`.
- Keep the capsule visible only while the current Assistant Turn has `loading === true`; never revive it for history.
- Preserve unrelated worktree changes. Verification must not start a development server.

**Execution note:** Implemented in the current `feat/0.5.2` workspace. The planned production-source string scan was replaced with real action-output, Planner-stream, operation-projection, and lifecycle behavior tests so the gate protects user-visible contracts without freezing private source text.

---

## Task 1: Replace canned screenplay stage prompts with one concise action source

**Files:**

- Create: `src/ScreenplayAgentPage/stageAgentAction.ts`
- Create: `src/ScreenplayAgentPage/stageAgentAction.test.ts`
- Modify: `src/ScreenplayAgentPage/index.tsx`
- Modify: `package.json`

- [x] **Step 1: Write failing unit tests for concise action generation**

Create table-driven tests that cover original/book orientation, structure variants, the default draft action, multi-episode and all-remaining draft scopes, and review reruns. The key assertions must include:

```ts
assert.equal(stageAgentAction({
  project: project({ active_stage: 'review' }),
  reviewState: {
    phase: 'readyToFinalize',
    recommendation: 'revise',
    hardChecks: [{
      code: 'review_input_unverified',
      message: '当前审阅报告没有可验证的正文输入',
    }],
  },
}), '重新审阅')

assert.equal(stageAgentAction({
  project: project({ active_stage: 'draft', format: '连续剧' }),
  draftScope: 'next_3_episodes',
}), '连续创作 3 集')

assert.equal(stageAgentAction({
  project: project({ active_stage: 'draft', format: '连续剧' }),
  draftScope: 'all_remaining',
}), '创作全部剩余正文')
```

Also assert that the generated values do not contain task-rule prose such as `不得沿用`, `重点检查`, `并生成可应用`, or `只有缺少`.

- [x] **Step 2: Make the action test reject embedded task instructions**

For every generated automatic action, assert that the observable submitted value contains none of `不得沿用`, `重点检查`, `并生成可应用`, or `只有缺少`. This catches the rejected user-visible behavior while allowing private implementation names to change.

- [x] **Step 3: Run the new tests and confirm they fail for the current implementation**

Run:

```bash
node --experimental-strip-types --test src/ScreenplayAgentPage/stageAgentAction.test.ts
.venv/bin/python -m pytest backend/tests/test_agent_refactor_boundaries.py -q
```

Expected: the new module is absent, so the action behavior test fails before implementation.

- [x] **Step 4: Implement the shared concise action function**

Move the existing `stagePrimaryActionLabel()` decision into `stageAgentAction.ts`, preserving its accepted-document and review-state decisions. Add the optional draft scope before the default draft label:

```ts
export interface StageAgentActionInput {
  project: ScreenplayProject
  documents?: ScreenplayDocument[]
  draftEpisodes?: ScreenplayDraftEpisode[]
  documentEpisodes?: ScreenplayDocumentEpisode[]
  reviewState?: ScreenplayV2Workspace['workflow']['review']
  draftScope?: ScreenplayDraftScope
}

export function stageAgentAction({
  project,
  documents = [],
  draftEpisodes = [],
  documentEpisodes = [],
  reviewState,
  draftScope = 'next_episode',
}: StageAgentActionInput): string {
  if (project.active_stage === 'completed') return '创作已完成'
  if (project.active_stage === 'orientation') {
    return project.source_kind === 'book' ? '开始分析' : '生成创作简报'
  }
  if (project.active_stage === 'brief') return '生成创作简报'
  if (project.active_stage === 'structure') {
    return isSeriesFormat(project.format) ? '设计分集结构' : '设计故事节拍'
  }
  if (project.active_stage === 'scenes') return '生成场景表'
  if (project.active_stage === 'draft') {
    if (draftScope === 'all_remaining') return '创作全部剩余正文'
    const count = draftEpisodeCountFromScope(draftScope)
    if (count != null && count > 1) return `连续创作 ${count} 集`
    const sceneList = [...documents].reverse().find(
      (document) => document.kind === 'scene_list' && document.status === 'accepted',
    )
    const sceneCount = documentEpisodes
      .filter((episode) => episode.document_id === sceneList?.id)
      .reduce((total, episode) => total + episode.item_ids.length, 0)
    const completedCount = draftEpisodes.reduce(
      (total, episode) => total + episode.scene_ids.length,
      0,
    )
    if (sceneCount > 0 && completedCount >= sceneCount) return '完成剧本正文'
    return isSeriesFormat(project.format) ? '创作下一集' : '创作正文'
  }
  if (reviewState) return reviewPrimaryAction(reviewState).label
  return fallbackReviewAction(documents)
}
```

Keep `isSeriesFormat()` and `fallbackReviewAction()` private to this module. Their logic must be the existing label logic, not new task instructions.

- [x] **Step 5: Wire every automatic submission and visible label to the same function**

In `index.tsx`:

- delete `stageAgentStarter()` and the local `stagePrimaryActionLabel()`;
- render the main stage action label with `stageAgentAction(...)`;
- submit that same `stageAgentAction(...)` value in `handleStageStartAction`;
- make `startDraftRange(scope, actionLabel?)` submit the provided concrete label or fall back to `stageAgentAction({ ..., draftScope: scope })`;
- pass `DraftBatchAction.label` to `startDraftRange(action.key, action.label)` so the visible batch action and persisted User Turn are identical;
- submit `连续创作 ${episodeCount} 集` for a custom numeric range so its exact scope remains visible in history;
- preserve user-entered composer text and message editing unchanged.

Replace both empty-session prefill branches with an empty value:

```ts
setAgentPrompt('')
```

This applies after `conversationClient.load(...)` when `next.turns.length === 0` and after project initialization fallback. Do not clear a non-empty user draft during ordinary rendering.

- [x] **Step 6: Register and pass the focused tests**

Add `src/ScreenplayAgentPage/stageAgentAction.test.ts` to `test:unit`, then run:

```bash
node --experimental-strip-types --test src/ScreenplayAgentPage/stageAgentAction.test.ts
npm run typecheck
```

- [x] **Step 7: Commit the concise-action change**

```bash
git add src/ScreenplayAgentPage/stageAgentAction.ts src/ScreenplayAgentPage/stageAgentAction.test.ts src/ScreenplayAgentPage/index.tsx package.json
git commit -m "fix(screenplay): submit concise stage actions"
```

---

## Task 2: Make Planner execution summaries truly optional

**Files:**

- Modify: `backend/application/screenplay_agent_planner.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`

- [x] **Step 1: Write failing Planner tests for omitted and overlong summaries**

Add an end-to-end Planner case whose valid JSON omits `executionSummary`:

```py
planner_output = json.dumps({
    "action": "answer",
    "instruction": "说明当前阶段",
    "scope": {"kind": "current_stage"},
    "constraints": [],
    "preserve": [],
    "requestedDeliverable": None,
    "reply": "当前处于创作简报阶段。",
}, ensure_ascii=False)
```

Execute the Turn through `ModelScreenplayIntentPlanner` and assert:

- the Turn completes through the existing answer path;
- no `commentaryDelta` is persisted;
- the assistant reply is still `当前处于创作简报阶段。`;
- no repair invocation is made solely because the summary is missing.

Add a second case with `executionSummary` longer than 600 characters. Assert exactly 600 summary characters are publicly projected, the closing newline may follow the cap, and the otherwise valid Intent still executes.

- [x] **Step 2: Run the focused tests and confirm the omitted-summary case fails**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py -q -k "planner and summary"
```

Expected: `_validate_intent()` rejects the omitted summary. The projection-cap test should document the already-existing `JsonStringFieldProjector(max_value_chars=600)` behavior.

- [x] **Step 3: Change the Planner schema instruction without adding fallback prose**

Change the schema description to:

```text
"executionSummary": "可选。确有必要时用 1 至 3 句简述如何理解意图、选择动作和确定范围；不需要公开概述时省略或填空字符串，不得复述内部协议"
```

Add a rule stating that omission/blank is legal and remove the repair instruction's requirement that `executionSummary` be present. Keep all Intent fields and answer `reply` rules intact.

- [x] **Step 4: Remove summary validation from business Intent validation**

Reduce `_validate_intent()` to the domain mapping:

```py
def _validate_intent(value: dict[str, Any]) -> dict[str, Any]:
    return ScreenplayIntent.from_mapping(value).to_mapping()
```

Do not add a default value to `value`, `ScreenplayIntent`, the stream, or the conversation. Keep `execution_progress_fields={"executionSummary": ""}` so a model-supplied value is still projected. Keep the existing projector cap at 600 characters.

- [x] **Step 5: Re-run Planner and structured-stream regressions**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py -q -k "planner or execution_progress"
```

Confirm non-empty summaries remain model-verbatim, blank/omitted summaries emit nothing, and long summaries do not invalidate the Intent.

- [x] **Step 6: Commit the optional-summary change**

```bash
git add backend/application/screenplay_agent_planner.py backend/tests/test_screenplay_agent_rewrite.py
git commit -m "fix(agent): make planner summary optional"
```

---

## Task 3: Flatten continuous operation history to one disclosure and direct rows

**Files:**

- Modify: `src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.scss`
- Create: `src/Workspace/AiPanel/components/ToolCallStatus/presentation.ts`
- Create: `src/Workspace/AiPanel/components/ToolCallStatus/presentation.test.ts`
- Modify: `src/Workspace/AiPanel/components/ToolCallStatus/index.tsx`
- Modify: `src/Workspace/AiPanel/components/ToolCallStatus/index.scss`
- Modify: `src/Workspace/AiPanel/components/SubAgentStatusList/index.tsx`
- Modify: `src/Workspace/AiPanel/components/WorkLog/index.tsx`
- Modify: `src/Workspace/AiPanel/components/WorkLog/index.scss`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`
- Modify: `package.json`

- [x] **Step 1: Write failing deterministic grouping and progress tests**

Extend `modelRuntime.test.cjs` with these cases:

```js
test('one visible operation is still wrapped in one step group', () => {
  const grouped = groupConsecutiveWorkSteps([{
    type: 'tools',
    segmentIndex: 0,
    segment: { labels: ['读取人物资料'], completedToolCount: 1 },
  }], 4)

  assert.equal(grouped.length, 1)
  assert.equal(grouped[0].type, 'stepGroup')
  assert.deepEqual(grouped[0].parts.map((part) => part.type), ['tools'])
})
```

Add operation-progress cases proving:

- two completed visible steps plus the third live step report `{ total: 6, completed: 2, current: 3, active: true }`;
- cached tool labels are excluded from both numerator and denominator;
- terminal delegation/context operations contribute to completed count;
- model commentary still splits two groups exactly as the existing boundary test specifies.

- [x] **Step 2: Write failing direct-row presentation tests**

Create `ToolCallStatus/presentation.ts` tests for a three-row segment:

```ts
assert.deepEqual(buildToolCallRows({
  labels: ['读取人物资料', '检查人物弧光', '写入候选稿'],
  completedToolCount: 1,
}), [
  { index: 0, label: '读取人物资料', outcome: 'ok', phase: 'done', text: '已完成 读取人物资料' },
  { index: 1, label: '检查人物弧光', outcome: 'ok', phase: 'running', text: '正在执行 检查人物弧光' },
  { index: 2, label: '写入候选稿', outcome: 'ok', phase: 'pending', text: '待执行 写入候选稿' },
])
```

Also assert cached rows are absent and `context_error` remains an error row. No presentation result may contain `已操作`, `正在操作`, or `准备操作`.

- [x] **Step 3: Run the focused frontend tests and confirm current behavior fails**

Run:

```bash
node --experimental-strip-types --test src/Workspace/AiPanel/components/ToolCallStatus/presentation.test.ts
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
```

Expected: the new direct-row module is absent and a single operation currently remains ungrouped.

- [x] **Step 4: Always materialize an operation group and expose pure progress**

Change `flushSteps()` so every non-empty `stepParts` buffer creates a `stepGroup`:

```ts
items.push({
  type: 'stepGroup',
  groupKey: `${messageIndex}-work-steps-${groupStartIndex}`,
  parts: stepParts,
})
```

Export a pure `getOperationGroupProgress(parts)` from `assistantTimeline.ts`. For tools, count only uncached labels and map `completedToolCount` through their original label indexes. For delegations, treat `done`, `failed`, and `canceled` as terminal and `queued`, `claimed`, and `running` as active. Context compaction has one step. Return:

```ts
export interface OperationGroupProgress {
  total: number
  completed: number
  current: number
  active: boolean
}
```

When active, calculate `current` as the bounded frontier `completed + max(1, activeCount)`; when terminal, set it to `completed`. Keep failure state separate so an error icon does not alter step counting.

- [x] **Step 5: Make `ToolCallStatus` a leaf-row renderer**

Move row derivation into `presentation.ts` and render those rows directly from `ToolCallStatus`:

```tsx
export default function ToolCallStatus(props: ToolCallStatusProps) {
  const rows = buildToolCallRows(props)
  if (rows.length === 0) return null
  return (
    <div className="bubble-tool-call-details">
      {rows.map((row) => renderToolRow(row))}
    </div>
  )
}
```

Delete the component's `expanded` state, timer, `PurrButton`, chevron, duration summary, `getSummaryText()`, and all `已操作/正在操作/准备操作` copy. Remove now-unused `startedAt`, `durationMs`, and `streaming` props from both `AssistantMessageBody` and `SubAgentStatusList`; operation-group timing remains authoritative in the single disclosure.

- [x] **Step 6: Remove the outer WorkLog disclosure and keep one semantic wrapper**

Delete the default `WorkLog` component, `WorkLogProps`, its open-state store, and its outer auto-open effects. Keep `WorkLogStepGroup`, its stable per-group open state, duration ticker, and error icon.

Add `currentStepCount` to `WorkLogStepGroupProps` and render one complete label:

```tsx
<span>
  {active
    ? `正在执行 ${currentStepCount}/${stepCount} 个步骤`
    : `执行了 ${stepCount} 个步骤`}
</span>
```

In `AssistantMessageBody`, replace `<WorkLog ...>` with a non-disclosure wrapper rendered only when `hasWorkLog`:

```tsx
{hasWorkLog ? (
  <div className="work-log">
    {workLogItems.map(renderWorkLogItem)}
  </div>
) : null}
```

Every operation item now arrives as `stepGroup`; commentary remains a direct sibling between groups. Inside a group, call `ToolCallStatus` once per tools part so expansion reveals leaf rows immediately. Use `getOperationGroupProgress(part.parts)` for `stepCount`, `currentStepCount`, and `active`.

- [x] **Step 7: Remove obsolete styles instead of hiding obsolete controls**

Delete outer `.work-log__toggle`, chevron, collapsible, open-state, and body-padding rules. Retain `.work-log` only as the presentation wrapper and retain commentary, context, sub-agent, and step-group styles. Delete tool summary, chevron, and nested-details toggle styles while preserving leaf-row phase/error styles.

Update the standby adjacency selector so the absence of the old `.work-log__body` does not leave extra bottom spacing.

- [x] **Step 8: Register and pass focused UI tests**

Add `ToolCallStatus/presentation.test.ts` to `test:unit`, then run:

```bash
node --experimental-strip-types --test src/Workspace/AiPanel/components/ToolCallStatus/presentation.test.ts
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
npm run typecheck
```

- [x] **Step 9: Commit the operation-presentation change**

```bash
git add src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.scss src/Workspace/AiPanel/components/ToolCallStatus src/Workspace/AiPanel/components/SubAgentStatusList/index.tsx src/Workspace/AiPanel/components/WorkLog package.json src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
git commit -m "fix(ai-panel): flatten operation history"
```

---

## Task 4: Present sequential and parallel task-plan progress accurately

**Files:**

- Modify: `src/Workspace/AiPanel/taskPlanSelection.ts`
- Modify: `src/Workspace/AiPanel/hooks/modelRuntime.test.cjs`

- [x] **Step 1: Change the existing sequential test to the agreed ordinal contract**

The existing non-linear plan has one completed step while the third visible step is running. Change its assertions to:

```js
assert.equal(progress.completed, 1)
assert.equal(progress.currentStepIndex, 3)
assert.equal(getTaskPlanCountLabel(plan), '第 3/4 步')
```

This is the regression that prevents `已完成 1/4` from replacing the current sequential position.

- [x] **Step 2: Add parallel, planned, terminal-stream, and history lifecycle cases**

Assert:

```js
assert.equal(getTaskPlanCountLabel(parallelPlan), '并行 3 项 · 已完成 2/6')
assert.equal(getTaskPlanCountLabel(plannedPlan), '第 1/4 步')
assert.equal(getTaskPlanCountLabel(donePlan), '已完成 4/4')
assert.equal(getActiveTaskPlan([{ role: 'assistant', taskPlan: donePlan }], true), donePlan)
assert.equal(getActiveTaskPlan([{ role: 'assistant', taskPlan: donePlan }], false), undefined)
```

Add a history case in which an older Assistant message has a plan but the current last message does not; `getActiveTaskPlan(..., true)` must remain `undefined`.

- [x] **Step 3: Run the focused tests and confirm the ordinal assertion fails**

Run:

```bash
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
```

- [x] **Step 4: Add the visible-step index and select the count label by real concurrency**

Update `getTaskPlanProgress()`:

```ts
const currentStepIndex = currentStep ? steps.indexOf(currentStep) + 1 : 0
return {
  total,
  completed,
  runningSteps,
  currentStep,
  currentStepIndex,
  percent: total > 0 ? Math.round((completed / total) * 100) : 0,
}
```

Update the label selection:

```ts
export function getTaskPlanCountLabel(plan: AiTaskPlan): string {
  const {
    completed,
    total,
    runningSteps,
    currentStep,
    currentStepIndex,
  } = getTaskPlanProgress(plan)
  const active = plan.status === 'planned' || plan.status === 'running'
  if (active && runningSteps.length > 1) {
    return `并行 ${runningSteps.length} 项 · 已完成 ${completed}/${total}`
  }
  if (active && currentStep && currentStepIndex > 0) {
    return `第 ${currentStepIndex}/${total} 步`
  }
  return `已完成 ${completed}/${total}`
}
```

Do not alter `getActiveTaskPlan()`'s `if (!loading) return undefined` boundary. `TaskPlanCard` already appends the sequential current title and uses the terminal plan label, so no new component state is needed.

- [x] **Step 5: Re-run focused tests and typecheck**

Run:

```bash
node --test src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
npm run typecheck
```

- [x] **Step 6: Commit the task-plan presentation change**

```bash
git add src/Workspace/AiPanel/taskPlanSelection.ts src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
git commit -m "fix(ai-panel): show sequential task position"
```

---

## Task 5: Lock review authority and run the complete regression gates

**Files:**

- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Verify: `backend/tests/test_agent_refactor_boundaries.py`
- Verify: all production and test files changed in Tasks 1-4

- [x] **Step 1: Add an explicit review Resolver regression for the current immutable draft**

Use a fake `ScreenplayAgentContextQuery` whose `head_revision_refs()` returns the accepted revision references and whose `draft_revision_manifest()` returns two existing episodes for `draft-current`. Resolve a `review` Intent against a workspace whose accepted `screenplayDraft` Head is `draft-current`, then assert:

```py
assert resolved.reviewed_draft_id == "draft-current"
assert resolved.episode_scene_ids == {
    1: ("ep01_s01",),
    2: ("ep02_s01", "ep02_s02"),
}
```

The fake must record the revision ID it receives so the test proves the Resolver did not select an older candidate or review document.

- [x] **Step 2: Strengthen the existing five-dimension Evidence test**

In `test_review_dimension_parts_aggregate_host_side`, retain the existing `REVIEW_DIMENSIONS` assertions and add:

```py
assert len(tool_calls.user_payloads) == len(REVIEW_DIMENSIONS)
assert all(
    payload["reviewedDraftId"] == "draft-head"
    and payload["reviewInput"]["draftRevisionId"] == "draft-head"
    and "previousReview" not in payload
    and "acceptedReview" not in payload
    and "reviewReport" not in payload
    for payload in tool_calls.user_payloads
)
```

Also assert every dimension receives the same `contentDigest`. This makes deletion of the stage prompt safe without duplicating its prose in another prompt layer.

- [x] **Step 3: Run focused screenplay correctness and architecture tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_agent_refactor_boundaries.py -q
```

- [x] **Step 4: Run the canonical frontend verification**

Run:

```bash
npm run test:unit
npm run typecheck
```

- [x] **Step 5: Run the complete Agent refactor gate**

Run:

```bash
npm run check:agent-refactor
```

This is the required ownership, persistence, replay, acceptance, type, frontend-unit, and backend regression gate for the changed Agent path. Do not substitute a live-provider run for this deterministic gate.

- [x] **Step 6: Inspect final structure and rejected copy**

Run:

```bash
rg -n "stageAgentStarter|上一份报告不可用于定稿|已操作：|正在操作：|准备操作：" src backend/application
git diff --check
git status --short
git diff --stat
```

Expected: the rejected production strings and function are absent; any match in a test must be an explicit negative regression assertion. Confirm there is no outer WorkLog disclosure component or hidden inner ToolCall disclosure state.

- [x] **Step 7: Commit the final regression assertions if they were not included earlier**

```bash
git add backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_agent_refactor_boundaries.py
git commit -m "test(agent): gate concise conversation presentation"
```

- [x] **Step 8: Report verification evidence and remaining limits**

Report exact passing commands and the observable contracts they cover. State explicitly that deterministic fake-provider tests verify omission, projection, immutable review input, grouping, replay selection, and capsule lifecycle; do not claim a live-provider wording guarantee unless a separate real-provider run was performed.

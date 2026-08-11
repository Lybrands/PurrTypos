# Screenplay Review Interaction Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore screenplay review modals, review-decision controls, and the CURRENT TASK Agent shortcut while hiding internal review Revision IDs.

**Architecture:** Fix the modal lifecycle once in the shared `PurrModal` compatibility layer, then keep screenplay-specific action visibility and wording in the existing pure review presentation model. Reuse `runAgent(promptOverride)` for immediate Agent submission and the existing adjudication API for user decisions; do not add new state, endpoints, or dependencies.

**Tech Stack:** React 18, TypeScript, Base UI Dialog/Menu, Node test runner, Vite, existing Purr components.

## Global Constraints

- Clicking the CURRENT TASK primary review action immediately submits `处理审阅意见` to the Agent; it does not only fill the composer.
- The review modal keeps user adjudication separate from Agent work.
- The UI must not render full, truncated, or hashed `reviewRevisionId` values.
- Do not restore the removed long canned prompts.
- Do not change review persistence, state-machine, or API contracts.
- Do not add a DOM-testing dependency solely for this repair.
- Real-page checks may open menus and local confirmation UI but must not submit a review decision or otherwise mutate user business data.

---

### Task 1: Restore the Shared Modal Lifecycle

**Files:**
- Modify: `src/purr-components/PurrModal/PurrModal.tsx:32-53`

**Interfaces:**
- Consumes: controlled `PurrDialog(open, onOpenChange)` and Base UI Portal's default hidden-content unmounting.
- Produces: `PurrModal` always mounts the Dialog Root, while closed portal content remains unmounted by Base UI.

- [x] **Step 1: Reproduce the failing real behavior before editing**

On the current project page, click `处理审阅意见（194）`, `打开项目文档`, and `归档项目`. The pre-fix page has zero `.purr-dialog` elements after every click while ordinary state buttons such as `收起对话列表` work.

Expected: RED because state-driven modals do not open.

- [ ] **Step 2: Apply the minimal shared lifecycle fix**

Keep `destroyOnHidden?: boolean` in `PurrModalProps` for caller compatibility. In the component implementation, make exactly these removals:

```tsx
destroyOnHidden,
if (destroyOnHidden && !open) return null
```

Do not add timers, trigger IDs, wrapper state, or a new modal abstraction.

- [ ] **Step 3: Run static checks**

Run: `npm run typecheck`

Expected: PASS with no unused-property or JSX errors.

- [ ] **Step 4: Verify the same modal path turns GREEN**

Reload the local page, open the current screenplay project, click `查看审阅项（194）` or the pre-rename review entry, then inspect:

```text
.purr-dialog count = 1
.screenplay-review-adjudication count = 1
```

Close it, open `打开项目文档`, and confirm `.purr-dialog count = 1` again.

Expected: both modals open and close. Do not submit any business command.

- [ ] **Step 5: Commit the shared fix**

```bash
git add src/purr-components/PurrModal/PurrModal.tsx
git commit -m "fix(ui): restore controlled modal opening"
```

### Task 2: Restore the Review Task Agent Shortcut

**Files:**
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts:55-130`
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.ts:71-113`
- Modify: `src/ScreenplayAgentPage/stageAgentAction.test.ts:25-95`
- Modify: `src/ScreenplayAgentPage/index.tsx:2790-2890, 3875-3905`

**Interfaces:**
- Produces: `reviewAgentActionAvailable(input: { phase: ScreenplayV2ReviewPhase; hardChecks?: ReadonlyArray<{ code: string; message: string }> }): boolean`.
- Produces: adjudicating `reviewWorkspaceEntry(input)` value `{ label: '查看审阅项（N）', emphasis: 'secondary' }`.
- Consumes: existing `stageAgentAction(input) -> '处理审阅意见'` and `runAgent(promptOverride)`.

- [ ] **Step 1: Write failing pure presentation tests**

Add the import and assertions:

```ts
import {
  reviewAgentActionAvailable,
  reviewWorkspaceEntry,
} from './reviewAdjudicationModel.ts'

test('review task keeps an immediate Agent action while findings await adjudication', () => {
  assert.equal(reviewAgentActionAvailable({
    phase: 'adjudicating',
    hardChecks: [],
  }), true)
  assert.equal(reviewAgentActionAvailable({
    phase: 'readyToFinalize',
    hardChecks: [],
  }), false)
  assert.deepEqual(reviewWorkspaceEntry({
    phase: 'adjudicating',
    counts: { pending: 8 },
  }), { label: '查看审阅项（8）', emphasis: 'secondary' })
})
```

Extend the stage-action table with:

```ts
{
  input: {
    project: project({ active_stage: 'review' }),
    reviewState: {
      phase: 'adjudicating' as const,
      recommendation: 'revise' as const,
      hardChecks: [],
    },
  },
  expected: '处理审阅意见',
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
node --experimental-strip-types --test \
  src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts \
  src/ScreenplayAgentPage/stageAgentAction.test.ts
```

Expected: FAIL because `reviewAgentActionAvailable` does not exist and the adjudicating workspace entry still says `处理审阅意见（8）`.

- [ ] **Step 3: Implement the smallest pure projection**

Add:

```ts
export function reviewAgentActionAvailable(input: {
  phase: ScreenplayV2ReviewPhase
  hardChecks?: ReadonlyArray<{ code: string; message: string }>
}): boolean {
  if (reviewRequiresRerun(input)) return true
  return input.phase === 'awaitingReview'
    || input.phase === 'adjudicating'
    || input.phase === 'readyToRevise'
}
```

Change only the adjudicating workspace entry:

```ts
return {
  label: `查看审阅项（${input.counts.pending}）`,
  emphasis: 'secondary',
}
```

- [ ] **Step 4: Connect the page to the pure projection**

Replace the inline phase checks with:

```ts
const reviewUsesAgentAction = openedProject?.active_stage !== 'review'
  || !reviewState
  || reviewAgentActionAvailable(reviewState)
```

In `handleStageStartAction`, reject a review action only when a state exists and the helper returns `false`:

```ts
openedProject.active_stage === 'review'
  && reviewState
  && !reviewAgentActionAvailable(reviewState)
```

Keep the existing call unchanged:

```ts
runAgent(primaryStageAction)
```

This makes the primary `处理审阅意见` button immediately submit the short action and leaves `查看审阅项（N）` as the modal entry.

- [ ] **Step 5: Run focused tests and typecheck**

Run:

```bash
node --experimental-strip-types --test \
  src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts \
  src/ScreenplayAgentPage/stageAgentAction.test.ts
npm run typecheck
```

Expected: both commands PASS.

- [ ] **Step 6: Commit the shortcut repair**

```bash
git add \
  src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts \
  src/ScreenplayAgentPage/reviewAdjudicationModel.ts \
  src/ScreenplayAgentPage/stageAgentAction.test.ts \
  src/ScreenplayAgentPage/index.tsx
git commit -m "fix(screenplay): restore review task shortcut"
```

### Task 3: Hide the Review ID and Verify Decision Controls

**Files:**
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.ts`
- Modify: `src/ScreenplayAgentPage/ReviewAdjudicationPanel.tsx:138-152`
- Modify only if still reproduced: `src/purr-components/PurrDropdown/PurrDropdown.tsx`

**Interfaces:**
- Produces: `reviewVersionLabel(reviewRevisionId: string | null): string` returning `当前审阅版本` or an empty string without exposing the ID.
- Consumes: existing `PurrDropdown` and `onDecide(issueIds, status, note)` flow.

- [ ] **Step 1: Write the failing ID-redaction test**

```ts
test('review version label never exposes the internal Revision ID', () => {
  assert.equal(reviewVersionLabel('sprev_internal_secret_1234'), '当前审阅版本')
  assert.equal(reviewVersionLabel(null), '')
  assert.doesNotMatch(
    reviewVersionLabel('sprev_internal_secret_1234'),
    /internal|secret|1234/,
  )
})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`

Expected: FAIL because `reviewVersionLabel` does not exist.

- [ ] **Step 3: Implement and render the public label**

```ts
export function reviewVersionLabel(reviewRevisionId: string | null): string {
  return reviewRevisionId ? '当前审阅版本' : ''
}
```

Replace:

```tsx
{review.reviewRevisionId ? ` · 审阅版本 ${review.reviewRevisionId.slice(-8)}` : ''}
```

with:

```tsx
const versionLabel = reviewVersionLabel(review.reviewRevisionId)

{versionLabel ? ` · ${versionLabel}` : ''}
```

- [ ] **Step 4: Run focused and full frontend checks**

Run:

```bash
node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts
npm run test:unit
npm run typecheck
```

Expected: all commands PASS.

- [ ] **Step 5: Verify the real review UI without mutating data**

Reload the local page and confirm:

1. CURRENT TASK shows both `处理审阅意见` and `查看审阅项（194）`.
2. Clicking `查看审阅项（194）` opens `审阅与定稿`.
3. The header contains `当前审阅版本` and does not contain the review Revision ID suffix.
4. Clicking the first `待处理` opens the existing decision menu.
5. Check one pending item, click `批量处理（1）`, and confirm its menu opens.
6. Cancel or close every dialog without clicking `确认处理`.
7. Open and close `项目文档` as a shared-modal regression check.

If and only if step 4 or 5 still fails, inspect the rendered menu portal and make the smallest shared `PurrDropdown` portal-container correction, then repeat the same checks. Do not replace the menu with a new component.

- [ ] **Step 6: Run the complete Agent gate and patch hygiene checks**

Run:

```bash
npm run check:agent-refactor
git diff --check
```

Expected: architecture boundaries, model contracts, screenplay acceptance, TypeScript, frontend units, and backend tests PASS. Real-provider tests may keep their existing credential-based skips.

- [ ] **Step 7: Commit the public-label and regression changes**

```bash
git add \
  src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts \
  src/ScreenplayAgentPage/reviewAdjudicationModel.ts \
  src/ScreenplayAgentPage/ReviewAdjudicationPanel.tsx \
  src/purr-components/PurrDropdown/PurrDropdown.tsx
git commit -m "fix(screenplay): restore review decision controls"
```

- [ ] **Step 8: Confirm cleanup**

Run:

```bash
git status --short
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

Expected: the worktree is clean. Do not stop the pre-existing port `5173` process because this task did not start it; any process started by this task must already be stopped.

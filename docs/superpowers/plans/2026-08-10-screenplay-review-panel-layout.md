# Screenplay Review Modal Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the screenplay Agent conversation at full size and expose review adjudication as an on-demand modal from the current-task card.

**Architecture:** The Agent studio again owns the central `agent` grid area directly. A pure presentation helper derives the current-task review entry, and `ScreenplayAgentPage` opens the existing `ReviewAdjudicationPanel` inside a large `PurrModal` without moving adjudication state or persistence logic.

**Tech Stack:** React 18, TypeScript, Sass, Purr components, Node test runner, Chrome computed-layout verification.

## Global Constraints

- Do not change review state, API, persistence, or decision behavior.
- Do not place adjudication controls in the immutable project-document reader.
- Do not add another outer-page scrollbar.
- Only the review findings list may scroll inside the modal.
- Keep the Agent composer and conversation workspace at their original full height.
- Do not commit or push without an explicit user request.

---

### Task 1: Review Entry Presentation Contract

**Files:**
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.ts`

**Interfaces:**
- Consumes: `ScreenplayV2ReviewPhase` and review counts.
- Produces: `reviewWorkspaceEntry(review)` returning the modal-entry label and emphasis.

- [x] **Step 1: Write the failing test**

Add literal assertions for `adjudicating`, `readyToRevise`, `readyToFinalize`, and `completed`, including the pending count in the adjudicating label.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts
```

Expected: FAIL because `reviewWorkspaceEntry` does not exist.

- [x] **Step 3: Implement the minimal helper**

Return `null` for `awaitingReview`; otherwise return the exact label and `primary`/`secondary` emphasis required by the design.

- [x] **Step 4: Run the focused test and verify GREEN**

Run the Step 2 command. Expected: PASS.

### Task 2: Current-Task Entry and Modal

**Files:**
- Modify: `src/ScreenplayAgentPage/index.tsx`
- Modify: `src/ScreenplayAgentPage/index.scss`

**Interfaces:**
- Consumes: `reviewWorkspaceEntry(reviewState)` and the existing `ReviewAdjudicationPanel` callbacks.
- Produces: `reviewAdjudicationOpen`, the current-task entry button, and the modal presentation.

- [x] **Step 1: Remove the rejected permanent split**

Delete `.screenplay-project-main`, render `.screenplay-agent-studio` directly in `.screenplay-project-workspace`, and restore `grid-area: agent` plus full original sizing on the studio.

- [x] **Step 2: Add the task-card entry**

Render the entry whenever a current review exists. Keep “开始修订” as the main action in `readyToRevise`; render the review entry as a secondary action beside it.

- [x] **Step 3: Open adjudication in a large modal**

Render `ReviewAdjudicationPanel` in a `PurrModal` with no duplicate footer. Close it when the review disappears, and prevent close while a review mutation is running.

- [x] **Step 4: Bound modal layout**

Make the modal panel fill a viewport-bounded height and keep `overflow-y: auto` only on `.screenplay-review-adjudication__findings`.

### Task 3: Regression Verification

**Files:**
- Verify: live `http://localhost:5173/` project page in Chrome

- [x] **Step 1: Run automated gates**

```bash
npm run typecheck
npm run test:unit
npm run check:agent-refactor
git diff --check
```

- [x] **Step 2: Verify wide layout**

With the modal closed, assert no visible adjudication panel and confirm the Agent studio occupies the full central grid area. Open the review entry and confirm the large modal has an internally scrolling findings list.

- [x] **Step 3: Verify narrow layout and cleanup**

Check a sub-760px viewport, reset any viewport override, stop every service started for verification, and confirm ports `5173` and `18321` have no listeners.

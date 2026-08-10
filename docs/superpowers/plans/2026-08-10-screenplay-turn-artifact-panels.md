# Screenplay Turn Artifact Panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild one durable artifact panel for every screenplay conversation turn and make each panel open and apply its own exact project-document Revision.

**Architecture:** Compose a lightweight Revision summary into every Task in the persisted conversation read model, project all Task results into turn-keyed frontend artifacts, and bind UI actions to explicit `role + revisionId` targets. Keep full Revision content in the project-document API and use an explicit modal navigation target instead of inferring selection from Workspace state.

**Tech Stack:** Python 3.12, FastAPI application services, SQLite repositories, React, TypeScript, Node test runner, pytest.

## Global Constraints

- The canonical ownership relation is `Task.turnId + Task.resultRevisionId`; do not infer artifact ownership from copy, current stage, array position, or the latest global proposal.
- Conversation snapshots may include only lightweight Revision summaries, never Parts, full Markdown bodies, or structured content payloads.
- Historical replay and live completion must use the same turn-artifact projection.
- Every View and Apply callback must close over its own Revision ID and role.
- The ordinary project-document entry keeps default selection; only artifact entries provide an explicit target.
- Preserve unrelated uncommitted work and do not commit unless the user explicitly requests it.

---

### Task 1: Durable Task result read model

**Files:**
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`
- Modify: `src/types.ts`

**Interfaces:**
- Produces: `ScreenplayAgentTask.resultRevision: ScreenplayV2RevisionSummary | null`
- Produces: summary Revision responses with `status: 'current' | 'historical' | 'candidate'`

- [ ] **Step 1: Write failing backend snapshot tests**

Add a production-path assertion that a completed Task snapshot includes a `resultRevision` whose ID, role, revision number, title and proposal kind match the published Revision. Assert the object has no `parts`, `contentText` or `contentJson` keys. Add status assertions for candidate, current after Apply, and historical after a newer Revision becomes current.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_agent_durable_service.py -q
```

Expected: the new `resultRevision` and summary `status` assertions fail because snapshots currently expose only `resultRevisionId` and summary reads omit status.

- [ ] **Step 3: Implement the lightweight read-model composition**

Make `ScreenplayAgentService.get_snapshot()` load each non-empty `resultRevisionId` with `self._projects.get_revision(revision_id, view='summary')` and attach it as `resultRevision`. Catch only a missing legacy Revision and return `null`; propagate other failures. Update `SqliteScreenplayV2Repository.get_revision()` to classify the selected Revision as current when it is a project Head, historical when an acceptance event exists, and candidate otherwise. Do not request or attach full content.

- [ ] **Step 4: Extend the TypeScript Task contract**

Add `resultRevision: ScreenplayV2RevisionSummary | null` to `ScreenplayAgentTask`. Update complete test fixtures to include this field explicitly so partial mock shapes do not hide protocol drift.

- [ ] **Step 5: Re-run focused tests and verify GREEN**

Run the Step 2 command and confirm both files pass.

### Task 2: Turn-keyed artifact projection

**Files:**
- Modify: `src/ScreenplayAgentPage/conversationState.ts`
- Modify: `src/ScreenplayAgentPage/conversationState.test.ts`
- Modify: `src/ScreenplayAgentPage/index.tsx`

**Interfaces:**
- Produces: `ScreenplayTurnArtifact`
- Produces: `screenplayTurnArtifacts(tasks, workspace): Map<string, ScreenplayTurnArtifact>`
- Consumes: `ScreenplayAgentTask.resultRevision`

- [ ] **Step 1: Write failing projection tests**

Create two completed Tasks with different `turnId`, `targetRole` and `resultRevisionId` values. Assert the projector returns two artifacts keyed to the correct turns, retains each Revision ID and role, uses summary title/kind when available, and falls back to stable role metadata when `resultRevision` is null. Assert a Task without `resultRevisionId` produces no artifact. Add a Workspace case proving only the matching role Head is current.

- [ ] **Step 2: Run the focused frontend test and verify RED**

Run:

```bash
npx tsx --test src/ScreenplayAgentPage/conversationState.test.ts
```

Expected: FAIL because `screenplayTurnArtifacts` and `ScreenplayTurnArtifact` do not exist.

- [ ] **Step 3: Implement the pure artifact projector**

Add the type and function to `conversationState.ts`. Validate `summary.proposalKind` against supported document kinds; use the role label as the fallback display kind and a stable role-based title when summary metadata is missing. Determine effective status from the current Workspace Head and candidates before using the snapshot status.

- [ ] **Step 4: Render one panel after every matching Assistant message**

Replace the single `proposalTurnId`/`agentProposalAttachment` branch with a memoized turn-artifact map. In `afterAssistantMessage`, read the canonical conversation entry by index and render the artifact for that entry's `turnId`. Build `messageAttachmentsVersion` from all artifact Revision IDs and effective statuses so Apply updates refresh the right panels.

- [ ] **Step 5: Re-run the focused test and verify GREEN**

Run the Step 2 command and confirm it passes.

### Task 3: Revision-bound View and Apply actions

**Files:**
- Modify: `src/ScreenplayAgentPage/index.tsx`
- Modify: `src/ScreenplayAgentPage/screenplayProjectModel.ts`
- Modify: `src/ScreenplayAgentPage/screenplayProjectModel.test.ts`

**Interfaces:**
- Produces: `RevisionLibraryTarget = { role: ScreenplayV2DeliverableRole; revisionId: string }`
- Produces: `acceptAgentRevision(artifact: ScreenplayTurnArtifact): Promise<void>`
- Consumes: `ScreenplayTurnArtifact.revisionId` and `.role`

- [ ] **Step 1: Write failing action-target tests**

Add pure model assertions that an artifact entry produces `{ role, revisionId }`, while the ordinary document entry produces `null`. Add status reconciliation assertions for already-current and historical artifacts.

- [ ] **Step 2: Run the model test and verify RED**

Run:

```bash
npx tsx --test src/ScreenplayAgentPage/screenplayProjectModel.test.ts
```

Expected: FAIL because the explicit library-target helper is missing.

- [ ] **Step 3: Centralize project-document opening**

Add `revisionLibraryTarget` state and `openRevisionLibrary(target)` in the page. Artifact panels pass their own target; the ordinary launcher passes `null`. Clear the target when switching or closing projects so an old turn can never influence another project.

- [ ] **Step 4: Generalize Apply around an explicit Revision**

Replace the latest-global `saveAgentProposal`/`acceptAgentProposal` dependency with `acceptAgentRevision(artifact)`. Refresh Workspace, return success when the artifact is already the role Head, call `acceptScreenplayV2Revision` with the artifact Revision ID, retain downstream-invalidation confirmation, then synchronize Workspace and project documents. Track the loading Revision ID so only the clicked panel displays progress.

- [ ] **Step 5: Re-run the model test and verify GREEN**

Run the Step 2 command and confirm it passes.

### Task 4: Exact Revision selection in the project-document modal

**Files:**
- Modify: `src/ScreenplayAgentPage/RevisionLibraryModal.tsx`
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.ts`
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.test.ts`

**Interfaces:**
- Consumes: optional `target: RevisionLibraryTarget | null`
- Produces: `resolveRevisionLibrarySelection(...)`
- Produces: `mergeRequestedRevision(...)`

- [ ] **Step 1: Write failing modal-selection tests**

Assert that an explicit target role overrides `defaultRole`, an explicit target Revision overrides the first history item, and a requested Revision summary absent from the first page is merged exactly once into the version list. Assert a null target preserves the current default-role behavior.

- [ ] **Step 2: Run the modal model test and verify RED**

Run:

```bash
npx tsx --test src/ScreenplayAgentPage/revisionLibraryModel.test.ts
```

Expected: FAIL because the selection and merge helpers are absent.

- [ ] **Step 3: Implement explicit target initialization**

Add the optional target prop. On every closed-to-open transition, choose `target.role` when provided and pass `target.revisionId` as the preferred selection to history loading. Manual role changes clear the explicit preferred Revision and continue using ordinary history behavior.

- [ ] **Step 4: Support targets outside the first history page**

Set `selectedRevisionId` to the explicit ID even when it is absent from the first response. Load that Revision detail directly, validate its role, and merge its lightweight summary into history without duplicates. Keep it selected if history and detail requests settle in either order. On a true not-found response, show the error and do not select another version silently.

- [ ] **Step 5: Re-run the modal model test and verify GREEN**

Run the Step 2 command and confirm it passes.

### Task 5: Regression verification

**Files:**
- Verify all files changed in Tasks 1-4
- Modify `package.json` only if the canonical unit suite does not already include the changed tests

**Interfaces:**
- Consumes all preceding contracts

- [ ] **Step 1: Run focused frontend tests**

```bash
npx tsx --test \
  src/ScreenplayAgentPage/conversationState.test.ts \
  src/ScreenplayAgentPage/screenplayProjectModel.test.ts \
  src/ScreenplayAgentPage/revisionLibraryModel.test.ts
```

- [ ] **Step 2: Run focused backend tests**

```bash
.venv/bin/python -m pytest \
  backend/tests/test_screenplay_agent_rewrite.py \
  backend/tests/test_screenplay_agent_durable_service.py -q
```

- [ ] **Step 3: Run frontend type and unit gates**

```bash
npm run typecheck
npm run test:unit
```

- [ ] **Step 4: Run the Agent architecture gate**

```bash
npm run check:agent-refactor
```

- [ ] **Step 5: Inspect the final workspace**

```bash
git diff --check
git status --short
git diff --stat
```

Confirm the conversation snapshot contains no full Revision content, every artifact action is Revision-bound, the ordinary project-document launcher clears the explicit target, unrelated user changes remain intact, and no development service was started.

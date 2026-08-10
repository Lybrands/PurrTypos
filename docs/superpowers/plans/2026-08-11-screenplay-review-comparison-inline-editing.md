# Screenplay Review Comparison and Inline Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure screenplay review Runs receive the exact draft text they review, represent internal per-episode failures outside the findings list, and turn Project Documents into one Markdown-first view/edit workspace with draft-review comparison.

**Architecture:** The screenplay application layer will build a host-owned, digest-bound review packet for each episode while preserving the existing `candidate_write` tool boundary. Review aggregation and adjudication projection will carry explicit failed-episode facts. A focused review lookup API will let the frontend match a report to an exact draft Revision, while pure frontend models own comparison and dirty-navigation decisions and `RevisionLibraryModal` owns only orchestration.

**Tech Stack:** Python 3.11, FastAPI, SQLite, Pydantic, PurrA tool Runs, React 18, TypeScript, Node test runner, SCSS, Tiptap `KnowledgeMarkdownEditor`.

## Global Constraints

- Project documents already read screenplay text correctly; do not replace Revision storage or the Markdown renderer.
- `candidate_write` must continue to expose only `writeScreenplayCandidatePart` and `inspectScreenplayCandidate`.
- Internal execution failures must never become review `issues` or user-adjudicable findings.
- Review comparison must match both exact `reviewedDraftId` and exact episode number.
- The comparison pane is read-only; review decisions remain in the existing Review & Finalize modal.
- There must be only one Project Documents modal; inline edit mode must not render a second `PurrModal`.
- A one-part `document:main` document has no directory; multi-part directories show part titles but no count badge.
- Preserve all unrelated uncommitted workspace changes. Never use broad `git add`, checkout, reset, or cleanup commands.
- Because relevant files already contain approved uncommitted work, use explicit-path verification checkpoints. Do not create an implementation commit unless the staged diff can be proven to contain only this plan's changes.

---

## File Structure

### Backend review contract

- Modify `backend/application/screenplay_incremental_generation.py`: construct host-bound episode review packets, aggregate completed and failed episodes, and update the review prompt.
- Modify `backend/application/screenplay_agent_task_executor.py`: validate completed review fragments against host-owned digest and contract facts.
- Modify `backend/domains/screenplay/review_adjudication.py`: project failed episodes into hard checks without turning them into findings.
- Modify `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py`: materialize per-episode review status and query the newest report bound to an exact draft Revision.
- Modify `backend/application/screenplay_v2_service.py`, `backend/routers/screenplay_v2.py`: expose the exact-draft review lookup.
- Modify `backend/tests/test_screenplay_agent_rewrite.py`, `backend/tests/test_screenplay_project_aggregate.py`, `backend/tests/test_screenplay_v2_routes.py`: behavior-first regression coverage.

### Frontend document workspace

- Modify `src/types.ts`, `src/services/backendApi.ts`, `src/services/index.ts`: failed-episode state and exact-draft review lookup types.
- Modify `src/ScreenplayAgentPage/revisionLibraryModel.ts`: pure comparison, one-part-directory, edit-dirty, and guarded-navigation decisions.
- Modify `src/ScreenplayAgentPage/revisionLibraryModel.test.ts`: model-first tests.
- Modify `src/ScreenplayAgentPage/RevisionLibraryModal.tsx`: one modal with inline view/edit modes and review comparison.
- Modify `src/ScreenplayAgentPage/index.scss`: full-height fixed shell, independent scroll panes, desktop split and narrow-screen tabs.

---

### Task 1: Bind every review episode to exact host-provided text

**Files:**
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/application/screenplay_incremental_generation.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`

**Interfaces:**
- Produces: review input `{"contractVersion": 2, "draftRevisionId": str, "episodeNumber": int, "sceneIds": list[str], "draftContentText": str, "scenePlan": dict, "requiredContext": dict, "contentDigest": str}`.
- Produces: completed fragment facts `reviewStatus`, `reviewedContentDigest`, and `inputContractVersion` inside `contentJson`.
- Preserves: `candidate_write` tool access and current candidate Artifact lifecycle.

- [ ] **Step 1: Write the failing host-input test**

Extend `_IncrementalReviewContext.episode_context` to return a real `scenePlan`, `sceneTexts`, and previous continuity. In `test_review_generation_checkpoints_each_episode_and_aggregates_host_side`, assert:

```python
payload = tool_calls.user_payloads[0]
review_input = payload["reviewInput"]
assert review_input["contractVersion"] == 2
assert review_input["draftRevisionId"] == "draft-head"
assert review_input["episodeNumber"] == 1
assert review_input["sceneIds"] == ["scene-1"]
assert "第 1 集真实正文" in review_input["draftContentText"]
assert review_input["scenePlan"]["scenes"][0]["id"] == "scene-1"
assert len(review_input["contentDigest"]) == 64
assert "按需调用工具读取" not in tool_calls.system_instructions[0]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py::test_review_generation_checkpoints_each_episode_and_aggregates_host_side -q
```

Expected: FAIL because `reviewInput` and the host digest are absent.

- [ ] **Step 3: Add bounded input normalization and digest helpers**

In `screenplay_incremental_generation.py`, add pure helpers with these exact signatures:

```python
def _review_episode_input(
    *,
    reviewed_draft_id: str,
    episode_number: int,
    episode_context: Mapping[str, Any],
) -> dict[str, Any]:
    current_draft = dict(episode_context.get("currentDraft") or {})
    scene_plan = dict(episode_context.get("episode") or {})
    scene_ids = _review_scene_ids(current_draft, scene_plan)
    packet = {
        "contractVersion": 2,
        "draftRevisionId": reviewed_draft_id,
        "episodeNumber": episode_number,
        "sceneIds": list(scene_ids),
        "draftContentText": _review_draft_text(current_draft),
        "scenePlan": scene_plan,
        "requiredContext": {
            "previousEpisode": episode_context.get("previousEpisode"),
        },
    }
    return {**packet, "contentDigest": _review_content_digest(packet)}

def _review_draft_text(current_draft: Mapping[str, Any]) -> str:
    values = current_draft.get("sceneTexts") or ()
    text = "\n\n".join(
        str(item.get("contentText") or "").strip()
        for item in values
        if isinstance(item, Mapping)
        and str(item.get("contentText") or "").strip()
    )
    if not text:
        text = str(current_draft.get("contentText") or "").strip()
    if not text:
        raise ValueError("review episode draft text is empty")
    return text

def _review_content_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

`_review_draft_text` must preserve scene order, join non-empty `sceneTexts[*].contentText`, and raise `ValueError("review episode draft text is empty")` when no text exists. `_review_episode_input` must verify scene identity in both draft and plan before computing SHA-256 over canonical UTF-8 JSON.

- [ ] **Step 4: Pass the complete packet to the candidate Run**

Build `review_input` before `_checkpoint_candidate`, pass it as `user_payload["reviewInput"]`, and rewrite `_review_fragment_tool_instruction` so it says the host already supplied the full episode evidence and the model must not retrieve or invent missing text.

Extend `_validate_review_fragment_candidate` to receive `reviewed_content_digest` and record:

```python
content.update({
    "reviewStatus": "completed",
    "reviewedContentDigest": reviewed_content_digest,
    "inputContractVersion": 2,
})
```

- [ ] **Step 5: Run focused review and tool-boundary tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py::test_review_generation_checkpoints_each_episode_and_aggregates_host_side backend/tests/test_screenplay_tool_catalog.py::test_formal_stage_tool_access_never_mixes_reads_and_candidate_writes -q
```

Expected: PASS.

- [ ] **Step 6: Record a clean checkpoint**

Run `git diff --check -- backend/application/screenplay_incremental_generation.py backend/application/screenplay_agent_task_executor.py backend/tests/test_screenplay_agent_rewrite.py` and inspect the explicit-path diff before continuing.

---

### Task 2: Separate review execution failures from content findings

**Files:**
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_screenplay_project_aggregate.py`
- Modify: `backend/application/screenplay_incremental_generation.py`
- Modify: `backend/domains/screenplay/review_adjudication.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py`
- Modify: `src/types.ts`

**Interfaces:**
- Produces: `failedEpisodes: list[{episodeNumber, code, message, retryable, runId?}]`.
- Produces: `completedEpisodes: list[int]` and `reviewedEpisodes: list[int]`.
- Produces: `ScreenplayV2ReviewState.failedEpisodes` with no corresponding `findings`.

- [ ] **Step 1: Write failing aggregation tests**

Configure `_CheckpointingToolCalls(fail_once_key="2")` to fail episode 2 after its available attempt and assert the aggregate keeps episode 1, reports episode 2 in `failedEpisodes`, and never inserts the failure message into `issues` or `contentText`.

Add a `derive_review_state` test with:

```python
review_content={
    "reviewedDraftId": "draft-1",
    "verdict": "revise",
    "issues": [],
    "failedEpisodes": [{
        "episodeNumber": 2,
        "code": "model_output_truncated",
        "message": "第 2 集审阅失败",
        "retryable": True,
    }],
}
```

Assert `findings == []`, `canFinalize is False`, and `hardChecks` contains `review_episode_failed`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py -k "review_generation and failure" backend/tests/test_screenplay_project_aggregate.py -k review -q
```

Expected: FAIL because one fragment exception aborts aggregation and review state has no failed-episode projection.

- [ ] **Step 3: Add host-owned failure normalization**

Inject a `classify_failure` callback from `ScreenplayTaskModelCalls._generate_review_incrementally` using `classify_screenplay_run_failure`. In the per-episode loop, catch `Exception` around input construction and checkpoint execution, convert it to:

```python
{
    "episodeNumber": episode_number,
    "code": failure.code,
    "message": f"第 {episode_number} 集审阅失败",
    "retryable": failure.retryable,
}
```

Do not include `str(error)` in user-facing review content. Cancellation remains uncaught. Continue later episodes.

- [ ] **Step 4: Aggregate completed and failed episodes explicitly**

Build `issues`, `verdict`, and Markdown only from completed checkpoints. Store `failedEpisodes` separately. If there are no completed checkpoints, use a neutral aggregate `verdict="major_rework"`, an execution summary that says the review failed, and no fabricated content issues.

Materialize each review episode part with `reviewStatus="completed"` or `reviewStatus="failed"`; a failed part has no `issues`.

- [ ] **Step 5: Add finalization hard checks and public types**

Extend `derive_review_state` to normalize failed episodes, append one deterministic hard check summarizing their episode numbers, expose them as `failedEpisodes`, and leave `findings` sourced only from `review.issues`.

Add to `ScreenplayV2ReviewState`:

```typescript
failedEpisodes: Array<{
  episodeNumber: number
  code: string
  message: string
  retryable: boolean
  runId?: string | null
}>
```

- [ ] **Step 6: Run focused backend tests**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_project_aggregate.py -q
```

Expected: PASS.

---

### Task 3: Add exact-draft review lookup and comparison models

**Files:**
- Modify: `backend/tests/test_screenplay_v2_routes.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py`
- Modify: `backend/application/screenplay_v2_service.py`
- Modify: `backend/routers/screenplay_v2.py`
- Modify: `src/types.ts`
- Modify: `src/services/backendApi.ts`
- Modify: `src/services/index.ts`
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.ts`
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.test.ts`

**Interfaces:**
- Produces: `GET /screenplay/v2/projects/{project_id}/draft-revisions/{draft_revision_id}/latest-review` returning `ScreenplayV2RevisionDetail | null`.
- Produces: `reviewComparisonForPart(draft, review, selectedPart)` pure projection.
- Produces: `shouldShowRevisionDirectory(parts)`.

- [ ] **Step 1: Write failing API and model tests**

Backend test: create two draft Revisions and two review Revisions bound through `screenplay_revision_inputs`; assert each lookup returns only the newest review bound to the requested draft and an unmatched draft returns `data: null`.

Frontend model tests must assert:

```typescript
assert.equal(shouldShowRevisionDirectory([mainPart]), false)
assert.equal(shouldShowRevisionDirectory([episodePart, mainPart]), true)
assert.equal(reviewComparisonForPart(draftV6, reviewForV5, episode7).kind, 'unavailable')
assert.equal(reviewComparisonForPart(draftV6, failedReviewV6, episode7).kind, 'failed')
assert.equal(reviewComparisonForPart(draftV6, cleanReviewV6, episode7).kind, 'clean')
assert.equal(reviewComparisonForPart(draftV6, issueReviewV6, episode7).kind, 'issues')
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_v2_routes.py -k latest_review -q
node --experimental-strip-types --test src/ScreenplayAgentPage/revisionLibraryModel.test.ts
```

Expected: FAIL because the endpoint and model functions do not exist.

- [ ] **Step 3: Implement the repository query and route**

Query the `review` deliverable Revisions joined to `screenplay_revision_inputs` where `input_role='screenplayDraft'` and `input_revision_id=draft_revision_id`, ordered by `revision_no DESC LIMIT 1`. Reuse `get_revision(review_revision_id, include_content=True)` for the returned detail and verify both project and role ownership.

- [ ] **Step 4: Implement the frontend service and pure comparison projection**

Add `getScreenplayV2LatestReviewForDraft({projectId, draftRevisionId})` to service types and implementations.

Define:

```typescript
export type ReviewComparison =
  | { kind: 'unavailable'; message: string }
  | { kind: 'failed'; message: string; episodeNumber: number }
  | { kind: 'clean'; markdown: string; reviewRevisionId: string }
  | { kind: 'issues'; markdown: string; reviewRevisionId: string }
```

`reviewComparisonForPart` must reject mismatched `reviewedDraftId`, match episode parts by numeric key/`episodeNumber`, and render structured review payload through the existing Markdown conversion rather than JSON.

- [ ] **Step 5: Run API and frontend model tests**

Run the commands from Step 2. Expected: PASS.

---

### Task 4: Define inline edit and guarded-navigation state before changing JSX

**Files:**
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.ts`
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.test.ts`

**Interfaces:**
- Produces: `workingCopyDraftEquals(left, right): boolean`.
- Produces: `revisionLibraryNavigationDecision({editing, dirty, busy}): 'continue' | 'confirm' | 'block'`.

- [ ] **Step 1: Write failing dirty-state tests**

Cover unchanged drafts, edits to the main document, edits to any episode, busy save/publish, clean exit, and dirty document/version/modal-close navigation.

```typescript
assert.equal(workingCopyDraftEquals(savedDraft, savedDraft), true)
assert.equal(workingCopyDraftEquals(savedDraft, changedEpisodeDraft), false)
assert.equal(revisionLibraryNavigationDecision({ editing: true, dirty: true, busy: false }), 'confirm')
assert.equal(revisionLibraryNavigationDecision({ editing: true, dirty: false, busy: false }), 'continue')
assert.equal(revisionLibraryNavigationDecision({ editing: true, dirty: true, busy: true }), 'block')
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
node --experimental-strip-types --test src/ScreenplayAgentPage/revisionLibraryModel.test.ts
```

Expected: FAIL because the state functions do not exist.

- [ ] **Step 3: Implement canonical draft equality and navigation decision**

Compare `mainText` and ordered `partText` values directly; do not stringify Working Copy metadata. The navigation function must stay UI-agnostic and never open confirmations itself.

- [ ] **Step 4: Run the model tests and typecheck the module**

Run:

```bash
node --experimental-strip-types --test src/ScreenplayAgentPage/revisionLibraryModel.test.ts
npx tsc --noEmit
```

Expected: PASS.

---

### Task 5: Replace the nested editor modal with one comparison workspace

**Files:**
- Modify: `src/ScreenplayAgentPage/RevisionLibraryModal.tsx`
- Modify: `src/ScreenplayAgentPage/index.scss`
- Modify: `src/ScreenplayAgentPage/revisionLibraryModel.test.ts`

**Interfaces:**
- Consumes: Task 3 exact-draft review service and `ReviewComparison`.
- Consumes: Task 4 dirty/navigation decisions.
- Consumes: `revisionLibraryWorkspacePresentation(editing)` as the single source of view/edit body and action mode.
- Removes: the second `PurrModal` with class `screenplay-working-copy-modal`.

- [ ] **Step 1: Add a failing workspace-presentation behavior test**

Add `revisionLibraryWorkspacePresentation(editing)` to the model contract and test the consumer-visible mode:

```typescript
assert.deepEqual(revisionLibraryWorkspacePresentation(false), {
  body: 'reader',
  actions: 'revision',
})
assert.deepEqual(revisionLibraryWorkspacePresentation(true), {
  body: 'inlineEditor',
  actions: 'workingCopy',
})
```

The React component must consume this projection to choose its body and toolbar branches; browser acceptance in Task 6 verifies that no nested overlay remains.

- [ ] **Step 2: Run the presentation test and verify RED**

Run the focused Node test. Expected: FAIL because the presentation projection does not exist.

- [ ] **Step 3: Move edit actions into the existing toolbar**

While `workingCopy == null`, retain view actions. While editing, replace them with `退出编辑`, `保存草稿`, and `发布候选版本`. Keep document/version selectors visible. Route selector changes, outer modal close, and exit editing through one async `requestNavigation(action)` function using the three-option confirm contract.

`放弃未保存修改` restores the last saved `workingCopyEditorDraft(workingCopy)` and then performs the requested navigation; it does not delete the saved Working Copy.

- [ ] **Step 4: Render one shared directory and content body**

Compute `activeParts`, `activeSelectedPart`, and `activeMarkdown` from either Revision detail or Working Copy. Render the directory only when `shouldShowRevisionDirectory(activeParts)` is true, remove both numeric badges, and keep episode switching local without confirmation.

For non-screenplay roles, render either `Markdown` or `KnowledgeMarkdownEditor` in one full-width content pane.

- [ ] **Step 5: Add draft-review comparison**

When `role === 'screenplayDraft'`, load the exact matching review whenever `revisionDetail.id` changes. Render:

- left pane: Markdown in view mode or `KnowledgeMarkdownEditor` in edit mode;
- right pane: fixed header `审阅意见 · 基于 vN` and the comparison state;
- failed state: concise `第 N 集审阅失败` card, never a finding card;
- unavailable state: `该版本尚无对应审阅`;
- clean state: `本集未发现需要处理的问题`.

Keep adjudication buttons out of this component.

- [ ] **Step 6: Implement scroll and responsive CSS**

The modal shell and workspace use `min-height: 0` and `overflow: hidden`. Only the directory list, body pane, and review pane scroll. Desktop uses `minmax(0, 1.6fr) minmax(260px, 1fr)`; below the existing narrow-screen breakpoint, show `正文 / 审阅` tabs so neither pane becomes unusably narrow.

- [ ] **Step 7: Run frontend verification**

Run:

```bash
npx tsc --noEmit
node --experimental-strip-types --test src/ScreenplayAgentPage/revisionLibraryModel.test.ts
npm run check:purr-components
```

Expected: PASS.

---

### Task 6: Full regression and visual acceptance

**Files:**
- Verify all files from Tasks 1-5.

**Interfaces:**
- Produces: evidence that the combined backend and frontend behavior is stable.

- [ ] **Step 1: Run focused backend suites**

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_project_aggregate.py backend/tests/test_screenplay_v2_routes.py backend/tests/test_screenplay_tool_catalog.py -q
```

- [ ] **Step 2: Run frontend suites and build**

```bash
npm run typecheck
npm run test:unit
npm run build:web
```

- [ ] **Step 3: Run Agent architecture gates**

```bash
npm run check:agent-refactor-boundaries
```

- [ ] **Step 4: Inspect the UI in Chrome**

Start only the required Web development processes. Verify:

1. Project Documents has one modal.
2. Screenplay Draft episode 7 shows body and only its matching review.
3. Edit mode changes the body pane in place and leaves review visible.
4. Editing two episodes preserves both local values.
5. Closing dirty edit mode shows all three decisions.
6. Source Analysis has no directory.
7. No directory header shows a count.
8. Long directory, body, and review content scroll independently without an outer scrollbar.
9. Narrow width switches between body and review tabs.

- [ ] **Step 5: Stop every process started for visual verification**

Record process IDs when starting the server, terminate only those processes, and verify ports used by the run no longer have listeners.

- [ ] **Step 6: Final diff audit**

Run `git diff --check`, inspect `git status --short`, and review explicit diffs for every touched file. Confirm no unrelated user changes were reverted or staged.

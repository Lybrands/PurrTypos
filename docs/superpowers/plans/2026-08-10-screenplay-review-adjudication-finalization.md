# Screenplay Review Adjudication and User Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Agent-verdict-controlled screenplay completion with persisted per-finding adjudication, batch decisions, deterministic finalization gates, and an explicit user “确认定稿” command.

**Architecture:** Keep immutable Agent review Revisions as evidence, add a separate review-adjudication projection and audit records keyed by `reviewRevisionId + issueId`, and add an idempotent finalization event bound to the current draft and review Revisions. The backend workspace is the authoritative read model; the React page renders a focused review panel and only sends explicit commands.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite, pytest, React 18, TypeScript, Base UI/Purr components, Node test runner, Sass.

## Global Constraints

- Agent `verdict` is advisory and never creates a new `completed` project.
- Every Agent review finding must leave `pending` before the workflow can proceed.
- `planned` means “accepted for revision” and blocks finalization.
- Only `resolved`, `dismissed`, and `riskAccepted` count as final decisions.
- Deterministic validation failures cannot be dismissed or risk-accepted.
- Even an empty `ready` report requires the user to click “确认定稿”.
- Agent review Revisions remain immutable; user decisions are stored separately.
- Commands must be atomic, CAS-guarded, and idempotent with the existing command-receipt pattern.
- Existing completed projects remain completed through an explicit legacy compatibility marker, not a fabricated user finalization event.
- Do not commit or push without an explicit user request.

---

### Task 1: Pure Review Workflow State

**Files:**
- Create: `backend/domains/screenplay/review_adjudication.py`
- Modify: `backend/domains/screenplay/project_aggregate.py`
- Modify: `backend/tests/test_screenplay_project_aggregate.py`

**Interfaces:**
- Produces: `derive_review_state(draft_revision_id, draft_content, review_revision_id, review_content, decisions, hard_checks, completion_source) -> dict[str, object]`.
- Produces: `derive_stage(..., has_current_finalization: bool = False, legacy_completed: bool = False) -> str`.
- Produces workspace phases: `awaitingReview | adjudicating | readyToRevise | readyToFinalize | completed`.

- [ ] **Step 1: Write failing domain tests**

Add tests proving that `verdict=ready` without finalization remains `review`, a report with missing decisions is `adjudicating`, all-final decisions are `readyToFinalize`, any `planned` decision selects `readyToRevise`, and only a matching finalization/legacy marker yields `completed`.

- [ ] **Step 2: Run the focused tests and confirm the old auto-completion behavior fails**

Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_project_aggregate.py -q`

Expected: FAIL because `derive_stage` still treats `verdict=ready` as completed and no review-state function exists.

- [ ] **Step 3: Implement the pure state projection**

Normalize review issues by ID, join persisted decisions without mutating review content, calculate counts, deterministic errors, `canFinalize`, `phase`, and the next workflow action. Change `derive_stage` so completion depends on a current user finalization or the explicit legacy marker, never the Agent recommendation.

- [ ] **Step 4: Re-run focused domain tests**

Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_project_aggregate.py -q`

Expected: PASS.

### Task 2: Durable Schema and Workspace Read Model

**Files:**
- Modify: `backend/database/screenplay_v2_schema.py`
- Modify: `backend/database/crud/screenplay_project_deletion.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py`
- Modify: `backend/tests/test_screenplay_v2_schema.py`
- Modify: `backend/tests/test_screenplay_v2_routes.py`

**Interfaces:**
- Creates tables `screenplay_review_decisions`, `screenplay_review_decision_events`, and `screenplay_finalization_events`.
- Adds nullable `screenplay_projects.completion_source` with values `user | legacyAgentVerdict`.
- Extends workspace with `review: ScreenplayV2ReviewState`.
- Produces repository commands `adjudicate_review(...)` and `finalize_project(...)`.

- [ ] **Step 1: Write failing schema and repository tests**

Cover idempotent schema initialization, legacy completed-project marking, project deletion cleanup, workspace `review` projection, atomic batch decisions, stale review rejection, pending/planned finalization rejection, successful user finalization, idempotent replay, and invalidation after accepting a new draft/upstream Revision.

- [ ] **Step 2: Run focused persistence tests and confirm failure**

Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_v2_schema.py backend/tests/test_screenplay_v2_routes.py -q`

Expected: FAIL because the new tables, workspace field, and repository methods do not exist.

- [ ] **Step 3: Add the idempotent schema migration**

Create current-decision and append-only decision-event tables; create finalization events; add indexes on project/review IDs; add and backfill `completion_source='legacyAgentVerdict'` only for already-completed projects. Include all new project-owned tables in hard deletion.

- [ ] **Step 4: Build the workspace review projection**

Read the current draft and review Heads, main Part payloads, decisions for the current review, deterministic validation results, and a matching finalization event. Feed those values through `derive_review_state`; use the result to derive `project.stage`, `workflow.nextActions`, and `workflow.review`.

- [ ] **Step 5: Implement atomic decision and finalization commands**

`adjudicate_review` validates every requested issue against the current review before writing any row, upserts current state, appends audit events, increments project Revision, records a command receipt, and writes an outbox event. `finalize_project` re-reads all gates inside one transaction, rejects pending/planned/hard-check failures, records the snapshot hash and finalization event, sets `active_stage='completed'` and `completion_source='user'`, and is replay-safe.

- [ ] **Step 6: Invalidate completion without deleting history**

When accepting a new Revision changes a Head, clear the current compatibility/completion projection and derive the active stage without matching old finalization events. Preserve decision and finalization rows for audit/history.

- [ ] **Step 7: Re-run focused persistence tests**

Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_v2_schema.py backend/tests/test_screenplay_v2_routes.py -q`

Expected: PASS.

### Task 3: Public Command Contracts and Routes

**Files:**
- Modify: `backend/schemas/screenplay_v2.py`
- Modify: `backend/application/screenplay_v2_service.py`
- Modify: `backend/routers/screenplay_v2.py`
- Modify: `backend/tests/test_screenplay_v2_routes.py`

**Interfaces:**
- Produces `AdjudicateScreenplayV2ReviewRequest(expectedProjectRevision, reviewRevisionId, decisions)`.
- Each decision is `{issueId, status, note}` where status is `pending | planned | resolved | dismissed | riskAccepted`.
- Produces `FinalizeScreenplayV2ProjectRequest(expectedProjectRevision, draftRevisionId, reviewRevisionId)`.
- Adds `POST /screenplay/v2/projects/{project_id}/review-decisions`.
- Adds `POST /screenplay/v2/projects/{project_id}/finalize`.

- [ ] **Step 1: Add failing wire-contract tests**

Test extra-field rejection, duplicate/empty issue IDs, invalid statuses, note normalization/limits, route idempotency keys, CAS conflicts, and workspace responses.

- [ ] **Step 2: Run the route tests and confirm failure**

Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_v2_routes.py -q`

Expected: FAIL because request classes and routes are missing.

- [ ] **Step 3: Implement Pydantic contracts, application normalization, and routes**

Follow the existing `ScreenplayV2Model(extra='forbid')`, `_command_id`, request-digest, and service-to-repository patterns. Both commands return the refreshed authoritative workspace.

- [ ] **Step 4: Re-run route tests**

Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_v2_routes.py -q`

Expected: PASS.

### Task 4: Frontend Types, Service Calls, and Pure Presentation Model

**Files:**
- Create: `src/ScreenplayAgentPage/reviewAdjudicationModel.ts`
- Create: `src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`
- Modify: `src/types.ts`
- Modify: `src/services/backendApi.ts`
- Modify: `src/services/index.ts`
- Modify: `package.json`

**Interfaces:**
- Produces `ScreenplayV2ReviewFindingStatus`, `ScreenplayV2ReviewFinding`, `ScreenplayV2ReviewState`, and typed Backend API methods.
- Produces pure helpers for selected IDs, selectable pending findings, exact batch action labels/counts, and finalization-disabled reason.

- [ ] **Step 1: Write failing pure frontend tests**

Test a six-finding example, select-all-pending behavior, exclusion of already-decided findings, explicit action labels, `planned` blocking finalization, and remaining-count messages.

- [ ] **Step 2: Run the new test and confirm failure**

Run: `node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`

Expected: FAIL because the model module does not exist.

- [ ] **Step 3: Add types, API methods, and pure helpers**

Add `adjudicateScreenplayV2Review` and `finalizeScreenplayV2Project` using `apiPostIdempotent`; register both in `services.screenplay`; add the new test to `npm run test:unit`.

- [ ] **Step 4: Run the new test and TypeScript check**

Run: `node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`

Run: `npm run typecheck`

Expected: both PASS.

### Task 5: Review Adjudication and Finalization UI

**Files:**
- Create: `src/ScreenplayAgentPage/ReviewAdjudicationPanel.tsx`
- Modify: `src/ScreenplayAgentPage/index.tsx`
- Modify: `src/ScreenplayAgentPage/index.scss`
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`

**Interfaces:**
- `ReviewAdjudicationPanel` consumes the authoritative workspace review state plus `readOnly`, `busy`, `onBatchDecision(issueIds, status)`, `onFinalize()`, and `onStartRevision()`.
- The page owns network calls, command IDs, workspace refresh, confirmation dialogs, and Agent revision start.

- [ ] **Step 1: Extend failing presentation tests**

Add cases for the exact primary action per phase: start review, process findings, start revision, confirm finalization, and completed. Prove that no UI path treats `ready` as completed.

- [ ] **Step 2: Run presentation tests and confirm failure**

Run: `node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`

Expected: FAIL for missing action projection.

- [ ] **Step 3: Implement the focused review panel**

Render current draft/review versions, Agent recommendation, counts, issue severity/scene references, selection checkboxes, per-item decision actions, “选择全部待处理项”, and a batch dropdown with the four explicit actions. Confirm bulk operations with the exact count; keep decisions editable.

- [ ] **Step 4: Connect project workflow commands**

Show the panel during `review` and `completed`; disable the existing CURRENT TASK Agent shortcut while adjudicating or ready to finalize; start revision only when all findings are triaged and at least one is `planned`; call finalization only after an explicit user confirmation. Refresh workspace/documents after every successful command and use server errors as the authority on stale state.

- [ ] **Step 5: Add responsive, readable styles**

Keep the finding list content-first, avoid nested full-page scrolling, make batch actions and remaining-count feedback visible, and stack controls on narrow widths.

- [ ] **Step 6: Run frontend tests and typecheck**

Run: `npm run test:unit`

Run: `npm run typecheck`

Expected: both PASS.

### Task 6: Regression and Architecture Verification

**Files:**
- Modify only if a failing regression exposes a contract defect in the files above.

**Interfaces:**
- Confirms schema, API, state-machine, frontend, and Agent boundaries remain consistent.

- [ ] **Step 1: Run screenplay acceptance tests**

Run: `npm run test:screenplay-acceptance`

Expected: PASS.

- [ ] **Step 2: Run the complete Agent refactor gate**

Run: `npm run check:agent-refactor`

Expected: boundary checks, screenplay acceptance, TypeScript, frontend unit tests, and the complete backend suite all PASS.

- [ ] **Step 3: Run patch hygiene checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 4: Verify process cleanup**

No development service is required for deterministic verification. If a service is started for optional visual QA, stop every process started by this task and verify its ports have no listeners before handoff.

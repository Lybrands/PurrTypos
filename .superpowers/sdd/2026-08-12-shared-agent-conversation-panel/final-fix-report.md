# Shared Agent conversation panel — final fix report

## Request-acceptance receipt design

The renderer-generated `streamId` is the Writing product command ID. Durable
Book requests use a two-step protocol: an idempotent reservation is committed
before the streaming POST is allowed to start expensive context preparation.
This removes the pre-response-header ambiguity without treating a timeout or a
temporarily empty Run query as a business terminal.

### State machine and identity

`ai_writing_chat_requests` is a product-owned table, not part of PurrA:

- `request_id TEXT PRIMARY KEY`: the normalized `streamId`.
- `session_id INTEGER NOT NULL` and `request_digest TEXT NOT NULL`: immutable
  ownership and canonical request identity. A repeated ID with a different
  session or digest is rejected with HTTP 409.
- `status`: `accepted -> starting -> run_bound`; a pre-Run request may instead
  move monotonically to `rejected` or `canceled`. A terminal state is never
  reopened.
- `run_id TEXT UNIQUE`, `cancel_requested_at_ms`,
  `cancel_applied_run_id`, `rejection_code`, and monotonic `revision` record the
  control-plane result without duplicating prompts, provider credentials, or
  Run output.

The SHA-256 digest is computed from the normalized executable request, excluding
`streamId`, and including session, messages, provider/model/options, Writing
owner/context selections, mode, locale, and a one-way SHA-256 identity of the
API key. The raw API key and a second prompt/output copy are never stored in the
receipt; the credential identity prevents replaying one command under a
different account.

### Transaction boundaries and crash windows

1. `PUT /ai/chat/requests/{requestId}` validates all pure request contracts and,
   in a cancellation-linearizable transaction, verifies the session and inserts
   `accepted`. Replaying the same ID/body returns the same receipt.
2. `POST /ai/chat/stream` recomputes the digest and claims
   `accepted -> starting`. A duplicate POST never submits a second Run; it
   attaches to receipt recovery.
3. After context preparation and immediately before Core submit, the lifecycle
   rechecks cancellation. `canceled` means provider/Core submission is skipped.
4. Core Run creation keeps its generic repository contract. The existing opaque
   `RunBinding(namespace='writing.chat.request', aggregate=session,
   command=requestId)` is validated when the product lifecycle binds the Run.
   `starting -> run_bound` and transfer of a pending cancel to
   `ai_agent_runs.cancel_requested_at_ms` occur in one transaction. Replays are
   idempotent; `cancel_applied_run_id` proves the effect was applied once.
5. Exceptions before Run binding CAS the receipt to `rejected`; exceptions after
   binding remain authoritative Run/journal failures. A disconnect only detaches
   SSE delivery.

Crash outcomes are therefore explicit: reservation without start remains
`accepted`; crash during preparation leaves `starting`; crash after Run insert
but before receipt bind is repaired by exact immutable RunBinding lookup; and a
bound Run is recovered by the existing Run lease/journal path. Startup recovery
never restarts credential-bound work: unbound `accepted/starting` becomes
`rejected` unless a durable cancel intent makes it `canceled`, while an exact
bound Run is repaired and then handled by normal Run recovery.

### Stop, query, deletion, retention, and compatibility

`POST /ai/chat/requests/{requestId}/cancel` stores a monotonic cancel intent. It
can terminalize an accepted/pre-submit request without inventing a Run result;
after binding it transfers to the existing Run cancel control plane exactly
once. The existing latest-session product query accepts `requestId` and returns
the receipt plus an optional Run snapshot, so recovery can distinguish
`accepted/starting`, `run_bound`, and authoritative pre-Run `rejected/canceled`.

Session deletion shares the same database transaction: `accepted/starting`
receipts block deletion, while terminal receipts are deleted with the session.
Reservation and stream claim both recheck session ownership under the write
lock. This closes delete-vs-request orphan races without changing the generic
PurrA Run repository contract.

This wave performs no time-based receipt GC. Terminal receipts remain until
their session is deleted, preserving idempotent replay and reload recovery. A
future retention job may delete only terminal receipts that are neither the
session's latest receipt nor linked to a retained Run.

Agent-mode legacy callers that do not advertise the receipt protocol continue
using the existing single POST behavior. The current Book renderer always
reserves an Agent request before POST and retries an ambiguous PUT with the same
ID/body; only an explicit validation/conflict response is authoritative
non-acceptance. Ask remains transport-local, but its terminal Conversation save
uses a stable `clientTurnId` plus an expected-history frontier. Response-loss
replay is backed by a separate `ai_local_conversation_turn_receipts` product
receipt. Its canonical full-payload digest makes the same command idempotent and
a different payload conflicting. Exact truncation monotonically changes the
receipt to `retired`, or pre-creates a nullable-payload tombstone when the save
has not materialized yet, so a lost response or late renderer cannot recreate
an edited-away turn. A concurrent Ask/Agent projection cannot fork the current
persisted history. An empty lookup or elapsed time never creates a synthetic
terminal.

### Receipt regression boundary

Tests cover reservation replay/conflict, response-loss replay, accepted while
preparation is deferred, cancel-before-submit, cancel between submit and bind,
later exact Run association, duplicate POST, restart repair, wrong-binding
rejection, session deletion races, exact-once cancel transfer, terminal receipt
cleanup, legacy POST compatibility, and renderer recovery for every receipt
state. All provider behavior is faked.

Local-turn receipt tests separately cover schema initialization, first save,
same/different-digest replay, save-before-truncate with a lost DELETE response,
truncate-before-save, double truncation, session-scoped identities, an
unmaterialized tombstone, kept legacy-turn protection, response-loss replay of
explicit-memory deposition, canonical JSON migration backfill (with malformed
legacy JSON failing closed), session/book/Screenplay-project deletion cleanup,
and the mounted Book edit command carrying every tail `clientTurnId`.

## Finding evidence

Base: `1676a2ab5c43581bdaa4a185aa10496a3a4edf88`.

### 1. Authoritative Book Stop

- RED: a slow durable Book Run could be detached and locally terminalized;
  Stop before a Run ID had no durable owner. The deferred regressions in
  `useChatSubmit.behavior.test.mjs`, `bookRunControl.test.ts`,
  `writingChatRequestReceipt.test.ts`, and the Writing request route/store tests
  exercised pre-ID Stop, deferred prepare/bind, disconnect, duplicate POST, and
  repeated cancel.
- GREEN: Book Agent Stop writes a request cancel intent, retains SSE, transfers
  the intent to the existing Run cancel plane once, and settles only from an
  authoritative request/Run terminal. Ask alone retains transport abort.
- Release-stability RED/GREEN: Run creation had already attached a delegated
  child, then the coordinator repeated the attach and could mark the delegation
  failed; the SQLite adapter also read connection-global `changes()`. Exact
  same-worker/same-child replay is now transactionally idempotent and emits no
  duplicate status event, while conflicting identities still fail closed.
- Main paths: `backend/infrastructure/persistence/writing_chat_request_store.py`,
  `backend/application/writing_chat_request_lifecycle.py`,
  `backend/routers/ai.py`, `src/services/writingChatRequestReceipt.ts`,
  `src/Workspace/AiPanel/hooks/bookRunControl.ts`, and
  `src/Workspace/AiPanel/hooks/useChatSubmit.ts`.

### 2. Lossless Book live-to-reload projection

- RED: fresh history trusted partial client Conversation rows, transport EOF
  synthesized a terminal, running reopen could start a second Run, pagination
  lost later events, and failed/canceled partial public output disappeared.
- GREEN: hydration and disconnect recovery replay all authoritative Run pages
  through the canonical reducer, preserve structured approval/error/termination
  state, normalize final text, recover the latest active Run, and never turn
  error codes or durable-dispatch receipts into Assistant text. Local terminal
  saves are client-turn idempotent through a durable persisted/retired receipt
  and history-frontier checked; ambiguous response loss is replayed before queue
  drain, explicit conflicts unlock without draining stale work, and Stop can
  cancel an unavailable persistence retry.
  Startup and the live orphan monitor also reconcile every terminal root
  `writing.chat.request` Run that lacks its Conversation, including a failure on
  the first projection attempt; the exact binding/aggregate filter prevents a
  colliding Screenplay session ID from entering Book history.
- Evidence: `bookConversationHydration.test.ts`,
  `durableAgentStreamRecovery.test.ts`, `test_agent_run_queries.py`,
  `test_conversations_routes.py`, `test_run_execution_control.py`,
  `test_main_lifespan.py`, and the mounted/hook terminal-save sequences.
- Main paths: `bookConversationHydration.ts`,
  `durableAgentStreamRecovery.ts`, `conversationSessionLifecycle.ts`,
  `backend/application/agent_run_queries.py`, and
  `backend/routers/conversations.py`.

### 3. No host-authored Assistant body

- RED: missing-key and host validation paths placed an explanation in
  `content`; terminal fallback could expose failure/dispatch metadata as prose.
- GREEN: host failures keep `content === ''` and use structured
  `error/isError/termination`; only public provider output becomes authored
  content. The actual missing-key submission and live/fresh terminal parity are
  behavior-tested in `useChatSubmit.behavior.test.mjs`,
  `modelRuntime.test.cjs`, `chunkHandlers.test.ts`, and
  `bookConversationHydration.test.ts`.

### 4. Frozen queued request identity

- RED: queued requests read the current Book, chapter, model, mode, scope, and
  selections when drained.
- GREEN: `QueuedChatSubmission` captures the complete request envelope and the
  A-to-B deferred behavior test changes every binding before drain, then asserts
  the wire request remains A. Display projection remains generic.
- Main paths: `chatQueue.ts`, `useChatSubmit.ts`, and
  `useChatSubmit.behavior.test.mjs`.

### 5. Proposal occurrence identity and reload

- RED: cards, resolutions, and Provider state were keyed only by entity
  `sessionKey`; the process-global attachment map was the only durable-looking
  source, and Run snapshots omitted the private proposal effect.
- GREEN: the product Run read model joins committed proposal journal events to
  tool receipts and emits stable `(runId, toolCallId, effectIndex)` occurrence
  IDs without changing PurrA's public runtime whitelist. Immutable payload comes
  from the Run projection; aliases/resolution live in the existing Conversation
  `agent_process`. Resolution is journal-validated CAS, and setting mutation plus
  committed resolution share one cancellation-linearizable transaction.
  Same-entity proposals queue by `proposalId`; active/resolved cards cannot
  rewrite each other. Stores are Book/session owner-bounded, deletion tombstones
  prevent late snapshot revival, and the mounted Provider filters background
  proposals from another Book.
- Evidence: `test_agent_run_queries.py`,
  `test_setting_diff_resolution_atomicity.py`,
  `bookConversationHydration.test.ts`, `bookProposalProjection.test.ts`,
  `settingDiffOccurrenceQueue.test.ts`, and the mounted
  `SettingDiffContext.behavior.test.mjs`.
- Main paths: `writing_proposal_read_model.py`,
  `book_conversation_product_projection.py`, `bookAssistantAttachments.ts`,
  `settingDiffProjection.ts`, and `SettingDiffContext.tsx`.

### 6. Hydration and editor session isolation

- RED: initialization disabled only the viewport, a late A load could commit
  after A-to-none, drafts/edit indices crossed session identity, and pending RAF
  work survived a switch.
- GREEN: a monotonic load epoch guards reads/actions and invalidates on
  no-session/unmount; drafts are session-owned; Submit/Enter/edit are defended in
  the shared selector and product action; viewport edit targets carry stable
  session/message identity and reset RAF/live cursor on switch.
- Evidence: `conversationSessionLifecycle.test.ts`,
  `viewportSession.test.ts`, `Panel.behavior.test.mjs`, and the real mounted
  deferred A-to-B Enter/click/editor sequence in
  `Panel.sessionIsolation.behavior.test.mjs`.

### 7. History deletion lifecycle and orphan safety

- RED: delete completion used stale active-session state, a deleting tab could
  reopen, active Runs/tasks could be orphaned with SQLite FK off, and edit resend
  raced deletion/materialization. A delayed local Ask save could also recreate
  a turn after exact truncation deleted its only idempotency row.
- GREEN: deletion is keyed, cannot reopen/select pending IDs, rereads current
  state, and orders failures against newer loads. Backend deletion and truncation
  use one write transaction, reject active Run/request/LongTask ownership,
  unlink/retire exact Run and receipt frontiers, archive conversation-sourced
  memories, clean reports, and preserve idempotent exact-truncation replay.
  Local Ask commands use an independent `(session_id, client_turn_id)` receipt;
  truncation retires or pre-tombstones each exact tail identity in the same
  transaction, and session/book deletion cleans the receipts. Resend awaits
  truncation and Stop cannot start a replacement during the gap.
- Evidence: `bookConversationController.test.ts`,
  `useChatSubmit.behavior.test.mjs`, `test_session_deletion.py`, and
  `test_conversations_routes.py`.
- These backend changes are product session-orphan and history-linearization
  safeguards; they do not alter Agent Run execution or provider semantics.

### 8. Stop/Resume mutual exclusion

- RED: Resume did not include `stopping`, and React state allowed cancel and
  resume commands for one Screenplay Operation revision.
- GREEN: the shared selector disables Resume while stopping; a synchronous,
  revision-keyed command latch retains accepted cancel until authoritative state
  observes it, releases safely on owner changes, and keeps visible stopping
  state aligned with the action guard.
- Evidence: `panelView.test.ts`,
  `screenplayOperationCommandLatch.test.ts`, and Screenplay controller tests.

### Minor review items

- `_source_between` now rejects reversed anchors, covered by
  `test_source_between_rejects_reversed_anchors`.
- Shift+Space detaches scroll follow. The real LinkeDOM-mounted scroller test
  dispatches the keyboard event and verifies wheel/touch/scrollbar listener
  cleanup after unmount.
- Proposal attachment lifetime is bounded by Book/session manager ownership,
  successful session-deletion eviction, and deleted-session tombstones; there is
  no magic item cap or process-global singleton source of truth.

## Verification

Fresh verification from the final working tree:

- `PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python npm run check:agent-refactor`: PASS.
  - architecture boundaries: 52 passed;
  - model contracts: 12 passed;
  - Screenplay acceptance: 118 passed;
  - TypeScript typecheck: passed;
  - mandatory unit/mounted behavior: 321 passed;
  - proposal/receipt/recovery projection: 27 passed;
  - full backend: 1479 passed, 4 skipped.
- `npm run build:web`: PASS (1087 modules transformed; only the existing chunk
  size advisory).
- `git diff --check`: PASS.
- Focused final local-save/frontier/deletion suite: 67 passed.
- Focused final Book submit/Stop/persistence sequences: 16 passed.

The four skipped credential-backed provider checks are **RELEASE BLOCKERS**, not
passes:

1. `DEEPSEEK_API_KEY`: deepseek-v4-flash reasoning-on Screenplay E2E.
2. `DEEPSEEK_API_KEY`: deepseek-v4-flash reasoning-off Screenplay E2E.
3. `ZAI_API_KEY`: glm-5.2 Screenplay E2E.
4. `MIMO_API_KEY`: mimo-v2.5-pro Screenplay E2E.

No paid provider request, live credential, UI server, or user persistent data was
used in this wave.

## Residual risks and compatibility

- The enhanced receipt protocol is product-versioned. Current Book Agent calls
  use it; legacy Agent callers without the v1 marker retain the pre-existing
  single-POST path for compatibility and therefore do not gain pre-header
  request-receipt recovery until upgraded.
- Ask remains non-Agent and transport-local by product design, but current Book
  Ask persistence is command-idempotent and history-frontier checked through the
  local-turn receipt. Legacy callers that omit `clientTurnId` and the frontier
  retain legacy last-writer behavior for compatibility.
- Setting mutation and resolution are atomic inside this product. The immutable
  proposal payload remains in the existing Run journal/tool receipt rather than
  being duplicated into Conversation JSON.
- The four credential-backed provider E2Es above still require release-time
  execution.

## Commit record

- Implementation and regression coverage:
  `7d821c6` (`fix(agent): harden shared conversation lifecycle`).
- This report is committed as the immediate report-only descendant; its exact
  full SHA is included in the final handoff.

Both commits descend from base
`1676a2ab5c43581bdaa4a185aa10496a3a4edf88`.

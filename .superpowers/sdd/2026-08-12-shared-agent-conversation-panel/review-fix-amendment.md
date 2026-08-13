# Shared Agent Conversation Panel — scoped review-fix amendment

Date: 2026-08-13

Base: `491532edee56381e39b03b0f09d1b396d3f2d208`

## Scope and boundaries

This amendment authorizes only the seven independently reproducible review
findings listed below. It does not change the PurrA/provider protocol, public
runtime event whitelist, tool/output semantics, or generic Agent meaning. It
does not add a route, product API, migration, or table. Existing product
control planes, journals, receipts, ownership columns, and cancellation-
linearizable transactions remain authoritative.

Each finding is implemented one at a time with a failing behavior regression
before the production fix. An incorrect review claim is rejected with evidence
rather than patched. Any newly discovered requirement outside this scope, or a
need for new schema/API, is reported as `NEEDS_CONTEXT` before implementation.

## Invariants and RED acceptance tests

### 1. Recovered Book terminal projection owns settlement until commit

The recovered Run retains the session's loading/stream ownership while its
terminal Conversation projection is being refetched or reconstructed. A
settled projection may commit only when its captured `{sessionId,
runtimeRevision, expectedRunId}` still owns the current runtime. Queue release
and a new Run may happen only after that authoritative projection commits.

RED: defer R1's terminal Conversation refetch, attempt/start R2, then resolve
R1. Assert R2 cannot be released early and no stale R1 commit can replace R2's
messages, loading state, or stream identity.

### 2. Setting proposal mutation is an immutable-journal CAS

For character, entity, and background commits, the same cancellation-
linearizable transaction must:

1. load the proposal occurrence from the Run journal/product read model;
2. require the request `before` and `proposed` snapshots to equal that immutable
   occurrence;
3. require the current target row to equal the journal `before` snapshot;
4. require the requested final mutation to be a legal composition of the
   reviewed before/proposed diff; and
5. bind replay identity to a server-computed canonical digest of target,
   journal `before/proposed`, and the reviewed final mutation; and
6. only then update the target, history, and resolution receipt.

Any mismatch is a product conflict (`409`) and writes nothing. RED covers all
three target kinds plus a valid mixed accept/reject mutation, forged replay,
and exact idempotent replay.

### 3. Product-owner deletion and request start share one ownership rule

Book/session/screenplay-project deletion use one transaction-scoped ownership
predicate over owned Conversations and sessions. Deletion rejects active root
or child Runs, accepted/starting Writing requests, active LongTasks/work items,
and queued/running/paused Screenplay Operations. Terminal deletion unlinks or
removes the same complete owner set, including request/local-turn receipts,
reports, summaries, favorites, operation/task rows, and terminal Run links.

Writing request `before_submit` rechecks the owning session and receipt under a
cancellation-linearizable transaction. Therefore claim -> delete ->
before_submit has exactly one winner: either deletion conflicts, or submit
observes the deleted owner and creates no Run.

RED covers direct Book deletion, Screenplay project deletion, terminal cleanup,
and the claim/delete/before-submit interleaving.

### 4. Screenplay hydration identity guards UI and domain actions

The Screenplay adapter exposes a conversation identity containing both
`sessionId` and monotonic load epoch. While authoritative hydration is in
progress, composer submit is disabled and send/edit are synchronously rejected
by the domain action layer. Drafts and edit targets belong to the captured
identity; a late A load/action cannot send or truncate against B.

RED is a mounted deferred A -> B sequence using real Enter, button click, and
editor submission, with assertions at both adapter and domain action seams.

### 5. Legacy Book root terminal holes remain product-identifiable

Terminal root Runs with no conversation are reconciled when either they carry
the modern `writing.chat.request` binding or their live `ai_sessions` row is
authoritatively Book-owned (`book_id` present and not screenplay-scoped).
Screenplay-owned sessions/Runs remain excluded even when numeric IDs collide.

RED covers restart recovery of an unbound legacy Book root and a colliding
Screenplay root that must not materialize as a Book Conversation.

### 6. Explicit memory reactivation distinguishes source truncation

No new memory schema is introduced. History truncation/session deletion marks
its archived source as `source_type='conversation_truncated'` in the same
transaction that removes the Conversation. A later explicit command may
reactivate and rebind only that source-truncation tombstone. A user-archived
`source_type='conversation'` memory remains archived even if another writer
later removes its source row. Fingerprint deduplication continues to prevent a
second memory row.

RED sequence: explicit remember -> truncate source -> same explicit fact from
a new Conversation -> same memory ID becomes active/pinned and points at the
new source. A companion manual-archive sequence must remain archived.

### 7. SettingDiff provider eviction follows deleted session ownership

After successful terminal Book-session deletion, the Book attachment manager
and mounted SettingDiff provider receive the same owner eviction. The provider
atomically removes matching active occurrences, queued occurrences, resolved
state, and command-latch ownership, tombstones late completions for that
session, then activates the next still-valid queued occurrence for each entity.
No eviction happens on tab close or failed deletion.

RED is a mounted provider sequence with session A active, session B queued for
the same entity, A deletion, and a late A command completion. B must become and
remain active; no A resolution or editor state may return.

## Verification

Focused RED/GREEN commands are recorded per finding in `final-fix-report.md`.
After all seven are green, run from a fresh process:

```text
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python npm run check:agent-refactor
npm run build:web
git diff --check
```

The four credential-gated live-provider E2Es remain release blockers unless
their credentials are supplied; they are never reported as passing coverage.

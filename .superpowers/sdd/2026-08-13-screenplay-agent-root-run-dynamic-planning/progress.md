# SDD ledger — plan: docs/superpowers/plans/2026-08-13-screenplay-agent-root-run-dynamic-planning.md

Branch: codex/agent-root-run-public-plan
Base: e4a6537
Pre-flight: common prerequisite and Writing dynamic planning implementation complete. Writing full gate: 1590 passed, 5 credential-blocked provider E2E; writing-method exact revision binding remains an explicit unrelated release gap.

Task 5: PASS. Screenplay Conversation Turns now submit one shared Root Run; answer and formal durable lifecycles, Root-ordered phase barriers, bounded full-transaction completion retry, strict persisted identity, terminal mapping, and cancellation are covered. Full Agent refactor gate: 1666 passed, 5 credential-blocked provider E2E.

Task 6: PASS. Screenplay AI Parts now run as host-orchestrated PurrA Children through shared AgentRunService lifecycle ownership; deterministic Parts create no Run, Child planning/delegation stay disabled, and persisted lineage/cancellation are covered.

Task 6 review: PASS. Host-child cancellation watcher cleanup is leak-free on all exits. Structured Child results are atomically journaled as private versioned canonical output and returned only by strict persisted readback; process-local validator state is no longer authoritative.

Task 6 durable retry review: PASS. Host-owned Child attempts now have a durable opaque idempotency receipt and generation CAS. Crash/restart and concurrent retries reuse the authoritative persisted Run, terminal retry policy is explicit, validated terminal replay identity is strict, and project aggregate cleanup owns receipt deletion while session audit retention is unchanged.

Task 6 candidate validation review: PASS. Task-specific Candidate normalization is now a persisted versioned terminal projection contract shared by tool and host-capture paths. Invalid output cannot produce DONE/finalized state; failed generations retry through the durable receipt, while stable-key identity covers the validation digest. Full gate: 1725 backend passed, 5 credential-blocked provider E2E, 344 frontend passed.

Task 6 candidate contract parser review: PASS. One pure strict parser now canonicalizes every Candidate validation kind before Child/receipt creation and is reused by DomainContext, projection, normalization, and persisted verification. Invalid/body-bearing contracts create no durable state; digest case canonicalization preserves one stable identity. Full gate: 1741 backend passed, 5 credential-blocked provider E2E, 344 frontend passed.

Task 7: IN PROGRESS. Auditing the Screenplay Recipe milestone boundaries and the durable observer/pause feedback seam before adding checkpoint planning. Task 1 remains the sole owner of public todo revision events.

Task 7: PASS. Screenplay durable work now performs bounded LLM Root plan revision only at episode, document-batch, and review-aggregate checkpoints. Crash-safe CAS receipts reconcile ready output with authoritative Root revision events; current plans come from persisted Root state, private content/IDs stay out of planner input, scope changes and invalid planning pause the business lifecycle, and Core remains the only todo publisher. Full gate: 1754 backend passed, 5 credential-blocked provider E2E, 344 frontend passed.

Task 8: IN PROGRESS. Auditing shared Root cancellation, transport detach, continuation-Root binding, resumable Recipe ownership, lineage-based usage, and cleanup before writing persistent RED tests.

Task 8: PASS. Root cancellation is now a persistent fenced tree lifecycle shared by generic and Screenplay routes; canonical terminal projectors atomically settle business state and lineage-based usage. Explicit resume creates one durable continuation Root over the existing Recipe, SSE disconnect only detaches, orphan recovery uses canonical commits, and truncate/aggregate cleanup wait for terminal ownership then remove only owned runtime receipts. Full gate: 1840 backend passed, 5 credential-blocked provider E2E, 344 frontend passed.

Task 9: PASS. The independent Screenplay Planner module and identity are retired; new snapshots/controllers use rootRunId only, one versioned decoder consumes historical plannerRunId input, SQLite keeps only its physical compatibility column, and the shared canonical reducer now has complete Root/Recipe/Child/checkpoint/Candidate/final live-replay acceptance. Full gate: 1846 backend passed, frontend/typecheck passed, 5 credential-blocked provider E2E.

Task 10: IN PROGRESS. Auditing the paid Screenplay Provider gate and replacing its provider-smoke/finalizer-only coverage with the real Root planning, durable Recipe, Child, checkpoint, cancellation, continuation, Candidate, publish, and lineage-usage workflow. Missing credentials remain explicit release blockers.

Task 10: PASS (credential-independent gates). The paid Screenplay E2E now uses the real composition/service/provider lifecycle and covers answer, formal cancel, persisted pause/continuation, Candidate, idempotent publish, Child lineage, no-zombie cleanup, semantic public plans, and usage aggregation. Full gate: 1848 backend passed, 5 credential-blocked provider E2E, 347 frontend passed. The deterministic requires-reresolution pause is test-controlled; all four paid profiles remain RELEASE BLOCKER until run with real credentials.

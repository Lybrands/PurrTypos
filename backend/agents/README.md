# Agent replacement boundary

The authoritative migration plan is
[`docs/design/2026-09-12-three-agent-purra-native-rebuild-plan.md`](../../docs/design/2026-09-12-three-agent-purra-native-rebuild-plan.md).

`backend/agents/` is the canonical home for the production implementations of
the writing, novel-analysis, and screenplay Agents. Their retired executable
implementations have been deleted; there is no legacy create or execution
fallback.

## Dependency direction

- `agents.shared` owns PurrA integration shared by all replacement Agents.
- Each product Agent may import `agents.shared`.
- `agents.shared` must not import a product Agent.
- Replacement code must not import a legacy Agent profile, service, executor,
  prompt, tool catalog, or projector.
- Historical compatibility is read-only and must stay behind explicitly named
  adapters. New Runs never write through more than one implementation.

## Routing rule

Every newly created Run persists an implementation and contract version. That
version is immutable for the Run: resume, cancellation, recovery, and replay
must use the recorded implementation identity. Production creation routes all
three Agent kinds to their PurrA-native profiles. Historical legacy Runs remain
queryable through versioned readers, while attempts to execute an uninstalled
legacy implementation fail closed.

Composition roots, routers, schemas, and shared persistence remain the shared
migration seams for versioned reads and current execution.

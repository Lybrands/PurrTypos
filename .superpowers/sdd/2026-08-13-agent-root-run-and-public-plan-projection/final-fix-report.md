# Final review fix report

## Status and scope

PASS for the requested final-review fix wave. Implementation commit:
`dcae4ae fix(agent): preserve root and profile ownership`.

The patch stays inside the common prerequisite branch. It does not change
`packages/purra/**`, migrations, or database schema/repository contracts. The
product factory still installs exactly Writing and Screenplay, the shared
frontend contains no Writing/Screenplay branch, and Root todo events remain the
only source of the public plan.

## Finding 1 — foreign canonical events cannot seize Root ownership

### RED

Command:

```bash
node --experimental-strip-types --test src/agent-runtime/chunkReplay.test.cjs
```

Observed result: exit 1, 9 passed and 2 failed.

```text
expected agentRunId root-run-a; actual child-run-a
expected agentRunId run-a; actual foreign-child
```

The tests asserted the complete protected projection: `agentRunId`,
`canonicalOutput.runId`, Root provider response, and the paused Root plan.

### GREEN

The canonical handler now binds live delivery to the renderer stream/turn and
accepts a different Run as a paused Root continuation only for the first public
`run.lifecycle(status=running)` envelope with the authoritative turn binding.
Every other direct foreign envelope is consumed without mutating canonical
state, response, plan, persistence identity, or cancellation identity. Replay
uses its persisted turn binding, while live Book chat uses its stream ID.

Command:

```bash
node --experimental-strip-types --test \
  src/agent-runtime/canonicalOutput.test.ts \
  src/agent-runtime/chunkHandlers.test.ts \
  src/agent-runtime/chunkReplay.test.cjs \
  src/Workspace/AiPanel/bookConversationHydration.test.ts \
  src/Workspace/AiPanel/hooks/useChatSubmit.behavior.test.mjs
```

Result: exit 0, 61 passed, 0 failed.

Files:

- `src/agent-runtime/chunkHandlers/canonical.ts`
- `src/agent-runtime/chunkHandlers/types.ts`
- `src/agent-runtime/chunkReplay.ts`
- `src/agent-runtime/chunkReplay.test.cjs`
- `src/Workspace/AiPanel/hooks/useChatSubmit.ts`

## Finding 2 — `create_core` has no order-dependent product default

### RED

Command:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_composition.py -q \
  -k 'requires_an_explicit_profile or rejects_additional_profile_factories or disables_delegation_when_profile_has_no_roles'
```

Observed result: exit 1, 3 failed. `create_core("key")` did not raise,
`create_agent_composition(..., profile_extension_factories=())` did not raise,
and a no-role Screenplay-shaped request crashed on `None.definitions`.

### GREEN

`AgentComposition.create_core` now requires `agent_profile`. Application and
child orchestration always call `create_core_for_request`, so product selection
is derived from the request domain rather than registry ordering. Direct tests
that intentionally assemble a Core pass their profile explicitly. No generic
Writing fallback was added.

Focused GREEN command:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_composition.py -q
```

Result: exit 0, 44 passed.

Files:

- `backend/application/agent_composition.py`
- `backend/application/agent_run_service.py`
- `backend/application/agent_delegation_adapter.py`
- `backend/tests/test_agent_composition.py`
- the Screenplay and lifecycle fake-composition tests updated to satisfy the
  now-explicit composition protocol

## Finding 3 — Run snapshots and delegation resolve persisted profile ownership

### RED

No-role application command (included with Finding 2) failed because
`AgentRunService` assumed every profile had roles.

Cross-profile route command:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_run_queries.py -q \
  -k 'screenplay_snapshot_uses_its_optional_role_registry or screenplay_parent_rejects_writing_delegation_roles'
```

The pre-fix delegation assertion failed because a Writing `researcher`
delegation was accepted for a Screenplay parent. The initial snapshot invocation
also exposed a test-helper argument typo; after correcting the invocation, the
route behavior was covered by the same finding-level RED and the final GREEN
suite below.

Direct service RED command:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_delegation_service.py -q \
  -k 'rejects_profiles_without_agent_roles'
```

Observed result: exit 1; delegation without a role registry did not raise.

### GREEN

Run creation now adds `agentProfile` and `domainNamespace` to the existing
opaque `RunBinding.attributes`; child snapshots inherit that identity by walking
the persisted `parent_run_id` lineage. Snapshot views receive the selected
profile's optional registry. Delegation is rejected both at the generic route
and application service when a profile has no roles, so Writing roles cannot be
applied to a Screenplay parent.

This reused `binding_attributes_json` and the existing parent lineage. It
required no migration, schema change, or new persistence abstraction.

Focused GREEN commands/results:

```text
backend/tests/test_agent_run_queries.py                         21 passed
backend/tests/test_writing_chat_request_routes.py              16 passed
backend/tests/test_agent_delegation_service.py                   9 passed
backend/tests/test_agent_run_service_binding_cleanup.py          4 passed
```

Files:

- `backend/application/agent_composition.py`
- `backend/application/agent_profile_registry.py`
- `backend/application/agent_run_service.py`
- `backend/application/agent_delegation_service.py`
- `backend/application/screenplay_structured_call.py`
- `backend/application/screenplay_tool_calling.py`
- `backend/routers/ai.py`
- the focused route, service, structured-call, durable-service, and cleanup
  tests listed in the implementation commit

## Finding 4 — product composition has exactly two profiles

### RED

The combined RED command above showed that the product factory continued to
accept `profile_extension_factories` and therefore allowed extra profiles.

### GREEN

`create_agent_composition` no longer pops or splices an extension escape hatch;
it passes exactly the Writing and Screenplay factories. Tests that need custom
profiles continue to instantiate `AgentComposition` directly.

Focused GREEN evidence is included in the 44-passing composition suite. The
lifespan suite also passed 8/8 with the public profile contract.

Files:

- `backend/application/composition_factory.py`
- `backend/tests/test_agent_composition.py`

## Consolidated verification

Directly affected backend suites:

```bash
/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python -m pytest \
  backend/tests/test_agent_composition.py \
  backend/tests/test_agent_delegation_service.py \
  backend/tests/test_agent_run_queries.py \
  backend/tests/test_agent_run_service_binding_cleanup.py \
  backend/tests/test_writing_chat_request_routes.py \
  backend/tests/test_screenplay_agent_rewrite.py \
  backend/tests/test_screenplay_agent_durable_service.py \
  backend/tests/test_screenplay_tool_catalog.py \
  backend/tests/test_ai_composed_sse_wire_contract.py \
  backend/tests/test_main_lifespan.py -q
```

Result: exit 0, 215 passed, 0 failed.

Frontend suites: exit 0, 61 passed, 0 failed (command recorded under Finding
1).

Typecheck:

```bash
npm run typecheck
```

Result: exit 0. During verification an initial run found four TypeScript errors
from reading transport `streamId` after canonical type narrowing; capturing the
transport field before narrowing fixed the type boundary, and the fresh rerun
passed.

Architecture boundary gate:

```bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python \
  npm run check:agent-refactor-boundaries
```

Result: exit 0, 53 passed in 1.01s.

Format and scope checks:

```bash
git diff --check
git diff --name-only | rg \
  '(^|/)packages/purra/|(^|/)(migrations?|alembic)(/|$)|(^|/)[^/]*migration[^/]*$'
rg -n \
  "namespace\\s*(===|==|!==|!=)|switch\\s*\\([^)]*namespace|case\\s+['\"][^'\"]*(writing|screenplay)|purrtypos\\.(writing|screenplay)" \
  src/agent-runtime src/components/AgentConversation
```

Result: `git diff --check` exited 0; both forbidden-path/business-branch scans
returned no matches. Per the brief, the full `check:agent-refactor` was not run.

## Self-review

- Ownership changes occur before canonical reduction, so ignored foreign
  envelopes cannot change sequence state, response, lifecycle, plan, or agent
  identity.
- Resume authority is the existing transport stream/turn binding plus the Root
  lifecycle start signal; there is no second UI ownership state.
- Profile identity is written only through the existing opaque Run binding and
  validated against conflicting persisted profile/domain values.
- Snapshot/delegation logic is profile-neutral and treats registries as
  optional; no Writing/Screenplay router split was introduced.
- Product bootstrap has exactly two factories, while generic test composition
  remains extensible.
- No unrelated user changes were present at the reviewed head or included in
  the implementation commit.

## Concerns and release blockers

No code blocker remains for this fix wave. The npm mirror-key warnings and Node
typeless-module warnings are pre-existing and non-failing.

The four credential-skipped real-provider Screenplay E2E cases documented by
the prior verification remain RELEASE BLOCKERS, not passes: two DeepSeek
reasoning variants, Z.ai GLM-5.2, and MiMo v2.5 Pro. This scoped wave did not run
the full gate or claim live-provider coverage.

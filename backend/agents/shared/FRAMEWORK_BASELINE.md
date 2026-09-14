# Replacement Agent PurrA framework baseline

> S1 baseline, 2026-09-12. Deterministic package evidence only; this is not a
> PyPI release claim and does not prove real Provider or Electron behavior.

## Installed artifact identity

The replacement Agents target the exact locally archived PurrA candidate that
is already installed in the PurrTypos virtual environment:

| Distribution | Version | Archived wheel SHA-256 |
| --- | --- | --- |
| `purra` | `1.0.0` | `9a3695b9fa2f37bc11b140c0077c5c1273c5b352fc2e13579af99ff859a03c2c` |
| `purra_openai` | `1.0.0` | `4abed23852834e63f9d49d86f9134a2d78abe91a04fa5228582fe9471dd43188` |
| `purra_anthropic` | `1.0.0` | `3049d50e15370219df01e29f41e73a5b3fcd9086b11c691fabe860dcd03e4019` |
| `purra_mem0` | `1.0.0` | `c2621689e3f71490de1da1d9c7e729dfdcb0b254a40a24e36595de315cea4a0d` |

The authoritative machine-readable evidence remains
`backend/purra-candidate.json`, `backend/requirements-purra.txt`, and the wheels
under `backend/vendor/purra-1.0.0/`. A version string alone is not an artifact
identity. The manifest's historical `base_commit` is provenance metadata; the
complete `core_source_sha256` file map and wheel hash are the source/content
identity used by the host verification.

## Public framework boundary used by replacement code

Replacement code may use only installed public modules already ratcheted by
`backend/tests/test_purra_package.py`. The initial shared kernel depends on:

- `purra.api` for Agent Core options and model-task entry points;
- `purra.contracts` for requests, Run identity/provenance and runtime limits;
- `purra.long_tasks` for recipes, durable Unit execution and settlement;
- `purra.operations` for operation lifecycle and public display contracts;
- `purra.output` for canonical output and response transactions;
- `purra.ports` for host repositories, tools, context and cancellation ports;
- `purra.recovery` for failure signals and dispositions;
- `purra.run_control` and `purra.orphan_recovery` for durable execution control;
- `purra.artifacts` for generic Artifact lifecycle contracts;
- `purra.model_protocol` for Provider capability requirements.

Replacement product modules must not import `purra.engine` or other private
controller internals. PurrTypos supplies persistence adapters, product context,
recipes, admissible evidence, Artifact schemas and public projections.

## Framework behavior assumed by the rebuild

1. One top-level request creates one owning Agent Run.
2. A durable Unit runs as an Operation inside that owning Run; its identity is
   `(taskId, unitId, attempt)`, and no synthetic Unit Run is created.
3. Canonical output carries channel, visibility, kind and stable journal
   identity; application replay must not treat a Root projection as an event
   delivery allowlist.
4. `CancelledError` is an interruption/cancellation signal, not ordinary model
   output failure.
5. LongTask status, Root conversation status and product workflow status are
   distinct projections.
6. Tool side effects require durable identity/effect state; an interrupted call
   cannot be assumed not to have written.
7. PurrA owns generic lifecycle and settlement. Product code owns whether a
   source revision, candidate or other domain fact is admissible.

Any framework artifact change requires rerunning package-boundary tests and
reviewing these assumptions before replacement routing is enabled.

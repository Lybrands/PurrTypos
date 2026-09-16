# Replacement Agent PurrA framework baseline

> S1 baseline, 2026-09-12. Deterministic package evidence only; this is not a
> PyPI release claim and does not prove real Provider or Electron behavior.

## Installed artifact identity

The replacement Agents target the exact locally archived PurrA candidate that
is already installed in the PurrTypos virtual environment:

| Distribution | Version | Archived wheel SHA-256 |
| --- | --- | --- |
| `purra` | `1.0.1` | `4894f211cbc72430a5f83e3e6846e974d2751d33f0861c5d02bdb03ca890261e` |
| `purra_openai` | `1.0.1` | `3089978779f47a11c89ef8497232c07fb49c99e16125ab80f230ca3b9973c13c` |
| `purra_anthropic` | `1.0.1` | `d0cfd69b4de5a942b843c316d750418b77fd867357fb45b65aed72a9a441b235` |
| `purra_mem0` | `1.0.1` | `ad21ed6a06b4214e8f066916109cfa36c927e6c963dc564411e5451b1d1e8529` |

The authoritative machine-readable evidence remains
`backend/purra-candidate.json`, `backend/requirements-purra.txt`, and the wheels
under `backend/vendor/purra-1.0.1/`. A version string alone is not an artifact
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

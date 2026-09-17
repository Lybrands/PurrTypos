# Replacement Agent PurrA framework baseline

> S1 baseline, 2026-09-12. Deterministic package evidence only; this is not a
> PyPI release claim and does not prove real Provider or Electron behavior.

## Installed artifact identity

The replacement Agents target the exact locally archived PurrA candidate that
is already installed in the PurrTypos virtual environment:

| Distribution | Version | Archived wheel SHA-256 |
| --- | --- | --- |
| `purra` | `1.1.1` | `cdde4afe3db550c4e4d69c26fab6fd8e6089921691245d96cde4a01ddefc319d` |
| `purra_openai` | `1.1.1` | `088e6a0a6a982f573da0901352a6cd2e54fbe7f70f7d2de5142df89adec9f0a1` |
| `purra_anthropic` | `1.1.1` | `dda93df534661df5677558a7a3dfbd9a26efe9f5230d73f4f4eb88190f4e49ad` |
| `purra_mem0` | `1.1.1` | `f2c5987328e023b9a956a35456f77b54159a4959119e7e40dd0e2ce436297855` |

2026-09-18 更正：PurrA 1.0.x 版本线实际包含 1.1 范围特性，整线改号为
1.1.1（候选 wheel 位于 `backend/vendor/purra-1.1.1/`，基线 commit
`a2e69c6`）；上表随之更新。

The authoritative machine-readable evidence remains
`backend/purra-candidate.json`, `backend/requirements-purra.txt`, and the wheels
under `backend/vendor/purra-1.1.1/`. A version string alone is not an artifact
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

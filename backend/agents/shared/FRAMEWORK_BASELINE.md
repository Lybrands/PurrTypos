# Replacement Agent PurrA framework baseline

> S1 baseline, 2026-09-12. Deterministic package evidence only; this is not a
> PyPI release claim and does not prove real Provider or Electron behavior.

## Installed artifact identity

The replacement Agents target the exact locally archived PurrA candidate that
is already installed in the PurrTypos virtual environment:

| Distribution | Version | Archived wheel SHA-256 |
| --- | --- | --- |
| `purra` | `1.0.1` | `ee18951c5b4719cdd6b95cd572a7e81eaac062a8035e447a5d63eff61ddecc39` |
| `purra_openai` | `1.0.1` | `51dda6a559a7b2d072008461e5213852aee1ad674a57acebf70d1794329c13e7` |
| `purra_anthropic` | `1.0.1` | `b3427a68eb85533640f1ba4d50655c52a044ac52f2bd6910f370d37e2ce25960` |
| `purra_mem0` | `1.0.1` | `717fb15ab1804cabb9009bf0b323efa5c2dbb62fd7bea2c8d44c8b7430aeb42b` |

2026-09-18 更正：候选线最终定版 1.0.1（1.0.0 的补丁迭代；期间曾短暂
改号 1.1.1 后回退）。`backend/vendor/purra-1.0.1/` 内 wheel 已替换为
新基线 commit `9bbc54b` 的构建，上表随之更新。

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

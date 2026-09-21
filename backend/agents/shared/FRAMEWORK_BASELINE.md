# Replacement Agent PurrA framework baseline

> S1 baseline, 2026-09-12; 2026-09-21 switched to the published PyPI release.

## Installed artifact identity

The replacement Agents run against the published PurrA 1.0.1 release installed
from PyPI (`purra`, `purra-openai`, `purra-anthropic`, `purra_mem0`, pinned in
`backend/requirements-purra.txt`). The same-version rev2 upload carries the
recoverable mixed-batch fix; background lives in
`docs/design/2026-09-17-recoverable-mixed-tool-batches.md`.

Artifact identity is the PyPI release: version pins in
`backend/requirements-purra.txt` plus `importlib.metadata` evidence asserted by
`backend/tests/test_purra_package.py` (site-packages origin, no `direct_url.json`
residue). The previous locally archived candidate line (vendored wheels +
`purra-candidate.json` + `scripts/verify-purra-candidate.py`) was removed with
the switch; its history remains in `docs/design/`.

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

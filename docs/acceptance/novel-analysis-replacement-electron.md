# Novel Analysis replacement Electron acceptance

Novel Analysis passed this gate and became the production implementation on
2026-09-14. This launcher remains only for synthetic acceptance against
isolated data; it no longer selects the Agent implementation.

## Isolated launch

Run:

```bash
npm run acceptance:novel-analysis:electron
```

The launcher fails if ports 5174 or 18321 are already occupied. It creates a
fresh temporary root containing separate backend database and Electron
`userData` directories and writes both acceptance markers. Normal processes
and this isolated process both create `novel_analysis/purra-native/v1` Runs.
The temporary root is printed on startup and retained after exit for evidence
inspection.

Setting the rollout environment variable alone is insufficient. The backend
requires the marker in the selected database directory, while Electron rejects
its normal `userData` directory and requires a second marked directory. This
prevents synthetic acceptance from reusing a real user database or normal
renderer storage. The switch does not change implementation routing.

## Required evidence

Use synthetic source material and a saved model configured inside the isolated
database. Record each new Run ID and verify its persisted implementation is
`novel_analysis/purra-native/v1`.

The Electron pass must cover:

1. initial analysis through the complete seven-stage recipe;
2. progress and tool presentation without exposing private Unit content;
3. pause, resume, cancel, and restart recovery;
4. pending review Artifact read, review edit, and publication;
5. source citation lookup;
6. Artifact-bound follow-up and edited-turn replay;
7. application restart and history hydration from the retained database.

A deterministic or simulated Provider pass does not satisfy this gate. Real
Provider evidence must identify the provider/model, use a new synthetic Run,
and distinguish model failure from host/runtime failure. Stop Electron and its
backend after the pass, then verify that port 18321 has no listener.

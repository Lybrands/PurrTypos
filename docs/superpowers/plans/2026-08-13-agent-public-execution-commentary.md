# Agent Public Execution Commentary Design


## Goal

 Show safe, provider-authored execution narration during tool-based Agent runs and open the execution log by default.

## Architecture

 After Core validates that a tool call is structurally valid and currently authorized, it publishes any ordinary Provider content already emitted in that tool round as a new canonical commentary event pair; raw reasoning remains diagnostic. The frontend keeps its existing per-log manual state and changes only the initial visible-panel default.


## Constraints

- Never expose `model.reasoning_delta` as user-visible progress.
- Only provider-authored ordinary content from an authorized tool round may become commentary.
- Persist commentary before publishing it to SSE consumers.
- Do not duplicate Artifact bodies or tool arguments into host-authored prose.
- Preserve a user's manual expand/collapse choice after the initial default.

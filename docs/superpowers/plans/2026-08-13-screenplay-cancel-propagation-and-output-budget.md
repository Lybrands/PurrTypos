# Screenplay cancellation propagation and output budget

## Incident

The canceled screenplay operation `spaop_21b390bd9ce64fe5a6d7f3360b741f10`
settled its LongTask and units, but its four model Runs remained `running`.
The units did not persist their Run ids until execution returned, and canceling
the outer asyncio task released the Core without canceling its live Run.
Each source-analysis invocation also inherited the model-profile maximum of
393,216 output tokens, allowing an analysis unit to spend minutes producing
private reasoning without reaching its required tool call.

## Implementation

1. Expose a generic durable-unit Run-start binding callback from the long-task
   coordinator. Bind the Run before waiting for model completion.
2. On outer task cancellation or Run-start binding failure, explicitly request
   cancellation on the Core Run before releasing it.
3. Apply a screenplay-workflow output ceiling of 32,768 tokens while preserving
   any smaller explicit user limit and the model-profile maximum.
4. Cover early binding, cancellation propagation, and output ceiling behavior
   with regression tests, then run the Agent refactor gate.

## Invariants

- Raw provider reasoning remains private.
- Cancellation is durable and idempotent across process boundaries.
- A canceled unit cannot start a retry.
- Generic PurrA owns durable Run identity; screenplay code only supplies its
  workflow policy and binds the created Run to its product unit.

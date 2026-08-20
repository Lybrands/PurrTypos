# Agent Public Execution Commentary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show safe, provider-authored execution narration during tool-based Agent runs and open the execution log by default.

**Architecture:** After Core validates that a tool call is structurally valid and currently authorized, it publishes any ordinary Provider content already emitted in that tool round as a new canonical commentary event pair; raw reasoning remains diagnostic. The frontend keeps its existing per-log manual state and changes only the initial visible-panel default.

**Tech Stack:** Python 3, PurrA canonical output journal, SQLite, React, TypeScript, Node test runner, pytest.

## Global Constraints

- Never expose `model.reasoning_delta` as user-visible progress.
- Only provider-authored ordinary content from an authorized tool round may become commentary.
- Persist commentary before publishing it to SSE consumers.
- Do not duplicate Artifact bodies or tool arguments into host-authored prose.
- Preserve a user's manual expand/collapse choice after the initial default.

---

### Task 1: Default-open execution log

**Files:**
- Modify: `src/components/AgentConversation/AssistantOutput/timeline.ts`
- Test: `src/components/AgentConversation/AssistantOutput/timeline.test.ts`

**Interfaces:**
- Consumes: `getExecutionPanelPresentation(parts, input)`.
- Produces: `ExecutionPanelPresentation.autoOpen === true` whenever the panel is visible.

- [ ] Change the existing presentation test to expect visible active and completed logs to default open while an invisible log remains closed.
- [ ] Run the focused test and verify it fails on the current `autoOpen: false` implementation.
- [ ] Compute `visible` once and use it for both `visible` and `autoOpen`.
- [ ] Run the focused test and verify it passes.

### Task 2: Publish authorized tool-round narration

**Files:**
- Modify: `packages/purra/src/purra/output/ports.py`
- Modify: `packages/purra/src/purra/output/processor.py`
- Modify: `packages/purra/src/purra/model_invocation/manager.py`
- Modify: `packages/purra/src/purra/runtime/orchestrator.py`
- Modify: `backend/infrastructure/persistence/sqlite_agent_output_repository.py`
- Test: `backend/tests/test_sqlite_agent_output_repository.py`
- Test: `packages/purra/tests/test_output_processor.py`
- Test: `backend/tests/test_purra_runtime.py`

**Interfaces:**
- Consumes: a committed private Provider stream and a structurally valid, currently authorized tool batch.
- Produces: `AgentOutputRepository.publish_stream_content_as_commentary(output_stream_id) -> tuple[AgentOutputEvent, ...]` and `AgentModelInvocationManager.publish_model_stream_commentary(output_stream_id)`.

- [ ] Add failing repository tests proving promotion appends one coalesced public Provider commentary delta plus a public commentary commit event at new sequences, is replayable, and is idempotent.
- [ ] Add a failing Runtime test proving authorized tool-round content requests commentary publication, while raw reasoning and rejected tool calls do not.
- [ ] Add a failing output-processor test proving promoted repository events are published only after persistence.
- [ ] Run the focused tests and verify the missing interfaces fail.
- [ ] Implement the repository operation as one transaction over a committed private stream; preserve Provider text exactly and append new canonical events rather than mutating old cursor positions.
- [ ] Route promotion through the output processor and model invocation manager.
- [ ] Invoke promotion only after Runtime tool authorization succeeds and before tool execution begins.
- [ ] Run focused backend and PurrA tests, then frontend tests, typecheck, architecture checks, and `git diff --check`.

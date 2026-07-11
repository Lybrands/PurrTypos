# AI Task Plan And Expert Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a phased task planning experience first, then refactor writing sub-experts into reusable expert definitions that future agent modes and task steps can invoke.

**Architecture:** Phase 1 adds a conversation-embedded To-dos view and message-level task plan data. Phase 2 uses a model-backed planner to emit structured planner chunks after backend validation. Phase 3 extracts current writing subagent role metadata into a registry while preserving current behavior. Phase 4 treats the plan as an Agent Run state stream: plan, tool calls, model output, and final result all update the same assistant run instead of starting a second conversation turn.

**Tech Stack:** React, TypeScript, Ant Design icons/components where suitable, FastAPI/Python service modules, existing SSE chat chunk pipeline.

---

## Phase 1: Conversation Task Plan Card

**Intent:** Build方案 A: an inline task plan card inside assistant messages. This is a visual/product layer only; real task execution remains future work.

**Files:**
- Create: `src/Workspace/AiPanel/components/TaskPlanCard/index.tsx`
- Create: `src/Workspace/AiPanel/components/TaskPlanCard/index.scss`
- Modify: `src/Workspace/AiPanel/hooks/chat.types.ts`
- Modify: `src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx`

- [x] Add task plan TypeScript types to `ChatMessage`.
- [x] Add `TaskPlanCard` component with planned/running/done/blocked/failed step states.
- [x] Render `TaskPlanCard` above setting diff cards in assistant messages.
- [x] Use subdued card styling distinct from `ThinkingRegion` and `ToolCallStatus`.
- [x] Verify TypeScript build.

## Phase 2: Planner Chunk Protocol

**Intent:** Allow backend/AI stream to send structured task plan chunks that hydrate `message.taskPlan`; todo content must come from a model planner and pass backend validation, not from keyword templates.

**Files:**
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/types.ts`
- Create: `src/Workspace/AiPanel/hooks/chunkHandlers/taskPlan.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/index.ts`
- Modify: `src/types.ts`

- [x] Define frontend chunk shape for `taskPlan`.
- [x] Handle incoming `taskPlan` chunk by attaching it to the latest assistant message.
- [x] Preserve history compatibility: messages without task plans render normally.
- [x] Replace heuristic todo generation with model planner JSON.
- [x] Validate tool/expert/write-boundary constraints before emitting todos.
- [x] Verify TypeScript build.

## Phase 3: Expert Registry Extraction

**Intent:** Move current writing subagent definitions into a reusable registry without changing current behavior.

**Files:**
- Create: `backend/services/expert_registry.py`
- Modify: `backend/services/writing_subagents.py`
- Test: `backend/tests/test_writing_subagents.py`

- [x] Move role labels, allowed tool lists, prompt builder selection, and output normalizers into `expert_registry.py`.
- [x] Keep `run_writing_subagent(...)` public API stable.
- [x] Verify existing subagent tests still pass.

## Phase 3.5: Agent Run Plan State Flow

**Intent:** Make task plan status reflect the current assistant run, not a cross-turn task board or a button-driven second conversation.

**Files:**
- Create: `src/Workspace/AiPanel/hooks/taskPlanRun.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/taskPlan.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/toolStart.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/toolProgress.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/streaming.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/terminal.ts`
- Modify: `src/Workspace/AiPanel/components/TaskPlanCard/index.tsx`
- Modify: `src/Workspace/AiPanel/components/TaskPlanCard/index.scss`

- [x] Start plan status when `taskPlan` arrives in the current run.
- [x] Advance read/tool steps from tool start and tool completion events.
- [x] Advance model/review/write steps when assistant output begins.
- [x] Finish or fail the plan from terminal done/error events.
- [x] Remove task-card buttons that launched a second user turn.
- [x] Verify TypeScript build.

## Phase 4: Task Step Expert Executor Hook

**Intent:** Define the bridge between task plan steps and reusable experts, but keep execution sequential and explicit.

**Files:**
- Create: `backend/services/task_planner_types.py`
- Create: `backend/services/task_step_executor.py`
- Test: new focused tests for mapping expert steps to registry entries.

- [x] Define task step schema with `executor: model | tool | expert`.
- [x] Add expert step validation against the expert registry.
- [x] Do not introduce parallel execution in this phase.

## Phase 5: Conversation JSON Persistence

**Intent:** Persist task-card action state now that task plans can trigger a follow-up execution prompt, while avoiding a dedicated queue/table until steps need independent lifecycle management.

**Decision:** Use the existing `ai_conversations.task_plan` JSON column first. Promote to `ai_task_plans` / `ai_task_steps` later only if plans need independent ownership, retries, background execution, or cross-message lifecycle.

**Files:**
- Modify: `backend/routers/conversations.py`
- Modify: `backend/schemas/conversations.py`
- Modify: `electron/preload_python.js`
- Modify: `src/types.ts`
- Modify: `src/Workspace/AiPanel/hooks/chat.types.ts`
- Modify: `src/Workspace/AiPanel/hooks/chunkHandlers/terminal.ts`
- Modify: `src/Workspace/AiPanel/utils.ts`
- Modify: `src/Workspace/AiPanel/index.tsx`
- Test: `backend/tests/test_conversations_routes.py`

- [x] Return created `ai_conversations.id` from `saveConversation`.
- [x] Hydrate assistant messages with `conversationId`.
- [x] Persist final run-level task plan state through `saveConversation`.
- [x] Remove the task-card action update path after moving to Agent Run state flow.
- [x] Add `ai_agent_runs`, `ai_agent_run_todos`, and `ai_agent_run_events` schema for run-level persistence.
- [x] Verify TypeScript and focused backend tests.

## Verification

- [x] `npx tsc --noEmit`
- [ ] Frontend build if UI files change broadly.
- [x] Python tests for expert registry extraction when Phase 3 begins.
- [x] Python tests for task step executor validation.
- [x] Python tests for conversation task-plan persistence.

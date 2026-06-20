# Long-Term Memory Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first long-term memory system that unifies memory storage, recall, injection, tools, and management UI while preserving existing spark idea and foreshadowing compatibility.

**Architecture:** Add `memory_items` as the canonical recall surface, mirror existing `ai_memories` and `ai_foreshadowing` into it, and route AI context construction through a backend memory orchestrator. Keep SQLite FTS5 as the default retriever and expose diagnostics so the UI can show forced, recalled, and deferred memory counts.

**Tech Stack:** FastAPI, aiosqlite, SQLite FTS5 trigram, pytest, React, TypeScript, Ant Design.

---

## File Structure

- Modify: `backend/database/schema.py` to create `memory_items`, `memory_links`, `memory_items_fts`, triggers, migration mirror, and delete-book cleanup support.
- Create: `backend/services/long_term_memory_service.py` for CRUD, mirror helpers, FTS search, fingerprinting, status filtering, and relation management.
- Create: `backend/services/memory_orchestrator.py` for recall ranking, budgeted formatting, and diagnostics.
- Modify: `backend/services/memory_service.py` to double-write existing spark idea and foreshadowing operations into `memory_items`.
- Modify: `backend/routers/memories.py` and `backend/schemas/memories.py` to add unified memory endpoints while keeping old routes.
- Modify: `backend/services/tool_handlers/memory_tools.py` and add/update `backend/skills/*/SKILL.md` for unified tools and compatible legacy tools.
- Modify: `backend/utils/chat_preflight.py` to call `memory_orchestrator` for selected and automatic memory context.
- Modify: `backend/routers/ai.py` only if request plumbing needs to pass user prompt/mode into the orchestrator.
- Modify: `electron/preload_python.js` and `src/types.ts` to expose new memory APIs.
- Create: `src/Workspace/AiPanel/components/MemoryCenter/index.tsx` and `index.scss` for memory center UI.
- Modify: `src/Workspace/DirectorNotebook/NotebookToolbar.tsx` to replace the placeholder with memory center.
- Modify: `src/Workspace/AiPanel/components/MemoryModal/index.tsx` only as needed to narrow it to forced selection.
- Modify: `src/Workspace/EditorPanel/inlineEditContext.ts` and inline submit path to stop front-end memory prompt assembly.
- Test: `backend/tests/test_long_term_memory_service.py`, `backend/tests/test_memory_orchestrator.py`, `backend/tests/test_chat_preflight.py`, `backend/tests/test_data_integrity_routes.py`, `backend/tests/test_tool_executor_registry.py`.

## Task 1: Database And Service Foundation

**Files:**
- Modify: `backend/database/schema.py`
- Create: `backend/services/long_term_memory_service.py`
- Test: `backend/tests/test_long_term_memory_service.py`

- [ ] **Step 1: Write failing tests for memory CRUD, fingerprint dedupe, and FTS search**

```python
async def test_create_memory_item_dedupes_by_fingerprint(temp_db):
    first = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="主角不能使用火系法术",
        scope_type="book",
    )
    second = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="主角不能使用火系法术",
        scope_type="book",
    )

    assert second["id"] == first["id"]
    assert second["deduped"] is True


async def test_search_memory_items_excludes_pending_and_superseded_by_default(temp_db):
    active = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="active"
    )
    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="pending"
    )
    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="superseded"
    )

    rows = await long_term_memory_service.search_memory_items("b1", "玉佩")
    assert [r["id"] for r in rows] == [active["id"]]
```

- [ ] **Step 2: Run the service tests to verify RED**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py -q`

Expected: FAIL because `long_term_memory_service` and `memory_items` do not exist.

- [ ] **Step 3: Add schema tables, FTS, triggers, and migration mirror**

Implement:

- `memory_items` with fields from the spec.
- `memory_links`.
- `memory_items_fts` using `tokenize=trigram`.
- insert/update/delete triggers for `memory_items_fts`.
- idempotent mirror insertion from `ai_memories` and `ai_foreshadowing`.

- [ ] **Step 4: Add `long_term_memory_service` minimal implementation**

Implement:

- `normalize_memory_text(text: str) -> str`
- `build_fingerprint(book_id, kind, scope_type, scope_id, content) -> str`
- `create_memory_item(...)`
- `get_memory_items_by_ids(ids)`
- `search_memory_items(book_id, query, options=None)`
- `update_memory_item(id_, data)`
- `archive_memory_item(id_)`
- `link_memory_items(...)`
- `mirror_spark_idea(row)`
- `mirror_foreshadowing(row)`

- [ ] **Step 5: Run the service tests to verify GREEN**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py -q`

Expected: PASS for CRUD, dedupe, and default search filtering.

## Task 2: Legacy Double-Write And Data Cleanup

**Files:**
- Modify: `backend/services/memory_service.py`
- Modify: `backend/routers/books.py`
- Test: `backend/tests/test_data_integrity_routes.py`
- Test: `backend/tests/test_long_term_memory_service.py`

- [ ] **Step 1: Write failing tests for legacy mirror behavior**

```python
async def test_spark_idea_write_mirrors_to_memory_items(temp_db):
    spark = await memory_service.add_spark_idea("b1", "全局", "龙族惧怕盐")
    rows = await long_term_memory_service.search_memory_items("b1", "龙族")

    assert rows[0]["kind"] == "canon"
    assert rows[0]["source_type"] == "spark_idea"
    assert rows[0]["source_id"] == str(spark["id"])


async def test_foreshadowing_write_mirrors_to_memory_items(temp_db):
    fs = await memory_service.add_foreshadowing("b1", "ch1", "黑猫避开神龛", type_="悬念")
    rows = await long_term_memory_service.search_memory_items("b1", "黑猫")

    assert rows[0]["kind"] == "foreshadowing"
    assert rows[0]["source_type"] == "foreshadowing"
    assert rows[0]["source_id"] == str(fs["id"])
```

- [ ] **Step 2: Run tests to verify RED**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py backend/tests/test_data_integrity_routes.py -q`

Expected: FAIL because legacy operations do not mirror into `memory_items` and delete-book cleanup does not clear new tables.

- [ ] **Step 3: Add double-write calls**

In `memory_service.add_spark_idea`, `update_spark_idea`, `delete_spark_idea`, `add_foreshadowing`, `update_foreshadowing`, and `delete_foreshadowing`, call `long_term_memory_service` mirror/update/archive helpers. Keep old return shapes unchanged.

- [ ] **Step 4: Add delete-book cleanup for new tables**

Ensure book deletion removes `memory_items` and `memory_links` for that `book_id`.

- [ ] **Step 5: Run tests to verify GREEN**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py backend/tests/test_data_integrity_routes.py -q`

Expected: PASS.

## Task 3: Memory Orchestrator

**Files:**
- Create: `backend/services/memory_orchestrator.py`
- Modify: `backend/utils/chat_preflight.py`
- Test: `backend/tests/test_memory_orchestrator.py`
- Test: `backend/tests/test_chat_preflight.py`

- [ ] **Step 1: Write failing orchestrator tests**

```python
async def test_build_memory_context_groups_forced_and_recalled_items(temp_db):
    forced = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="canon", content="主角不能撒谎", pinned=1
    )
    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩已经在雨夜发光"
    )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1", "selectedMemoryIds": [forced["id"]]},
        "玉佩现在有什么异常？",
        "agent",
    )

    assert "必须遵循的设定" in block.text
    assert "已发生的剧情事实" in block.text
    assert forced["id"] in block.included_ids
    assert block.diagnostics["forced"] == 1


async def test_build_memory_context_defers_items_over_budget(temp_db):
    for idx in range(20):
        await long_term_memory_service.create_memory_item(
            book_id="b1", kind="plot", content=f"玉佩相关事实 {idx} " + "字" * 200
        )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1", "memoryBudget": 500},
        "玉佩",
        "ask",
    )

    assert len(block.deferred_ids) > 0
    assert "未注入" in block.text
```

- [ ] **Step 2: Run tests to verify RED**

Run: `py -m pytest backend/tests/test_memory_orchestrator.py -q`

Expected: FAIL because `memory_orchestrator` does not exist.

- [ ] **Step 3: Implement `MemoryContextBlock` and recall ranking**

Implement:

- dataclass `MemoryContextBlock`
- `build_memory_context(tool_ctx, user_prompt, mode)`
- forced ID fetch
- FTS recall
- status filtering
- kind grouping
- budget truncation
- diagnostics
- `last_used_at` update for included IDs

- [ ] **Step 4: Route selected memory block through orchestrator**

Update `build_selected_memory_block` to call the orchestrator for selected legacy IDs where possible while preserving failure behavior: return `""` on exception.

- [ ] **Step 5: Run tests to verify GREEN**

Run: `py -m pytest backend/tests/test_memory_orchestrator.py backend/tests/test_chat_preflight.py -q`

Expected: PASS.

## Task 4: Unified API And Electron Bridge

**Files:**
- Modify: `backend/schemas/memories.py`
- Modify: `backend/routers/memories.py`
- Modify: `electron/preload_python.js`
- Modify: `src/types.ts`
- Test: `backend/tests/test_long_term_memory_service.py`

- [ ] **Step 1: Write failing route/service tests for memory API**

```python
async def test_memory_api_can_create_search_and_archive(async_client):
    created = await async_client.post("/api/memories", json={
        "bookId": "b1",
        "kind": "plot",
        "content": "玉佩在雨夜发光",
    })
    assert created.json()["success"] is True

    search = await async_client.post("/api/memories/search", json={
        "bookId": "b1",
        "query": "玉佩",
    })
    assert search.json()["data"][0]["content"] == "玉佩在雨夜发光"
```

- [ ] **Step 2: Run tests to verify RED**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py -q`

Expected: FAIL because unified routes do not exist.

- [ ] **Step 3: Add request schemas and routes**

Add:

- `CreateMemoryRequest`
- `UpdateMemoryRequest`
- `SearchMemoriesRequest`
- `GetMemoryByIdsRequest`
- `LinkMemoriesRequest`
- `BuildMemoryContextRequest`

Routes:

- `POST /api/memories`
- `PUT /api/memories/{id}`
- `POST /api/memories/search`
- `POST /api/memories/by-ids`
- `POST /api/memories/{id}/archive`
- `POST /api/memories/link`
- `POST /api/memories/context`

- [ ] **Step 4: Expose new APIs through preload and TypeScript types**

Add `window.electronAPI` methods for the new routes and define `MemoryItem`, `MemoryContextDiagnostics`, and request/response types in `src/types.ts`.

- [ ] **Step 5: Run tests to verify GREEN**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py -q`

Expected: PASS.

## Task 5: Agent Tools

**Files:**
- Modify: `backend/services/tool_handlers/memory_tools.py`
- Create/update: `backend/skills/searchMemories/SKILL.md`
- Create/update: `backend/skills/createMemory/SKILL.md`
- Create/update: `backend/skills/updateMemory/SKILL.md`
- Create/update: `backend/skills/archiveMemory/SKILL.md`
- Create/update: `backend/skills/linkMemories/SKILL.md`
- Create/update: `backend/skills/resolveForeshadowing/SKILL.md`
- Test: `backend/tests/test_tool_executor_registry.py`

- [ ] **Step 1: Write failing tool registry tests**

```python
def test_memory_tools_are_registered():
    names = get_registered_tool_names()
    assert "searchMemories" in names
    assert "createMemory" in names
    assert "updateMemory" in names
    assert "archiveMemory" in names
    assert "linkMemories" in names
    assert "resolveForeshadowing" in names
```

- [ ] **Step 2: Run tests to verify RED**

Run: `py -m pytest backend/tests/test_tool_executor_registry.py -q`

Expected: FAIL because unified memory tools are not registered.

- [ ] **Step 3: Implement handlers**

Add tool handlers that call `long_term_memory_service` and keep legacy `searchSparkIdeas` / `addSparkIdea` behavior intact.

- [ ] **Step 4: Add SKILL.md definitions**

Each skill must include a valid JSON parameters block and clear constraints: `bookId` comes from host context when omitted, `pending` memories are not used for canonical facts, and `resolveForeshadowing` updates the foreshadowing status plus mirrored memory item.

- [ ] **Step 5: Run tests to verify GREEN**

Run: `py -m pytest backend/tests/test_tool_executor_registry.py -q`

Expected: PASS.

## Task 6: Frontend Memory Center And Selection UI

**Files:**
- Create: `src/Workspace/AiPanel/components/MemoryCenter/index.tsx`
- Create: `src/Workspace/AiPanel/components/MemoryCenter/index.scss`
- Modify: `src/Workspace/DirectorNotebook/NotebookToolbar.tsx`
- Modify: `src/Workspace/AiPanel/components/AiContextBar/index.tsx`
- Modify: `src/Workspace/AiPanel/components/MemoryModal/index.tsx`
- Modify: `src/types.ts`

- [ ] **Step 1: Add TypeScript types before UI code**

Define `MemoryItem`, `MemoryKind`, `MemoryStatus`, `SearchMemoriesPayload`, and `MemoryContextDiagnostics` in `src/types.ts`.

- [ ] **Step 2: Build `MemoryCenter` with Ant Design components**

Use `Modal` parent from `NotebookToolbar`, and inside `MemoryCenter` use Ant Design `Tabs`, `Input.Search`, `Select`, `List`, `Tag`, `Button`, `Space`, `Empty`, `Spin`, and `Modal.confirm` for destructive actions.

- [ ] **Step 3: Replace placeholder in `NotebookToolbar`**

Pass `bookId` to `MemoryCenter`; keep existing style modal untouched.

- [ ] **Step 4: Narrow `MemoryModal` language**

Update copy so the lamp selector means “本轮强制注入” instead of “全部记忆管理”. Keep existing selection localStorage key.

- [ ] **Step 5: Build TypeScript to catch UI type errors**

Run: `npx tsc --noEmit`

Expected: exit 0.

## Task 7: Inline Context Unification

**Files:**
- Modify: `src/Workspace/EditorPanel/inlineEditContext.ts`
- Modify: inline submit caller files if needed
- Modify: `electron/preload_python.js`
- Test: `backend/tests/test_chat_preflight.py`

- [ ] **Step 1: Add a backend context route call in preload**

Expose `buildMemoryContext` to return `MemoryContextBlock`.

- [ ] **Step 2: Stop front-end memory prompt assembly**

Remove direct `getSparkIdeasByIds` / `getForeshadowingByIds` calls from `inlineEditContext.ts`; pass selected IDs to `buildMemoryContext` and append `text`.

- [ ] **Step 3: Preserve associated chapter and outline behavior**

Keep existing associated chapter/outline text assembly until a later refactor moves it fully into backend; only memory/foreshadowing moves now.

- [ ] **Step 4: Run TypeScript build**

Run: `npx tsc --noEmit`

Expected: exit 0.

## Task 8: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `backend/config.py` only if stale comments mention active mem0 behavior
- Test: all backend memory-related tests

- [ ] **Step 1: Update README memory description**

Replace stale “mem0 集成（长期记忆）” wording with SQLite-backed long-term memory and optional future embedding.

- [ ] **Step 2: Run backend verification**

Run: `py -m pytest backend/tests/test_long_term_memory_service.py backend/tests/test_memory_orchestrator.py backend/tests/test_chat_preflight.py backend/tests/test_data_integrity_routes.py backend/tests/test_tool_executor_registry.py -q`

Expected: PASS.

- [ ] **Step 3: Run frontend verification**

Run: `npx tsc --noEmit`

Expected: PASS.

- [ ] **Step 4: Inspect git status**

Run: `git status --short`

Expected: only intended files are modified or added. Do not commit unless the user explicitly asks.

## Self-Review

Spec coverage:

- Unified memory pool: Task 1 and Task 4.
- Legacy compatibility: Task 2 and Task 5.
- Unified recall/injection: Task 3 and Task 7.
- UI memory center: Task 6.
- Automatic deposition candidate workflow: represented in schema/status and service fields in Task 1; full model-driven extraction is deferred until after stable recall, as specified.
- Tests and migration: Task 1, Task 2, Task 3, Task 4, Task 5, Task 8.

Placeholder scan: no placeholder steps; each task includes concrete files, commands, and expected results.

Type consistency:

- Backend canonical name is `memory_items`.
- Service name is `long_term_memory_service`.
- Orchestrator return type is `MemoryContextBlock`.
- Frontend type name is `MemoryItem`.

Execution mode for this session: inline execution, because the user requested continuing without further confirmation.

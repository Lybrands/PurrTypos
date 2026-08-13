# Agent Root Run 与公开计划投影实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口产品 Agent 装配边界，并保证小说、剧本共享对话中的公开任务计划只来自 Root Run 的 LLM `TaskPlan`，不会被 LongTask、Recipe 或业务固定步骤替换。

**Architecture:** 保留 `AgentComposition` 作为业务无关的应用装配器，通过类型化 Profile Extension 注入 Writing 和 Screenplay 的产品能力。前端 canonical reducer 以 `run.todos_updated` / `run.todo_updated` 为唯一公开计划协议；`long_task.*` 只维护耐久任务关联和执行状态，不生成第二份 `TaskPlan`。本计划不修改 `packages/purra`。

**Tech Stack:** Python 3.12、FastAPI、PurrA application ports、SQLite、TypeScript、Node test runner、React runtime projection。

## Global Constraints

- [ ] 不修改 `packages/purra/**`；若测试证明缺少通用契约，停止本计划并另提 Core 设计。
- [ ] 不把 Writing 或 Screenplay 条件分支加入共享前端。
- [ ] 不删除旧数据库字段；本阶段只停止把重复字段作为执行权威。
- [ ] 不覆盖或整理工作区已有改动；每次提交只暂存本任务列出的文件。
- [ ] 实时 SSE 与历史 replay 必须经过同一个 canonical reducer。
- [ ] `run.todos_updated` / `run.todo_updated` 是公开计划唯一来源；`long_task.progress.units` 只能作为内部执行状态。

---

## Task 1: 用失败测试固定公开计划的单一来源

**Files:**
- Modify: `src/agent-runtime/chunkReplay.test.cjs`
- Modify: `src/agent-runtime/chunkHandlers.test.ts`
- Inspect only: `src/agent-runtime/chunkHandlers/canonical.ts`

- [ ] 将现有测试 `canonical long-task progress restores the execution status plan without todo events` 改为断言：只收到 `long_task.progress` 时会恢复 `longTaskId`，但 `assistant.taskPlan` 仍为 `undefined`。
- [ ] 新增回放用例：先收到 LLM 的 `run.todos_updated`，再收到包含 Recipe units 的 `long_task.progress`；断言计划标题、步骤 ID、步骤标题和依赖保持 LLM 原值。
- [ ] 新增终态用例：`long_task.progress.status=completed` 不直接终结公开计划；只有对应 Root Run 的 `run.todo_updated` / `run.completed` 改变计划步骤和计划终态。
- [ ] 运行失败测试：

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts src/agent-runtime/chunkReplay.test.cjs
```

预期：当前 `normalizeLongTaskPlan()` 会创建或覆盖 `taskPlan`，新增断言失败。

- [ ] 提交测试基线：

```bash
git add src/agent-runtime/chunkHandlers.test.ts src/agent-runtime/chunkReplay.test.cjs
git commit -m "test(agent-ui): require root run public plans"
```

## Task 2: 移除 LongTask 到公开 TaskPlan 的投影

**Files:**
- Modify: `src/agent-runtime/chunkHandlers/canonical.ts`
- Modify: `src/agent-runtime/chunkReplay.test.cjs`

- [ ] 在 `applyCanonicalRuntimeView()` 的 `long_task.progress` 分支只更新 `ctx.acc.longTaskId`，删除 `normalizeLongTaskPlan()` 的调用以及对 `ctx.acc.taskPlan` 的写入。
- [ ] 删除只为公开计划服务的 `normalizeLongTaskPlan()`、`normalizeLongTaskStep()`、`normalizeLongTaskPlanStatus()`、`normalizeLongTaskStepStatus()`、`normalizeLongTaskStepType()` 和 `isLongTaskProjection()`；若其中某个函数仍被非计划状态使用，保留最小公共部分并重命名为其真实职责。
- [ ] 保留 `long_task.dispatched` 与 `long_task.progress` 的 `taskId` 恢复，确保取消、恢复和诊断仍能找到 LongTask。
- [ ] 运行定向前端测试并确认通过：

```bash
node --experimental-strip-types --test src/agent-runtime/chunkHandlers.test.ts src/agent-runtime/chunkReplay.test.cjs src/agent-runtime/runtimeSelectors.test.ts
```

- [ ] 提交实现：

```bash
git add src/agent-runtime/chunkHandlers/canonical.ts src/agent-runtime/chunkReplay.test.cjs
git commit -m "fix(agent-ui): keep durable progress out of public plans"
```

## Task 3: 建立类型化的产品 Profile Extension 契约

**Files:**
- Modify: `backend/application/agent_profile_registry.py`
- Modify: `backend/application/agent_composition.py`
- Modify: `backend/tests/test_agent_composition.py`
- Modify: `backend/tests/test_application_boundaries.py`

- [ ] 先在 `backend/tests/test_agent_composition.py` 增加一个最小 fake extension，覆盖以下契约：`profile_registration()`、`prepare_request()`、可选 `context_provider_factory()`、`response_judge_policies()`、可选 `task_admission`、可选 `create_long_task_dispatcher()`。
- [ ] 增加边界测试：`backend/application/agent_composition.py` 不再导入 `domains.writing`、`infrastructure.writing` 或 `infrastructure.persistence.writing`。
- [ ] 在 `agent_profile_registry.py` 定义 `AgentProfileExtension` Protocol；所有可选能力使用显式方法并给出无副作用默认实现，避免 `AgentComposition` 继续用业务 ID 或 `getattr` 猜能力。
- [ ] 将 `AgentComposition.__init__()` 改为只接收 `profile_extension_factories` 产生的 extension，并从 extension 的 `profile_registration()` 建立 registry；删除 `skills_dir`、`writing` 和默认 Writing registration。
- [ ] 将 `prepare_request()` 改为始终委托当前 Profile Extension；若 extension 不改变请求，返回原请求。
- [ ] 将 Writing 特有的 context-provider factory 与 response judge 分支替换为 extension 方法；`create_core()` 只消费 extension 返回的通用 ports。
- [ ] 保留 `agent_role_registry_for_request()`，删除 Writing 专用的 `writing`、`skill_catalog`、`agent_role_registry` 属性。调用方不得通过默认 Writing 属性绕过请求 Profile。
- [ ] 运行失败后转绿的测试：

```bash
.venv/bin/python -m pytest backend/tests/test_agent_composition.py backend/tests/test_application_boundaries.py -q
```

- [ ] 提交契约收口：

```bash
git add backend/application/agent_profile_registry.py backend/application/agent_composition.py backend/tests/test_agent_composition.py backend/tests/test_application_boundaries.py
git commit -m "refactor(agent): type product profile extensions"
```

## Task 4: 把 Writing 产品装配移到 composition factory

**Files:**
- Create: `backend/application/writing_agent_profile.py`
- Modify: `backend/application/composition_factory.py`
- Modify: `backend/application/agent_run_service.py`
- Modify: `backend/routers/ai.py`
- Modify: `backend/tests/test_agent_composition.py`
- Modify: `backend/tests/test_writing_chat_request_routes.py`

- [ ] 先写 `WritingAgentProfileExtension` 的测试，要求它拥有当前 Writing 装配的全部职责：Writing catalog hydration、`WritingSkillCatalog`、Writing tool catalog、`RepositoryWritingContextSource`、model-backed memory reranker、Writing response judge policies和 Writing role registry。
- [ ] 在 `writing_agent_profile.py` 实现 `build_writing_profile_extension(db, ...)`；把从 `AgentComposition` 移出的 Writing imports 和对象创建完整迁入，不复制同一依赖构造。
- [ ] 在 `composition_factory.py` 注册 Writing extension；用无业务钩子的 `StaticAgentProfileExtension` 包装当前 Screenplay adapter，直到剧本动态计划实施计划将其替换为完整 Screenplay extension。
- [ ] 修改 `agent_run_service.py`：始终调用 `agent_role_registry_for_request(request)`，删除回退到 `composition.agent_role_registry` 的兼容分支。
- [ ] 修改 `routers/ai.py` 的 Writing 查询/审批调用：根据已构造的 Writing request 或明确的 Writing namespace 获取 role registry，不再访问已删除的默认属性。
- [ ] 将需要产品 Profile 的测试统一改用 `create_agent_composition()`；纯装配单元测试显式传 fake extension，确保无隐式 Writing 默认值。
- [ ] 运行定向测试：

```bash
.venv/bin/python -m pytest backend/tests/test_agent_composition.py backend/tests/test_writing_chat_request_routes.py backend/tests/test_agent_run_queries.py -q
```

- [ ] 运行装配边界门禁：

```bash
npm run check:agent-refactor-boundaries
```

- [ ] 提交产品装配：

```bash
git add backend/application/writing_agent_profile.py backend/application/composition_factory.py backend/application/agent_run_service.py backend/routers/ai.py backend/tests/test_agent_composition.py backend/tests/test_writing_chat_request_routes.py
git commit -m "refactor(agent): inject writing profile from product composition"
```

## Task 5: 验证 Root Run 计划的实时与回放一致性

**Files:**
- Modify: `backend/tests/test_ai_composed_sse_wire_contract.py`
- Modify: `backend/tests/test_sse_private_protocol_projection.py`
- Modify: `src/agent-runtime/chunkReplay.test.cjs`

- [ ] 增加 wire contract：Root Run 的 `run.todos_updated` 包含公开语义步骤；LongTask units 可持久化和回放，但不能投影为第二个 `taskPlan`。
- [ ] 增加顺序扰动用例：todo、long-task progress、todo update、run terminal 以实时和 replay 两条路径输入，最终 UI 状态完全一致。
- [ ] 确认 Recipe 标题、校验步骤、发布步骤和 `plannerStepId` 不出现在公开计划中。
- [ ] 运行前后端定向测试：

```bash
.venv/bin/python -m pytest backend/tests/test_ai_composed_sse_wire_contract.py backend/tests/test_sse_private_protocol_projection.py -q
node --experimental-strip-types --test src/agent-runtime/chunkReplay.test.cjs src/agent-runtime/chunkHandlers.test.ts
npm run typecheck
```

- [ ] 提交协议回归测试：

```bash
git add backend/tests/test_ai_composed_sse_wire_contract.py backend/tests/test_sse_private_protocol_projection.py src/agent-runtime/chunkReplay.test.cjs
git commit -m "test(agent): lock public plan replay semantics"
```

## Task 6: 完成共同前置的回归门禁

**Files:**
- Verify only; no planned production changes.

- [ ] 运行完整 Agent 架构门禁：

```bash
npm run check:agent-refactor
```

- [ ] 运行格式检查：

```bash
git diff --check
```

- [ ] 使用 `git diff --name-only <base>...HEAD` 确认没有 `packages/purra/**` 和数据库 migration 变更。
- [ ] 验收：Writing 和 Screenplay Profile 均可从 `create_agent_composition()` 解析；共享前端不含业务 namespace 分支；无 todo 事件时不显示 Task Progress；LongTask 仍可取消、恢复和诊断。
- [ ] 若完整门禁因真实 Provider 凭据缺失而跳过测试，记录为后续两份业务计划的 release blocker，不能把模拟 Gateway 结果当作 E2E 通过。

## Handoff

- 本计划完成后再执行小说计划和剧本计划。
- 小说与剧本计划可以在共同前置合并后并行实施，但各自必须独立通过测试和真实 Provider E2E。
- 不要在本计划顺手重写 Screenplay Service；Root Run 所有权迁移属于剧本实施计划。

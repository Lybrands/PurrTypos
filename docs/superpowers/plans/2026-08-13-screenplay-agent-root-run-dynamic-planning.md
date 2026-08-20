# 剧本 Agent Root Run 与动态语义规划实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一个 Root Run 承载剧本问答或正式创作任务，让 LLM 生成公开语义计划和业务 TaskSpec，由 Screenplay Resolver 与 Manifest Compiler 编译私有耐久 Recipe，并移除独立 Planner Run 和剧本业务自管 Child Run 生命周期。

**Architecture:** Root PurrA Run 先生成 `TaskPlan + TaskSpec`。Screenplay Profile Extension 将普通问答准入为 INLINE，将正式任务解析为 `ScreenplayIntent`、Screenplay Operation 和私有 Recipe，再通过 PurrA durable dispatcher 执行。确定性 Part 不创建 Run；AI Part 通过通用 AgentRunService 以无 delegation 的 RunLineage 创建 Child Run；只有 LLM 明确选择 Screenplay agent role 时才创建 Delegation Child Run。公开计划只由 Root Run 持有。

**Tech Stack:** Python 3.12、PurrA task admission/durable execution、FastAPI、SQLite、Screenplay Operation/Manifest/Artifact、pytest、SSE replay。

## Global Constraints

- [ ] 先完成共同 Root Run/公开计划投影计划。
- [ ] 不删除 Screenplay Operation、Manifest、Recipe、Part、Artifact、Revision 或原子发布。
- [ ] 不把 Recipe/Part/校验/发布标题作为公开任务计划。
- [ ] 不让 LLM 决定重试次数、并行度、幂等键、Revision 基线或发布顺序。
- [ ] 不为确定性 Part 创建 Run；不把每个 Child Run 都标记成子 Agent。
- [ ] 不在 Screenplay Service 手写 `run.todos_updated`；Root 计划修改必须经过 PurrA controller。
- [ ] 旧 `planner_run_id` 数据库列第一阶段保留，仅作为兼容存储 Root Run ID，不再代表独立 Planner。
- [ ] 所有生产改动先由失败测试证明；当前工作区已有变更不纳入提交。

---

## Task 1: 补齐 durable 检查点计划修订的通用 Core 契约

**Why this is the one Core exception:** 当前 `complete_admitted_task()` 只调用 `sync_durable_execution()`，没有让 durable dispatcher 请求 `AgentRunController.revise_plan()` 的 port。若不补此契约，业务层只能伪造 todo 事件，既不会更新 Run snapshot，也会破坏 replay 与恢复。该能力对所有 durable Profile 通用，不包含剧本语义。

**Files:**
- Modify: `packages/purra/src/purra/task_admission/contracts.py`
- Modify: `packages/purra/src/purra/engine/durable_execution.py`
- Create: `packages/purra/tests/test_durable_execution.py`
- Modify: `backend/tests/test_purra_durable_dispatcher.py`
- Modify: `backend/tests/test_purra_boundaries.py`

- [ ] 先增加失败测试：dispatcher observer 发出带 `plan_revision` 的 update 后，Root Run 通过 `run.todos_updated` 原子替换未完成步骤，而不是追加一个旁路事件。
- [ ] 在 `LongTaskExecutionUpdate` 增加可选的 `plan_revision: TaskPlan | None`；默认值为 `None`，现有 dispatcher 不受影响。
- [ ] 在 durable loop 接收 revision 时，先验证：
  - revision 的步骤 ID 集合与 `admission.covered_step_ids` 完全相同；
  - 已完成步骤的 ID、标题、类型、executor、依赖和 done 状态不变；
  - revision 仍满足 `TaskPlan` 结构约束；
  - update event 本身只承载 checkpoint 证据，不能携带伪造的 `RUN_TODOS_UPDATED`。
- [ ] 验证通过后调用 `controller.revise_plan(plan_revision)`，再持久化 checkpoint event；任何验证失败都使 Root Run 以 contract violation 失败，不继续执行旧 Recipe。
- [ ] 不允许在 durable revision 中增加或删除步骤 ID。本期“有界动态规划”允许 LLM 更新未完成步骤的标题、描述和依赖顺序；这样现有 Recipe coverage 与 `plannerStepId` 映射保持稳定。
- [ ] 增加边界测试：Core 新契约不得导入 `screenplay`、`writing` 或产品 application。
- [ ] 运行 Core 定向测试：

```bash
.venv/bin/python -m pytest packages/purra/tests/test_durable_execution.py backend/tests/test_purra_durable_dispatcher.py backend/tests/test_purra_boundaries.py -q
```

- [ ] 提交独立 Core 变更：

```bash
git add packages/purra/src/purra/task_admission/contracts.py packages/purra/src/purra/engine/durable_execution.py packages/purra/tests/test_durable_execution.py backend/tests/test_purra_durable_dispatcher.py backend/tests/test_purra_boundaries.py
git commit -m "feat(purra): accept validated durable plan revisions"
```

## Task 2: 用 TaskSpec 表达剧本语义与计划绑定

**Files:**
- Modify: `backend/domains/screenplay_agent/contracts.py`
- Modify: `backend/domains/screenplay_agent/agent_context.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_purra_contracts.py`

- [ ] 在测试中定义 `TaskSpec.target.screenplay` 的版本化结构：

```json
{
  "version": 1,
  "scope": {
    "kind": "current_stage|next_episodes|episodes|all_remaining",
    "count": 2,
    "episodeNumbers": []
  },
  "stepBindings": [
    {"stepId": "understand-source", "phase": "evidence"},
    {"stepId": "draft-analysis", "phase": "creation"},
    {"stepId": "deliver-candidate", "phase": "delivery"}
  ]
}
```

`TaskSpec.operation` 承载 `answer|create|revise|review`，`deliverable` 承载目标角色，`instruction/constraints/preserve` 使用通用字段。

- [ ] 新增 `ScreenplayPlanPhase`，只允许 `evidence|creation|review|delivery`；新增 `ScreenplayPlanBinding`，严格校验每个 Root plan step 恰好出现一次。
- [ ] 实现 `ScreenplayIntent.from_task_spec(task_spec, plan_steps)`：
  - 普通 answer 不要求 reply 字段，最终文本由 Root Run 生成；
  - 正式动作必须有合法 deliverable、scope 与完整 stepBindings；
  - binding step ID 集合必须与 TaskPlan 步骤 ID 集合完全相同；
  - stage command 的 action、deliverable、scope 仍使用 `require_compatible()` 校验。
- [ ] 将 `ScreenplayAgentDomainContext` 增加 root-only 的 `turn_id` 和 `stage_command`，同时保留 child Part 的 `task_id/unit_id/expected_part_key`；root 与 child 的判断不得依赖 prompt 文本。
- [ ] 删除 `ScreenplayIntent.reply` 作为业务协议；旧持久化 payload 读取时可忽略 reply，不能再用它完成新 Turn。
- [ ] 运行契约测试：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_purra_contracts.py -q
```

- [ ] 提交：

```bash
git add backend/domains/screenplay_agent/contracts.py backend/domains/screenplay_agent/agent_context.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_purra_contracts.py
git commit -m "feat(screenplay-agent): model semantic task specs"
```

## Task 3: 启用 Root Run 的 LLM 规划并注入项目事实

**Files:**
- Modify: `backend/domains/screenplay_agent/adapter.py`
- Create: `backend/domains/screenplay_agent/prompts.py`
- Modify: `backend/application/screenplay_agent_context.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_application_planning_constraints.py`

- [ ] 将 `ScreenplayToolLoopPolicy.should_plan()` 改为：projectId 和用户文本非空、且 context 不含 `task_id/unit_id` 时返回 true；AI Part Child Run 返回 false，避免内部 Part 再生成公开业务计划。
- [ ] 在 `prompts.py` 实现 `build_screenplay_planning_policy()`，要求 LLM：
  - 生成用户可见的语义步骤，不复述 Recipe 机械动作；
  - 同时填写版本化 Screenplay TaskSpec 和完整 stepBindings；
  - 普通问答使用 answer，不虚构 Operation；
  - 正式按钮 stage command 是不可变约束；
  - 不决定 Revision、重试、并行、发布或内部 Part。
- [ ] 让 `ScreenplayHostContextProvider.build_planning_context()` 通过 `ScreenplayAgentContextQuery.planning_context()` 提供项目阶段、可用交付物、Head/Candidate 摘要、原作范围和 stage command；不包含 Artifact body 或完整原作正文。
- [ ] 为 root planning context 增加独特正文泄漏测试；数据库中的正文 marker 不得出现在 planner payload。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_application_planning_constraints.py -q
```

- [ ] 提交：

```bash
git add backend/domains/screenplay_agent/adapter.py backend/domains/screenplay_agent/prompts.py backend/application/screenplay_agent_context.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_application_planning_constraints.py
git commit -m "feat(screenplay-agent): enable root semantic planning"
```

## Task 4: 将 Resolver、Operation 和 Recipe 接入 task admission

**Files:**
- Create: `backend/application/screenplay_agent_profile.py`
- Create: `backend/application/screenplay_task_resolver.py`
- Modify: `backend/application/screenplay_agent_planner.py`
- Modify: `backend/application/screenplay_manifest_compiler.py`
- Modify: `backend/application/composition_factory.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`

- [ ] 在测试中构造真实 `TaskPlan + TaskSpec`，断言 answer 返回 `ExecutionMode.INLINE` 且不创建 Operation；create/revise/review 返回 `ExecutionMode.DURABLE` 且只创建一个 Operation。
- [ ] 创建 `ScreenplayAgentProfileExtension`，实现：
  - `profile_registration()`：Screenplay adapter/tool catalog；
  - `prepare_request()`：补齐可信项目事实和 stage command；
  - `task_admission.evaluate()`：解析 TaskSpec、校验 stage command、调用 Resolver、创建 Operation、编译 Manifest/Recipe；
  - `create_long_task_dispatcher()`：返回使用 Screenplay executor registry 的 `RecipeLongTaskDispatcher`。
- [ ] 从 `screenplay_agent_planner.py` 删除 `ModelScreenplayIntentPlanner`、`_PLANNER_INSTRUCTION`、`_PLANNER_REPAIR`、`_ANSWER_INSTRUCTION` 和第二次 public answer 调用；将 `SqliteScreenplayTaskResolver` 移到 `screenplay_task_resolver.py`，旧文件暂时只做兼容 re-export，Task 9 在引用归零后删除。
- [ ] 修改 `compile_screenplay_manifest()` 接收 `ScreenplayPlanBinding`；每个内部 Part 按其业务 phase 映射到一个公开 `plan_step_id`。多个 Part 可以映射同一步，但每个公开 step 至少有一个 Recipe unit。
- [ ] 删除 `_recipe_step()` 中固定 `create/publish` 映射，删除 `_durable_plan()` 及固定“创作并校验内容/生成候选稿”。
- [ ] `TaskAdmissionDecision.covered_step_ids` 使用 Root TaskPlan 的完整步骤 ID；Recipe 继续私有，只在 metadata 保存 plan binding digest，不保存公开标题副本。
- [ ] 在 `composition_factory.py` 用 Screenplay extension factory 替换 plain registration。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_agent_durable_service.py -q
```

- [ ] 提交：

```bash
git add backend/application/screenplay_agent_profile.py backend/application/screenplay_task_resolver.py backend/application/screenplay_agent_planner.py backend/application/screenplay_manifest_compiler.py backend/application/composition_factory.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_agent_durable_service.py
git commit -m "refactor(screenplay-agent): admit root plans into durable operations"
```

## Task 5: 让 Conversation Turn 提交一个 Root Run

**Files:**
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/routers/screenplay_conversations.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py`
- Modify: `backend/tests/test_screenplay_agent_routes.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`

- [ ] 重写 `ScreenplayAgentService.execute_turn()` 的主路径：从 Turn 构造 Screenplay `AgentRunInput/AgentRunRequest`，通过共享 `AgentRunService.run()` 启动 Root Run，并把 runtime output 交给现有 canonical output processor。
- [ ] 删除 Service 内部的 planner → `_durable_plan` → dispatcher → `_execute_attached_operation` 主编排；这些职责已由 Profile task admission 和 PurrA durable execution拥有。
- [ ] 将现有数据库列 `planner_run_id` 暂时映射为应用字段 `rootRunId`，新增 repository 方法 `attach_root_run(turn_id, root_run_id)`；新 API snapshot 输出 `rootRunId`，只为旧客户端保留只读 `plannerRunId` alias，并标注弃用。
- [ ] Root Run 的 `conversation_turn_id` 绑定当前 Turn；普通问答完成后由 Root final response 投影回 `assistantContent`，正式任务在 Operation terminal 后由同一个 Root final response 完成。
- [ ] 路由 `_service()` 不再构造 `ModelScreenplayIntentPlanner` 或私有 dispatcher；只注入 composition、project service、turn repository 和 output processor。
- [ ] 增加路由测试：
  - answer：一个 Root Run、零 Operation、零 Child Run；
  - formal create：一个 Root Run、一个 Operation；
  - snapshot/replay 的 runId 等于 rootRunId；
  - 不再出现独立 `screenplay_intent_planning` Run。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_routes.py backend/tests/test_screenplay_agent_durable_service.py -q
```

- [ ] 提交：

```bash
git add backend/application/screenplay_agent_service.py backend/routers/screenplay_conversations.py backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py backend/tests/test_screenplay_agent_routes.py backend/tests/test_screenplay_agent_durable_service.py
git commit -m "refactor(screenplay-agent): run each turn under one root run"
```

## Task 6: 把 AI Part 交给 PurrA Child Run，确定性 Part 留在宿主

**Files:**
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/screenplay_structured_call.py`
- Modify: `backend/application/screenplay_tool_calling.py`
- Modify: `backend/application/agent_run_service.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_purra_durable_dispatcher.py`

- [ ] 为 Part 分类写参数化测试：
  - evidence binding、schema validation、artifact assembly、final publish：无 Child Run；
  - draft scene、document section、review dimension、必要的 final response composition：有无 delegation 的 Child Run；
  - 只有 LLM 计划明确的 `executor=agent + agent_role` 才产生 Delegation。
- [ ] 为通用 `AgentRunService` 增加 host-orchestrated child 提交入口，要求调用者传 `RunLineage(parent_run_id, root_run_id, delegation_id=None, agent_role="screenplay-part", depth=1)`；Run repository 与 cancellation lease 仍由 PurrA 管理。
- [ ] 修改 `ScreenplayTaskUnitExecutor`：AI Part 使用上述入口并从 Child Run validated output 读取结构化结果；不得直接调用 run repository、手工 begin/complete Run 或自行同步父子终态。
- [ ] 收缩 `ScreenplayStructuredCallService` 和 `ScreenplayToolCallingService`：保留 provider output parsing/业务 schema validation 的纯能力，移除其创建独立 Run、续租、终态和取消职责。
- [ ] Child Run 的 domain context 包含 taskId/unitId/expectedPartKey，因此 `ScreenplayToolLoopPolicy.should_plan()` 为 false；它只完成当前 Part，不生成第二份公开剧本计划。
- [ ] 默认 `enable_delegation=False`。仅当 Root plan 明确包含合法 Screenplay agent role，才通过现有 `ApplicationDelegationAdapter` 创建带 delegationId 的 Child Run；确定性 Part 和普通 AI Part 的 delegationId 均为 null。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_purra_durable_dispatcher.py backend/tests/test_agent_run_queries.py -q
```

- [ ] 提交：

```bash
git add backend/application/screenplay_agent_task_executor.py backend/application/screenplay_structured_call.py backend/application/screenplay_tool_calling.py backend/application/agent_run_service.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_purra_durable_dispatcher.py
git commit -m "refactor(screenplay-agent): delegate ai parts to child runs"
```

## Task 7: 在检查点执行有界 LLM 计划修订

**Files:**
- Create: `backend/application/screenplay_checkpoint_planning.py`
- Modify: `backend/application/screenplay_agent_profile.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`

- [ ] 只在业务检查点触发 revision：一个 episode 完成、一个 document section batch 完成或 review aggregate 完成；每个 Part 完成不触发。
- [ ] `ScreenplayCheckpointPlanner` 使用模型读取：原 Root TaskPlan、已完成步骤摘要、Artifact receipts、失败/约束变化和剩余范围。输出完整 revised TaskPlan，但步骤 ID 集合必须不变。
- [ ] Prompt 允许修改未完成步骤的 title、description 和 dependency order；禁止改变 completed step、阶段、deliverable、episode scope、base Revision 或已产生 Artifact。
- [ ] 若 LLM 建议改变阶段、deliverable 或 scope，checkpoint planner 不直接应用；返回 `requires_reresolution`，暂停 Operation，等待新的用户 Turn 重新经过 Resolver。
- [ ] 将合法 revision 放进 `LongTaskExecutionUpdate.plan_revision`，由 Task 1 的 Core 契约调用 Root controller；业务层不得自行发布 todo event。
- [ ] 增加测试：
  - 合法未完成步骤修订实时/replay 一致；
  - completed step 变化被 Core 拒绝；
  - scope 变化暂停且不重编译已完成 Part；
  - 已完成 Artifact/Revision 不被覆盖；
  - checkpoint plan output 失败时保留原计划并按 typed failure policy 暂停，不伪装成功。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_rewrite.py packages/purra/tests/test_durable_execution.py -q
```

- [ ] 提交：

```bash
git add backend/application/screenplay_checkpoint_planning.py backend/application/screenplay_agent_profile.py backend/application/screenplay_agent_task_executor.py backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_rewrite.py
git commit -m "feat(screenplay-agent): revise root plans at checkpoints"
```

## Task 8: 统一取消、暂停、恢复和 usage 所有权

**Files:**
- Create: `backend/application/agent_cancellation_service.py`
- Modify: `backend/routers/ai.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/routers/screenplay_conversations.py`
- Modify: `backend/tests/test_screenplay_agent_durable_service.py`
- Modify: `backend/tests/test_screenplay_agent_runtime_cleanup.py`

- [ ] 将 `routers/ai.py::cancel_agent_run` 的持久化取消逻辑提取到 `AgentCancellationService`，供通用 cancel endpoint 和 screenplay turn cancel 共用。
- [ ] screenplay cancel 先按 turn 找 rootRunId，再请求 Root Run cancellation；由同一 signal 向下传播到 Operation、LongTask、active host-child Runs 和 Delegations。Screenplay service 只负责把 Operation cancel receipt 与业务终态原子结算。
- [ ] SSE 断开只结束订阅，不调用 cancellation service；增加断线后 Operation/Root 继续运行并可 replay 的测试。
- [ ] 取消完成后查询 Run、LongTask unit、Operation 和 delegation tables，断言没有遗留 running/claimed。
- [ ] 暂停或模型不兼容时 Root Run 以 paused/canceled terminal 结束；resume command 作为新的控制命令启动 continuation Root Run，继续同一 Operation 的未完成 Recipe，不重做完成 Part。
- [ ] usage 以 Root Run + 实际 Child Run 去重汇总到 Operation；删除 `planner_run_id + task_run_ids` 的旧特殊算法。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_runtime_cleanup.py backend/tests/test_agent_run_queries.py -q
```

- [ ] 提交：

```bash
git add backend/application/agent_cancellation_service.py backend/routers/ai.py backend/application/screenplay_agent_service.py backend/routers/screenplay_conversations.py backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_runtime_cleanup.py
git commit -m "fix(screenplay-agent): cascade root run lifecycle"
```

## Task 9: 清理旧 Planner Run 代码并完成协议回放验收

**Files:**
- Delete after references reach zero: `backend/application/screenplay_agent_planner.py`
- Modify: `backend/database/crud/screenplay_agent_runtime_cleanup.py`
- Modify: `backend/tests/test_screenplay_agent_rewrite.py`
- Modify: `backend/tests/test_ai_composed_sse_wire_contract.py`
- Modify: `src/ScreenplayAgentPage/screenplayConversationController.test.ts`
- Modify: `src/agent-runtime/chunkReplay.test.cjs`

- [ ] 使用 `rg "ModelScreenplayIntentPlanner|_durable_plan|screenplay_intent_planning|plannerRunId"` 清除生产调用；仅允许 migration/legacy deserialization 注释中出现旧字段名。
- [ ] 更新 runtime cleanup：旧数据仍能按 `planner_run_id` 列清理关联 Root Run，但新代码语义命名为 root run。
- [ ] 增加端到端 replay fixture：Root todo → Operation/LongTask progress → AI Child Run tool/output → checkpoint todo revision → Candidate → Root final。断言公开计划不包含 Recipe units，执行过程保留工具、上下文压缩和显式委派。
- [ ] 前端 controller 只读取 canonical Root plan/rootRunId；不新增 Screenplay-specific plan reducer。
- [ ] 运行：

```bash
.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_ai_composed_sse_wire_contract.py backend/tests/test_screenplay_agent_runtime_cleanup.py -q
node --experimental-strip-types --test src/ScreenplayAgentPage/screenplayConversationController.test.ts src/agent-runtime/chunkReplay.test.cjs
npm run typecheck
```

- [ ] 提交：

```bash
git add backend/application/screenplay_agent_planner.py backend/database/crud/screenplay_agent_runtime_cleanup.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_ai_composed_sse_wire_contract.py src/ScreenplayAgentPage/screenplayConversationController.test.ts src/agent-runtime/chunkReplay.test.cjs
git commit -m "refactor(screenplay-agent): retire independent planner runs"
```

## Task 10: 完整离线门禁与手工 Provider 验收

**Files:**
- Verify all related suites.

- [ ] 项目所有者手工覆盖 answer、正式 create、一次 checkpoint revision、手动 cancel、resume 和 Candidate 生成。
- [ ] 手工验收必须确认：
  - 每个用户 Turn 一个 Root Run；
  - answer 不创建 Operation；
  - formal task 一个 Operation；
  - deterministic Part 无 Child Run；
  - AI Part Child Run 的 delegationId 为空；
  - 只有显式 agent role 才有 Delegation；
  - Recipe/validation/publish 不进入公开 TaskPlan；
  - cancel 后无 zombie；
  - publish 原子且重复请求不生成重复 Revision。
- [ ] 运行架构与完整测试：

```bash
npm run check:agent-refactor
npm run test:screenplay-acceptance
git diff --check
```

- [ ] 真实 Provider 验收在仓库测试之外执行，不得用 simulated provider 替代。

## Acceptance Checklist

- [ ] 普通问答只有 Root Run，不创建 Operation。
- [ ] 正式任务只有一个 Root Run 和一个 Screenplay Operation。
- [ ] 公开计划来自 LLM TaskPlan，不来自 `_durable_plan`、Manifest 或 Recipe。
- [ ] 检查点 revision 经过 PurrA controller，实时与 replay 一致。
- [ ] 已完成 Part、Artifact 和 Revision 不被重规划覆盖。
- [ ] 确定性 Part 不创建 Run；普通 AI Part 是无 delegation 的 Child Run。
- [ ] 仅显式 agent role 产生 Delegation Child Run。
- [ ] 手动取消向下关闭所有 active execution，无 zombie。
- [ ] SSE 断线不取消；重启/恢复不重做完成 Part。
- [ ] PurrA Core 的唯一改动是业务无关的 durable plan revision port，并独立通过边界测试。

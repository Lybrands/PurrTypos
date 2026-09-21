# PurrA 宿主接入交接记录

> 更新时间：2026-08-27  
> 适用仓库：`/Users/liuyubin/Lybrand_project/PurrTypos`  
> 文档性质：当前阶段的恢复上下文与执行边界，不替代代码、数据库和测试结果。

## 1. 当前结论

PurrTypos 的主 Agent 运行链路已经统一到 PurrA：

```text
HTTP / SSE
  -> application request mapping
  -> AgentRunService
  -> AgentComposition
  -> PurrA AgentCore
  -> Run / Output / Long Task persistence
```

Writing 和 Screenplay 都通过产品 Profile 注入上下文、规划策略、工具目录和任务准入规则。生产代码没有发现继续使用旧 `AgentRuntime` 或 child run 的路径。

本阶段没有修改代码，只完成了宿主边界审计。

## 2. 已确认的所有权边界

### PurrA 拥有

- Run 生命周期、状态和终态；
- 通用模型轮次、工具轮次和事件协议；
- 上下文预算与上下文阶段；
- 通用规划、计划校验和 durable execution；
- 工具 Schema、授权、取消、恢复、lease、幂等和 usage；
- Artifact、Output 和通用任务执行契约。

PurrA 不应导入 Project、Stage、Episode、Scene、Revision、Writing 方法或来源书籍等产品语义。

### Writing 拥有

- `WritingAgentProfile`、`WritingDomainContext`；
- Writing Context Provider、Planning Policy 和工具风险策略；
- 记忆、章节、大纲、人物和写作方法的业务查询；
- Writing 专属响应约束和业务评测。

### Screenplay 拥有

- 意图到业务任务的映射；
- `ExecutionPlan` 到产品 `ExecutionRecipe` 的编译；
- Operation、Task、Artifact Part、Candidate 和 Revision 的业务语义；
- 来源读取、证据回执、分集生成、审阅和检查点规则；
- `ScreenplayTaskUnitExecutor` 以及候选稿业务校验。

Screenplay 的结构化模型调用、候选稿生成和任务单元执行都属于同一 Root Run 内的私有 bounded model task，不是第二套 Agent 生命周期。

## 3. 关键入口

- [composition_factory.py](../../backend/application/composition_factory.py)：产品 Composition Root，注册 Writing 与 Screenplay Profile。
- [agent_composition.py](../../backend/application/agent_composition.py)：通用 Composition，创建 PurrA Core、模型任务 Runner、持久化适配器和 Profile。
- [agent_run_service.py](../../backend/application/agent_run_service.py)：统一 Run 提交、订阅、重放、等待终态和私有模型任务入口。
- [writing_agent_profile.py](../../backend/application/writing_agent_profile.py)：Writing 领域装配。
- [screenplay_agent_profile.py](../../backend/application/screenplay_agent_profile.py)：Screenplay 准入、Recipe 和 durable dispatcher 装配。
- [screenplay_agent_service.py](../../backend/application/screenplay_agent_service.py)：Screenplay Root Run、Operation 和恢复入口。

## 4. 审计发现（2026-08-27）

### P1：删除 Screenplay 旧 evidence 读取分支

生产生成路径已经写入：

```text
evidenceDescriptor
evidenceReceipt
reviewInputRef
```

但 [screenplay_agent_task_executor.py](../../backend/application/screenplay_agent_task_executor.py#L215) 以及其验证逻辑仍读取：

```text
evidence
evidence.manifest
evidence.reviewInput
```

这属于旧兼容契约，不应继续保留。删除代码时必须同步删除或改写测试 fixture，尤其是 [test_screenplay_agent_rewrite.py](../../backend/tests/test_screenplay_agent_rewrite.py#L4281) 中仍构造旧 `evidence` 输出的测试。

当前本地 SQLite 数据库中没有发现旧形态的 Artifact batch 或 Run event；这只证明当前本地数据，不代表其他部署环境无需执行数据核查。

### P2：收拢标题生成的 Provider 分支

[ai.py](../../backend/routers/ai.py#L759) 的 `/ai/title` 仍在 Router 中直接按 Provider 调用：

- `openai_chat.generate_title`；
- `anthropic_chat.generate_title`；
- `zai_chat.generate_title`。

这不是 AgentCore 绕过，也不是 child run 问题，但它把 Provider 选择、标题 fallback 和 HTTP 处理混在了一起。

建议将其移动到 Application Service。不要为了标题接口伪造一个 Run 去调用当前要求绑定已有 Run 的 `AgentModelTaskRunner`。是否增加 PurrA 的 standalone bounded model task，应在有第二个真实需求时再决定。

## 5. 不要误删的部分

- [screenplay_checkpoint_planning.py](../../backend/application/screenplay_checkpoint_planning.py#L88)：它负责 Screenplay 检查点触发、业务输入、CAS/lease、暂停和续接，不是 PurrA 通用规划的重复实现。
- `_ACTIVE_TASKS` 与 Composition 的后台任务跟踪：前者是业务派发去重/取消，后者是进程生命周期管理，所有权不同。
- [screenplay_agent_runtime_cleanup.py](../../backend/database/crud/screenplay_agent_runtime_cleanup.py#L1)：这是可审计的旧数据清理工具，不是旧 Agent 执行链路。
- 数据库 schema migration：迁移和旧数据退休必须与运行时代码分开判断。
- `persist_legacy_event`：这是 Run Repository 的适配兼容行为，是否删除需要单独评估所有 Host 是否都已切换到 Output Repository，不能和业务旧代码一起删。

## 6. 当前验证边界

- 之前的确定性架构门禁和测试已通过；该审计未修改代码，因此没有重新宣称新的全量测试结果。
- 没有执行真实 Provider E2E；该验证由用户负责。
- 任何新的 Run 故障必须先查询本地 SQLite 中的 Run、events、Output、Artifact、Task、Operation 绑定，再判断是 Provider、模型输出、工具契约、业务校验、SSE 或恢复问题。

## 7. 推荐执行顺序

1. 删除旧 evidence fallback，并同步清理旧测试 fixture；
2. 跑确定性 Screenplay 和 Agent 边界测试；
3. 把 `/ai/title` Provider 选择收进 Application Service，但暂不扩展 PurrA Core；
4. 做真实 Provider E2E；
5. 只有当真实使用场景证明需要时，才设计 standalone bounded model task；
6. 再评估是否可以删除 Run Repository 的 legacy event 兼容参数。


## 9. 文档优先级

发生冲突时按以下顺序判断：

1. 当前代码和数据库真实状态；
2. `purra-screenplay-refactor-charter.md` 的架构不变量；
3. 当前阶段设计计划；
4. 本交接文档；
5. 历史 handoff、旧测试 fixture 和旧实现说明。

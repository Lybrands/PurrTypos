# Agent Core

简体中文 | [English](README.md)

`agent_core` 是与具体产品无关的 Agent 执行内核，负责定义通用合约、规划与运行时策略、Run 生命周期、工具授权、人工审批及多 Agent 编排端口。该包必须能够仅依赖 Python 标准库独立导入和运行。

## 依赖方向

```text
宿主 / 领域 / 基础设施
          ↓
       应用服务
          ↓
      agent_core
```

Core 不得导入宿主传输协议、产品领域、模型 SDK、数据库驱动或具体持久化适配器。以下静态测试会持续检查这一架构约束：

- `tests/test_agent_core_boundaries.py`
- `tests/test_application_boundaries.py`

## 动态规划

初始计划只是暂定路线，而不是不可修改的执行脚本。每次工具步骤成功完成或发生可恢复的执行失败后，Runtime 都会把可信的工具结果续写、已完成步骤历史和剩余轮次预算交回 Planner。实现了 `DynamicTaskPlanner` 的 Planner 可以在下一轮模型调用前保留、替换、新增或删除未来步骤。

已经完成、阻塞或失败的步骤属于不可改写的执行历史；Controller 会把每次修订后的未来计划和对应的 `run.todos_updated` 事件原子持久化。Runtime 会为请求范围内的候选工具保留上下文预算，但每一轮只暴露最新当前步骤授权的工具。因此，重新规划不能绕过工具策略、人工审批、作用域校验或幂等边界。授权拒绝和人工审批拒绝不属于可恢复的工具执行失败。

## 可复用持久化端口

- `RunRepository`：保证 Run 生命周期状态与 Outbox 事件的原子持久化。
- `ExecutionLeaseStore`：管理执行所有权、心跳续租和持久化取消请求。
- `DelegationRepository`：管理父子任务队列、并发认领和结果回传。
- `CheckpointStore`：提供持久化、基于游标的 Run 快照。
- `ApprovalGateway`：管理只能决议一次的人工审批。
- `ToolIdempotencyGateway`：保证具有副作用的工具调用可以安全重放。

具体适配器由宿主的 Composition Root 统一装配。当前宿主在 `infrastructure/persistence` 下提供 SQLite 实现。

## 实时多 Agent 委派

写作产品宿主只会为根对话 Run 注册 `delegateToAgents` 工具。父 Agent 可以把一至三个相互独立的目标委派给 `researcher`、`reviewer` 或 `analyst` 子 Agent。每个被接受的目标都会先持久化入队，再在父 Run 的并发额度内被认领，并携带不可变的 `RunLineage` 作为独立 Agent Run 执行。

子 Run 与父 Run 共用取消信号，不能继续递归委派，并且只能获得产品角色注册表允许的工具模式；当前写作产品的子 Agent 均为只读角色。父 Agent 会等待必需的子 Agent 返回结果后再继续；与此同时，委派生命周期事件会被实时合并到父 Run 的 SSE 流中。因此桌面端工作日志可以直接展示“等待中、已认领、执行中、已完成/失败/取消”，不需要等父 Agent 的最终回答结束。如果实时连接中断，持久化 Checkpoint 快照仍然是恢复状态的可信来源。

当前写作产品在 `domains/writing/agent_roles.py` 中统一定义角色 ID、展示名称、委派说明、可信角色指令和允许的工具模式。应用服务只消费注入的注册表；Agent Core 和桌面端都不再写死写作产品的角色名称。

## 添加新的适配器

新的持久化适配器必须通过 `tests/support/agent_adapter_contracts.py` 中与数据库无关的行为契约测试。这套测试会验证：

- 执行所有权和单一租约持有者
- 多执行器竞争时只有一个认领成功
- 持久化取消及租约释放
- 委派并发额度和优先级
- Checkpoint 事件游标和分页恢复
- 工具调用的幂等重放

因此，未来增加 PostgreSQL、内存存储或其他产品专用实现时，可以复用同一套断言验证与 Core 的兼容性。

## 产品扩展边界

以下内容必须保留在 Core 之外，由各产品通过端口、策略或注册表注入：

- Agent 角色及角色 Prompt
- 领域上下文提供器
- 工具目录和工具处理器
- 规划策略及响应验证策略
- 模型供应商适配器
- HTTP、SSE、WebSocket 或桌面端事件映射
- SQLite、PostgreSQL 等具体持久化实现

Core 只认识通用的 `agent_role`、`objective`、`input`、`result`、`priority` 和 `required` 等编排概念，不内置研究、写作、审校等产品角色。

## 第二产品接入原则

验证 Core 通用性的标准是：第二款产品只新增自己的领域适配器和基础设施适配器，不修改 Agent Core 的执行语义。

如果新产品必须在 Core 中增加产品名称判断或领域字段，说明抽象边界仍需调整；应优先扩展通用端口或策略，而不是在 Core 中加入产品分支。

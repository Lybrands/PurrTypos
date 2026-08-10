# PurrA 与剧本对话重构章程

> 状态：Phase 0–5 已完成。本文定义 2026-08 重构的层级所有权、目标契约、实施顺序和验收门槛。旧的 Agent 完成记录只作为历史材料，发生冲突时以本文为准。

## 1. 重构目标

本次重构同时解决两个问题：

1. 把已经膨胀的 PurrA 编排实现拆成可独立验证的阶段，同时保持 Run 生命周期、原子提交、恢复、取消和工具安全语义不变；
2. 删除剧本 Agent 现有的 AI 对话执行链路，基于新的 Core 契约重建独立的剧本 Conversation/Operation 应用。

重构完成不以“文件变小”作为标准，而以状态与结果的唯一所有者重新明确、依赖方向可以自动检查、断线和重放行为可以确定性验证作为标准。

## 2. 不可妥协的不变量

1. Core 只拥有 Run 事实，不拥有任何剧本业务结果。
2. 剧本业务域只拥有 Project、Operation、Revision、Head、Candidate 与 Stage，不实现 Run 生命周期。
3. Application 是唯一允许把业务命令转换为 Core 请求、再把 Core 事实投影为业务结果的层。
4. Core 可以传输不透明的领域事件，但不得解释、分支或校验其业务字段；携带不等于拥有。
5. 正式剧本任务只以 Operation 作为业务控制权威；Run、Long Task 和 SSE 连接都不是第二个业务状态机。
6. 普通咨询对话不创建 Operation、Candidate 或 Revision。
7. 明确的阶段生成命令由宿主编译确定性的 `ExecutionRecipe`；模型不生成机械 DAG，也不决定业务提交顺序。
8. 用户消息、Assistant Turn、Run、Operation 和 Revision 的关联都服务端持久化；前端缓存不得成为事实源。
9. SSE 只是带游标的订阅协议。订阅断开不等于取消，刷新必须能从快照和事件重建相同界面。
10. 所有命令具有幂等键；同一正式任务最多产生一个 Operation 和一个 Candidate Revision。

## 3. 层级所有权

| 层 | 拥有的输入/状态/结果 | 明确禁止 |
|---|---|---|
| `purra` | `RunRequest`、`TaskSpec`、通用 `ExecutionRecipe`、Run 状态、lease、恢复额度、工具执行证据、usage、Core Event、`RunOutcome` | Project、Stage、Operation、Candidate、Revision、剧本工具名称或 DTO |
| `domains/screenplay` | 剧本聚合、领域值对象、不变量、Stage 推导、Candidate/Revision 规则、领域工具输入输出 | Provider、SSE、SQLite、HTTP DTO、Run lease/retry/recovery |
| `application/screenplay_agent` | 对话意图分流、业务命令处理、Workflow Compiler、上下文查询协调、Run Binding、Run Event Projector | 实现 Core 状态机、直接执行 SQL、保存前端临时状态 |
| `infrastructure` | Provider、SQLite Repository、Outbox、事件订阅和端口实现 | 改写 Core 或剧本领域状态语义 |
| `routers/screenplay_v2` | HTTP 校验、身份/幂等头解析、调用 Application、响应映射 | 组装 Core、推导 Stage、直接操作 Repository |
| 剧本前端 | 输入、展示、调用命令、按 cursor 订阅和重取 Workspace | 推导 Head/Candidate、客户端权威落库、用 stream abort 代替业务取消 |

### 3.1 Core 结果

Core 的结果只允许包含通用执行事实：

```text
RunOutcome
├── run_id
├── status
├── final_response / structured_output_ref
├── usage
├── failure
└── last_event_cursor
```

`RunOutcome` 不出现 `project_id`、`target_role`、`operation_id`、`revision_id` 或 `stage`。宿主关联信息使用通用且不透明的 `RunBinding(namespace, aggregate_id, command_id)` 保存，Core 不读取其中的业务含义。

### 3.2 剧本业务结果

以下结果只能由剧本域及其 Application Projector 生成：

```text
ScreenplayOperation
ScreenplayCandidateRevision
ScreenplayRevision
ScreenplayProjectHead
ScreenplayStage
```

Core 的原子事件提交可以调用宿主注入的 Projector；Projector 在同一数据库事务中消费领域 effect，创建 Candidate Revision，并把公开事件替换为只含 Revision 引用的事件。Core 只执行投影端口，不理解投影内容。

## 4. 目标依赖方向

```text
routers / frontend
        ↓
application/screenplay_agent ─────→ purra public contracts
        ↓                                  ↑
domains/screenplay                         │
        ↑                                  │
infrastructure implements domain/core ports┘
```

硬性规则：

- `purra` 只能依赖标准库与自身；
- `domains/screenplay` 不得依赖 Application、Infrastructure、Router、Schema 或 Database；
- 剧本纯领域模型不依赖 Core；需要实现 Core Port 的策略放入 Application adapter；
- Application 可以依赖领域与 Core 公共 contracts/ports，不得依赖 Core 的 `runtime.py`、`engine.py` 内部实现；
- Router 只能依赖 Application facade 与自己的请求/响应 DTO；
- Infrastructure 通过构造注入实现端口。

迁移期当前已知越界由 `backend/tests/test_agent_refactor_boundaries.py` 建立只减不增的债务基线，最终目标是所有 allowlist 清零。

## 5. 目标模块结构

```text
packages/purra/src/purra/
  contracts/                 # enums + messages/planning/context/tools/runs 兼容入口
  ports/                     # model, context, planning, tools, persistence
  runtime/
    orchestrator.py          # 薄状态机
    model_round.py
    tool_round.py
    response_finalization.py
  engine/                    # `purra.engine` 兼容入口
    orchestrator.py
    options.py
    context_phase.py
    durable_execution.py
    planning_validation.py

backend/domains/screenplay/
  project/                   # Project, Stage, Head
  operation/                 # Operation 状态与业务命令
  revision/                  # Candidate/Revision 不变量
  tools/                     # 领域 Schema 与校验

backend/application/screenplay_agent/
  conversation_service.py
  operation_service.py
  workflow_compiler.py
  context_query.py
  run_binding.py
  run_projector.py
  event_mapping.py

backend/infrastructure/screenplay/
  persistence/
  context_queries/
  tools/

backend/routers/screenplay_v2/
  projects.py
  conversations.py
  operations.py
  revisions.py
```

拆分期间旧 `purra.contracts` 和 `purra.ports` 可以作为纯 re-export facade 暂时保留；不得在 facade 中加入行为或产品分支。

## 6. 新剧本对话命令边界

剧本对话只有两种服务端分流结果：

```text
ConversationTurn
  └── read_only_answer       # 不创建 Operation

ScreenplayCommand
  └── Operation
      └── ExecutionRecipe
          └── Candidate Revision
```

建议的公共接口：

```http
POST /api/screenplay/v2/projects/{projectId}/conversation/turns
GET  /api/screenplay/v2/projects/{projectId}/conversation/snapshot
GET  /api/screenplay/v2/projects/{projectId}/conversation/events?after={cursor}
POST /api/screenplay/v2/conversation/turns/{turnId}/cancel

POST /api/screenplay/v2/projects/{projectId}/operations
GET  /api/screenplay/v2/operations/{operationId}
POST /api/screenplay/v2/operations/{operationId}/cancel
```

一次提交必须先持久化用户 Turn，再由服务端判断只读回答或正式 Operation。正式 Operation 的创建、Run 调度意图和命令回执必须处于一个可恢复提交边界，不能再由前端发第二个通用 Chat 请求启动。

## 7. 分阶段迁移

### Phase 0：准备与冻结

- 建立本文、现状清单、V2 验收入口和架构债务门禁；
- 完整测试基线通过；
- 不改变生产行为。

### Phase 1：Core 无行为拆分

- 将 Runtime、Engine、Contracts 和 Ports 从单文件迁为包；
- 先迁移无共享可变状态的模型轮次、工具轮次、响应收口、上下文、规划校验、持久执行与 Run Repository 职责；
- 旧的 `purra.runtime`、`purra.engine`、`purra.contracts` 和 `purra.ports` 导入保持兼容；
- 保持事件、状态、错误码和持久化顺序不变；
- 每次提取都先通过 Core characterization tests。

### Phase 2：Core/Application 边界收紧

- 引入通用 Run Binding、Execution Recipe 和 Domain Projector contracts；
- 从通用 `AgentRunService`、`ChatStreamRequest`、`sse_mapping` 移除剧本逻辑；
- 将剧本数据库读取移到 Application Query Port/Infrastructure。

完成状态：以上三项已全部落地。Run Binding 已不可变持久化；剧本 Workflow Compiler 负责生成 Execution Recipe；SQLite Run Repository 只调用通用 Domain Projector 端口。通用 Run Service、请求映射和 SSE 映射中的 screenplay 引用均为 0，剧本 Domain 中的 Database import 也为 0。旧 `/ai/chat/stream` 的剧本兼容入口、旧 long-task 路由与前端 reducer 暂时保留到 Phase 3/4，不视为新链路。

### Phase 3：新剧本对话纵向链路

- 新增独立 Conversation API、持久化 Turn、Snapshot 和 cursor events；
- 新增只读对话路径与正式 Operation 路径；
- 新前端只消费剧本 API，不复用通用 Writing chat accumulator。

完成状态：原生 Conversation/Turn/Event 持久化、Snapshot/cursor API、只读与正式 Operation 原子分流、Turn 执行 lease/heartbeat、显式 cancel 与凭据重注入 resume 已落地。新前端的 `conversationClient.ts` 与 `conversationState.ts` 只依赖剧本 API 和持久化 Snapshot，不读取通用 Writing stream/chunk 状态。

### Phase 4：切换并删除旧实现

- 切换剧本页面到新链路；
- 删除旧 SSE adapter、旧 chunk screenplay branches、旧 long-task conversation routes 和双请求启动逻辑；
- 不保留运行时 fallback 或长期双轨。

完成状态：剧本页面已切换到持久化 Snapshot 与原生 Turn submit/cancel/resume；普通问答只提交一个 Turn，正式任务由同一次提交原子创建 Operation。旧页面 SSE/Run monitor、长任务对话 adapter、通用 Writing chunk 剧本分支、通用 Conversation 剧本字段、通用 AI Router 的剧本请求和 long-task 路由已删除。Phase 4 门禁将这些模块、标记和引用锁定为 0，不允许恢复 fallback。

### Phase 5：收口

- 债务 allowlist 清零；
- 删除兼容 re-export 之外的迁移设施；
- 更新运维文档和完整故障注入报告。

完成状态：通用 `AgentComposition` 的 screenplay 引用已经归零，产品 Profile、Projector、Admission、Long Task 和上下文准备通过宿主 factory 注入。剧本 Operation 归属只认不可变 `RunBinding`，通用 Run、Work Item、Long Task、Artifact 的 `operation_id` 列和读写路径已直接删除，不迁移旧值。Artifact 是否已经投影由通用 projection receipt 表表示。旧产品请求模型、旧表和迁移审计设施均已删除；Core 包级 re-export 继续作为 Core 公共 API 存在，不承载剧本数据或业务语义。完整验证与故障注入结果见 [`purra-screenplay-phase5-verification.md`](purra-screenplay-phase5-verification.md)。

### 对话质量重写：native 链路二次收口

Phase 0–5 删除了旧 Writing Chat 双轨，但第一版 native Conversation 仍存在运行语义与对话质量问题。二次收口增加以下硬规则：

- `ScreenplayAgentRunRequest` 是独立产品输入，不继承 `ChatStreamRequest`；通用 Run Service 只依赖结构化 `AgentRunInput`；
- 咨询 Turn 启用项目与来源的 `READ` 工具，但绝不暴露 `PROPOSE`；正式 Operation 才同时拥有 `READ` 与 `PROPOSE`；
- 咨询对话使用简洁的剧作顾问规则，正式 Operation 才注入 Artifact/阶段提交协议；
- 同一 Session 同时最多一个 queued/running Turn，不再依赖渲染进程的临时发送队列；
- 失败或取消 Turn 不进入后续模型历史；恢复会清除旧半截回答和旧 Run/Revision 引用，并递增 Turn attempt；
- Conversation event 只做 Snapshot 失效通知，永不复制 `screenplay.document_proposal` 全文；
- 页面以 SSE cursor 通知触发 Snapshot 刷新，低频轮询只作为订阅断线的恢复路径。

此前 native Conversation 表缺少 attempt，且无法安全表达新恢复语义。测试阶段启动时只定向删除这一个不兼容的旧 Conversation/Events 数据集；Project、Operation、Revision、Head、Artifact 与来源数据保持不变。

## 8. 每阶段验收门槛

每个阶段都必须满足：

1. Core 静态依赖测试通过；
2. 重构债务 ratchet 只减少、不增加；
3. V2 Project/Operation/Revision 验收通过；
4. Run 的取消、断线、重启、重复提交和终态竞争测试通过；
5. 前端 typecheck 与单测通过；
6. 后端完整测试通过；
7. `git diff --check` 通过；
8. 没有静默修改现有事件、错误码或持久化语义。

最终额外满足：普通问答零 Operation；正式命令一个 Operation/一个 Candidate；刷新可重建；通用 AI DTO/reducer/service 中 screenplay 引用为零。

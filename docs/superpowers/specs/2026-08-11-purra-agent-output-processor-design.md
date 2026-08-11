# PurrA 通用能力收口与 AgentOutputProcessor 架构设计

日期：2026-08-11

状态：已确认，等待按实施计划执行

适用范围：PurrA 通用 Agent 执行、模型调用、输出、运行生命周期、操作计时、持久化、重放与业务装配

## 1. 决策摘要

业务只能基于 PurrA 定义的通用能力开发。业务故障可以暴露框架缺口，但不能反向拥有、重写或绕过 PurrA 的执行语义。PurrA 定义通用状态机、不变量和稳定入口；剧本、小说等业务只注册工具、上下文、策略、校验器和领域效果。

当前 PurrA 已经有一批正确的单一所有者：`AgentCore`、`AgentRunController`、`AgentPlanner`、`CoreToolExecutor`、`ContextCompressionCoordinator`、`ArtifactLifecycle`、`WorkItemLifecycle`、`LongTaskCoordinator` 和 `RecoveryLedger`。本轮不重建这些已有能力，而是补齐已确认的通用缺口：

- 把现有 `ManagedModelExecutor` 升级为所有 Provider 调用共用的 `AgentModelInvocationManager`；
- 建立 `AgentOutputProcessor`，作为所有 Agent 向外输出的唯一出口；
- 建立 `AgentRunSupervisor`，使服务端 Run 执行生命与 SSE 订阅生命解耦；
- 建立 `AgentOperationController`，统一模型、工具、校验和子 Run 操作的状态、时间戳和耗时；
- 建立 `AgentDelegationCoordinator`，把父/子 Run 委派状态、结果聚合和事件联合从应用层收回 PurrA。

这五个名称表示五种不同的通用不变量，不合并成一个什么都做的大处理器。`AgentCore` 仍是唯一完整 Agent Run 入口，并按固定管线协调上述组件。

公开自然语言只能来自 Provider 的真实模型增量。宿主、工具、领域服务和前端不得拼装、补写或模拟 `commentary` 与最终回答。工具、进度、计时和错误继续作为结构化事件展示，不能转写成 Assistant 文案。

新实现不保留旧输出协议兼容分支。上线新协议时，定向清理旧剧本 Agent 的对话、输出事件和运行时投影；剧本项目、Revision、Artifact、候选正文、原作及其他业务数据必须保留。

本文取代 `2026-08-11-agent-public-commentary-ownership-design.md` 中仅删除宿主文案注入的局部方案，也取代旧设计中允许把结构化 JSON 字段或工具参数摘要投影为公开文案的规则。

## 2. 事故证据与根因

### 2.1 最终回答是事后模拟流式

真实持久化 Turn `spaturn_79e8629652c143d891615475cf1819c0` 中，Run `run_2c5fcbcfcfc640d3` 的 Provider 增量先完整形成：

```json
{"finalResponse":"已完成第1集至第8集各集的审阅……"}
```

宿主等待 JSON 完整结束后提取 `finalResponse`，再通过 `visible_stream_chunks()` 拆成单字 `delta`。数据库中的事件 `2450440` 至 `2450519` 全部写于 `2026-08-11 14:41:59`。这不是 Provider 实时增量，而是完成后模拟流式。

### 2.2 过程文案与工具调用同时出现

事件 `2291607` 同时包含完整 `toolCalls` 和整段 `commentaryDelta`：“针对第6集五场戏的结构与节奏进行独立审阅……”。该文案来自结构化候选或工具参数中的 `processSummary` 投影，只有在工具调用参数完成后才被一次性发布，不是公开模型文本的实时流。

### 2.3 现有系统没有单一输出所有者

当前生产链路存在多个独立出口：

- PurrA Runtime 直接产生 `ASSISTANT_COMMENTARY_DELTA` 和 `ASSISTANT_FINAL_DELTA`；
- `screenplay_structured_call.py` 使用 `JsonStringFieldProjector` 从结构化 JSON 中提取公开文字；
- `screenplay_tool_calling.py` 从宿主模板、候选字段和工具参数生成 `commentaryDelta`；
- `screenplay_progress_stream.py` 把完整字符串拆成单字 chunk；
- `sse_mapping.py` 根据旧事件类型再次判断 `delta`、`commentaryDelta` 和诊断内容；
- 前端实时 reducer、持久化恢复与最终 Conversation 投影分别拼接输出。

因此，文字来源、流式时机、终态提交、持久化与重放没有共同的权威边界。此前对单一 UI 层或单一注入点的修补无法根治问题。

### 2.4 业务正在补框架缺口

输出问题不是唯一绕过点：

- `screenplay_structured_call.py` 直接组合 `AgentRunController` 和 `ManagedModelExecutor`，而不是进入完整 `AgentCore` 管线；
- `AgentRuntime` 直接调用 `ModelGateway.stream()`，而 Planner 和宿主子调用使用 `ManagedModelExecutor`，输出上限、终止分类、调用证据和发布意图没有同一所有者；
- `AgentCore.run()` 的公开迭代器被关闭时会将 Run 标记为 `consumer_disconnected`，把传输订阅生命误当成服务端执行生命；
- `AgentRunService` 在应用层手工多路复用子 Run 事件，并通过 `AgentComposition.append_run_event()` 直接写入父 Run；
- PurrA 只持久化生命周期、工具和领域事件，三种文本 delta 仍是 transport-only；剧本业务因此又建立 `screenplay_agent_chunks` 真源；
- `CoreToolExecutor` 能保证授权、幂等和副作用安全，但对外的 `TOOL_CALL_COMPLETED` 事件没有每个操作的 `startedAt`、`finishedAt` 和 `durationMs`；当前耗时只存在批次 Trace 中；
- `DomainEventProjector` 可在 Repository 事务中替换事件，但缺少“不得更改可见性、文字来源或输出通道”的 Core 级限制。

这些都是 PurrA 通用能力的缺口或收口不完整，不应继续由剧本、小说或 SSE 层分别修补。

## 3. PurrA 能力所有权审计

| 能力 | 当前权威组件 | 审计结论 | 必要调整 |
| --- | --- | --- | --- |
| 完整 Agent Run 入口 | `purra.api.AgentCore.run` | 已收口 | 保留唯一公开入口，禁止业务直接组合 Runtime/Controller |
| Run 状态转换 | `AgentRunController` + `RunStateMachine` | 已收口 | 保留原子状态/outbox 提交 |
| 计划 | `AgentPlanner` + AgentCore 编译/授权校验 | 已收口 | 业务只提供通用约束和能力注册 |
| 工具执行 | `CoreToolExecutor` | 已收口 | 业务只注册 handler/schema/policy，不得替代执行器 |
| 上下文预算与压缩 | AgentCore + `ContextCompressionCoordinator` | 已收口 | 保留为一条 Core 管线；业务只提供 ContextProvider/claim |
| Artifact 生命周期 | `ArtifactLifecycle` | 已收口 | 保留通用 CAS、顺序、校验和终态 |
| Work Item / Long Task | `WorkItemLifecycle` + `LongTaskCoordinator` | 已收口 | 业务只编译不透明 recipe 和注册 executor |
| 恢复决策 | `RecoveryLedger` + failure disposition | 基本已收口 | 保持 Core 决定重试/重规划/失败；业务只提供 typed failure |
| Provider 模型调用 | Runtime 直调 Gateway + `ManagedModelExecutor` | 收口不完整 | 升级为 `AgentModelInvocationManager`，所有调用经过同一意图、能力、终止和证据边界 |
| 向外输出 | Runtime、应用、剧本 chunk store、SSE 并存 | 完全缺失 | 新建 `AgentOutputProcessor` 及规范输出存储 |
| 服务端执行生命 | Agent iterator + `RunExecutionSession` + 应用 background task | 收口不完整 | 新建 `AgentRunSupervisor`，执行与订阅解耦 |
| 操作状态与计时 | Runtime Trace、Tool event、前端本地计时分散 | 完全缺失 | 新建 `AgentOperationController`，生命周期组件记录时间，输出处理器只传递 |
| 子 Agent 委派 | Core 有契约/Repository，应用层手工协调与多路复用 | 收口不完整 | 将通用委派状态机下沉为 `AgentDelegationCoordinator`；角色目录仍由业务注册 |
| 传输与错误展示 | `sse_mapping.py` 同时转换协议和编写文案 | 收口不完整 | SSE 只序列化规范事件；错误本地化是产品读模型，不是 Assistant 输出 |

审计结论不是“每行逻辑都新建一个 Processor”。只有当某项能力需要统一授权、顺序、幂等、状态转换或持久化时，才建立不可绕过的所有者。纯函数计算和输入映射不需要为了命名而包一层管理器。

上下文和错误展示是两个刻意不新增处理器的例子。上下文预算、压缩和每轮 Provider 输入已由 AgentCore + `ContextCompressionCoordinator` 在同一管线内决定；对话面板的动态用量应改为消费该管线与规范输出事件，而不并行再造一个业务 Context Manager。错误分类是 PurrA typed failure，本地化文案是产品读模型；它们不是 Assistant 内容，也不需要一个会生成自然语言的 Error Processor。

## 4. 目标与非目标

### 4.1 目标

1. 所有向外输出只有 `AgentOutputProcessor` 一个入口。
2. 公开过程文字和最终回答只来自 Provider 的真实 `content_delta`。
3. 每次模型调用在开始前确定输出用途，执行中不得重新分类。
4. 实时、断线恢复、刷新重放和 Conversation 投影消费同一份规范事件。
5. 每条公开输出都能追溯到 Run、Invocation、Provider 增量和稳定序号。
6. 生命周期时间由权威执行组件记录，处理器只排序和传递。
7. 输出失败不能重复执行工具、Artifact 写入、校验或 Revision 发布。
8. PurrA 保持业务无关，不出现 screenplay、Episode、Scene 或 Revision 语义。

### 4.2 非目标

- 不公开模型私有 chain-of-thought。
- 不把剧本正文或候选 payload 放入对话。
- 不使用前端字符串黑名单判断文案来源。
- 不通过定时器、逐字动画或人工拆分伪造流式。
- 不让 `AgentOutputProcessor` 计算业务耗时或控制 Run/Tool 状态机。
- 不为旧剧本 Agent 输出协议保留生产兼容路径。
- 不在没有性能证据时合并、节流或重新切分 Provider chunk。

## 5. 方案比较与选择

### 5.1 方案 A：只补 AgentOutputProcessor

只将文本和事件收到新处理器。这能禁止部分编排文案，但模型子调用、Run 执行寿命、每项操作计时和子 Agent 多路复用仍由业务层补齐。输出处理器会被迫吸收这些职责，最终成为新的补丁层，因此不采用。

### 5.2 方案 B：一个全能 AgentProcessor

把调用、输出、Run 寿命、操作时间、持久化和恢复全部放入一个巨型处理器。它表面上只有一个入口，实际上把多种状态机混在一起，无法独立验证副作用、订阅、输出提交和计时不变量，因此不采用。

### 5.3 方案 C：通用能力内核（推荐）

保留 `AgentCore` 作为唯一完整 Run 入口，并让模型调用、输出、Run 执行监管和操作生命周期各自拥有一个单一权威组件。组件通过 typed event 和 port 协作，不相互接管状态。

业务层不再拥有任何 PurrA 语义，只做三件事：把产品请求映射为通用契约、注册领域能力、消费规范输出。这个方案既修正依赖方向，又避免用一个大处理器集中所有职责，因此作为本稿推荐方案。

## 6. 架构边界

```text
Product request
      |
      v
Product adapter -- registers ports/policies/tools --> AgentCore
                                                    |
                      +-----------------------------+------------------+
                      |                             |                  |
                      v                             v                  v
             AgentRunSupervisor          AgentModelInvocationManager  Core capabilities
                      |                             |                  |
                      |                    Provider chunks             |
                      |                             |                  |
                      +-----------> AgentOperationController <---------+
                                                    |
                                                    v
                                         AgentOutputProcessor
                                                    |
                                         persisted canonical events
                                           /                    \
                                          v                      v
                                    replay/query port       live publisher
                                           \                    /
                                            +-------- UI -------+
```

### 6.1 PurrA 拥有

- 输出用途与可见性契约；
- 完整 Run 的唯一入口与执行顺序；
- Provider 调用的唯一内部边界；
- 服务端 Run 执行监管与订阅解耦；
- 模型、工具、校验、压缩和子 Run 的通用 Operation 生命周期；
- `AgentOutputProcessor`；
- 规范 `AgentOutputEvent`；
- 输出流状态机；
- 来源授权、顺序、幂等和持久化端口；
- 实时发布端口；
- 与业务无关的输出安全策略端口。

### 6.2 生命周期组件拥有

- `AgentRunSupervisor` 拥有服务端执行 task、lease、心跳、显式取消和订阅者管理；
- `AgentOperationController` 拥有 Operation 的 `startedAt`、`finishedAt` 和 `durationMs`；
- `AgentRunController` 拥有 Run 状态转换和终态提交；
- Run/Tool 的状态转换与终态；
- 工具副作用、幂等和恢复；
- Artifact 生命周期。

`AgentOutputProcessor` 不启动计时器、不推算耗时，也不以 SSE 到达时间代替真实执行时间。

### 6.3 业务 Profile 拥有

- 把产品命令映射为 PurrA 公开契约；
- 注册 ContextProvider、ToolRegistration、PlanningPolicy、Validator、Judge 和 typed domain effect；
- 编译业务 Manifest/Recipe，但不执行 PurrA 状态机；
- 在 PurrA 通用 Artifact 事务中实现业务校验与产品发布端口；
- 把业务对象投影为文档、候选稿、审阅项或其他产品视图。

业务不拥有“先调哪个模型、何时打开公开流、如何重试、如何写 Run 事件”这些 PurrA 语义。它也不得直接产生公开文字事件，不得直接写 `delta` 或 `commentaryDelta`。

### 6.4 应用与前端拥有

- 应用装配层把通用处理器、持久化和发布端口连接起来；
- SSE 只传输规范事件，不再判断文字语义；
- 前端使用同一个 reducer 折叠实时和重放事件；
- UI 把结构化状态渲染为操作、进度、计时和错误，但不把它们伪装成模型发言。

### 6.5 不可绕过规则

- 完整 Agent 任务只能调用 `purra.api.AgentCore`；
- Runtime、Planner、Compactor、Judge 和子调用只能通过 `AgentModelInvocationManager` 访问 Provider；
- 业务不得直接 import `purra.runtime`、`purra.run_controller` 或具体 Gateway；
- 只有 `AgentRunController` 能改变 Run 状态；
- 只有 `AgentOperationController` 能开始和结束 Operation；
- 只有 `AgentOutputProcessor` 能写规范输出事件；
- `DomainEventProjector` 不得替换文字来源、可见性、通道、顺序或终态，只能在 Core 声明的 typed effect 提交点执行业务原子投影；
- `TaskAdmissionDecision.message`、`LongTaskDispatchReceipt.message` 和错误本地化只能作为结构化状态/显示元数据，不得成为 Assistant 回答；
- SSE、WebSocket 或其他 Transport 只能订阅和序列化，关闭订阅不会取消 Run。

### 6.6 唯一 Run 入口与订阅解耦

现有 `AgentCore.run()` 将“启动 Run”和“消费 async iterator”绑成同一个生命周期，这使任何路由、SSE 或业务服务都必须自己保活 background task。新的 PurrA 公开入口改为执行句柄语义：

```text
handle = AgentCore.submit(request, options)
handle.run_id
handle.subscribe(after_sequence)
handle.wait()
handle.cancel(reason)
```

`submit()` 是唯一完整 Agent 任务入口。`AgentRunSupervisor` 在服务端拥有执行 task 和 lease，`subscribe()` 只从 `AgentOutputProcessor` 的规范事件流订阅。订阅者断开、超时或重新连接都不改变 Run 状态；只有 `handle.cancel()`、持久化控制面取消命令或服务端执行失效才能终止 Run。

业务路由不再调用 `track_background_run()`、不再在 `finally` 中 `aclose()` 执行流、不再通过关闭 HTTP 响应释放 Run。

## 7. 调用与事件契约

### 7.1 模型调用意图

每次模型 Invocation 由 PurrA 创建时，必须一次性固定两个通用契约：`AgentOutputIntent` 和 `OutputCommitMode`。业务只能通过任务契约表达需要的结果，不能在模型返回后改写这两个值。

`AgentOutputIntent` 区分内容的可见性与通道：

- `execution_public`：Provider `content_delta` 是公开过程说明；
- `final_public`：Provider `content_delta` 是正式回答的实时流；
- `structured_private`：内容用于 JSON、候选或协议解析，不进入产品对话；
- `reasoning_private`：内容仅进入受控诊断。

`OutputCommitMode` 区分何时允许对外发布：

- `live`：真实 Provider chunk 到达后立即持久化和发布；首个公开 chunk 发布后，不允许以“全文校验失败”为由撤回、修复或重试同一回答；
- `gated`：Provider chunk 只进入私有候选流，完整性、结构和语义校验全部通过后才能提交为结果；该流不能事后拆分为公开伪流；
- `private`：只用于 Planner、Judge、Compactor、结构化协议或候选 Artifact，永不进入产品对话。

Planner JSON、候选 JSON、工具参数和 `executionSummary`、`processSummary` 等结构化字段一律属于 `structured_private`。它们不能因为字段名看起来像摘要而被提升为公开文案。

PurrA 对意图与提交方式做 fail-closed 组合校验：带全文 Validator/Judge 的候选生成不得声明 `live`；`structured_private` 和 `reasoning_private` 不得声明公开通道；公开输出不得携带工具参数或 Artifact payload。

`AgentModelInvocationManager` 是该契约的唯一执行者。它统一解析输出上限、调用能力、终止原因、使用量、调用证据和 Provider stream；`AgentRuntime` 不再直接调用 `ModelGateway`。

### 7.2 处理器接口

处理器提供以下语义接口：

```text
openModelStream(invocationReceipt, outputIntent, commitMode)
acceptProviderChunk(outputStreamId, providerChunk)
acceptOperationEvent(operationEvent)
acceptRunLifecycleEvent(runLifecycleEvent)
acceptToolEvent(toolEvent)
acceptDomainEffectEvent(domainEffectEvent)
finishModelStream(outputStreamId, finishReason)
abortModelStream(outputStreamId, errorCode)
```

只有 `acceptProviderChunk()` 能产生公开自然语言。其他接口使用不同的封闭 typed contract，不接受通用 `AgentEvent` 或任意 `channel/payload`，因此 Runtime、Tool、Domain 从类型和运行时契约上都不能伪造 `commentary` 或 `final` 文本。

### 7.3 规范事件

`AgentOutputEvent` 至少包含：

```text
eventId
outputStreamId
runId
turnId
invocationId
sequence
source
kind
channel
visibility
payload
occurredAt
emittedAt
```

- `sequence` 是同一 Turn 输出流的权威顺序；
- `source` 区分 provider、runtime、tool 和 domain；
- `occurredAt` 来自真实动作发生的组件；
- `emittedAt` 是处理器发布时间；
- `channel` 区分 commentary、final、operation、lifecycle、error 和 diagnostic；
- `visibility` 区分 public、private 和 diagnostic。

## 8. 真实流式规则

1. Provider 到达一个 `content_delta`，处理器就接收一个增量。
2. 处理器不得等待完整文本后重新拆分。
3. 处理器不得把结构化字段、工具参数或宿主摘要改造成增量。
4. 首版不合并相邻 Provider chunk；如存储或发布性能不达标，必须先提供测量数据，再设计保留原始顺序与时间证据的批处理。
5. Provider 本身若只提供大块或非流式结果，产品如实反映其能力，不能用动画伪装。
6. 安全策略只能放行、拒绝或脱敏，不能补写前缀、后缀、过渡句或完成总结。

## 9. 校验与公开输出事务

“真实流式”和“完整生成后再做全文校验”不能同时作用在同一份可见文本上：全文校验只能在 Provider finish 后得到结果，而真实流式要求在 finish 前已经发布 chunk。把已完成的文本校验后再逐字推送，仍然是伪流式，不允许。

因此 PurrA 定义通用的两种响应事务，而不按剧本或小说分支：

### 9.1 直接公开回答

用于不需要全文校验和业务原子发布的普通问答：

```text
public invocation(live)
  -> persist each provider chunk
  -> publish each provider chunk
  -> provider finish
  -> commit final response
```

这条路径可以真实流式。但是首个公开 chunk 出现后，PurrA 不得再以全文数量、连贯性或摘要长度校验失败为由替换这份回答。

### 9.2 受校验结果与公开呈现

用于需要完整性、数量、连贯性、安全或业务原子发布校验的结果：

```text
private candidate invocation(gated)
  -> validate complete candidate
  -> commit typed result / Artifact through registered port
  -> obtain a bounded PublicFactBundle
  -> optional public presentation invocation(live, no tools)
  -> commit Conversation answer if public text exists
```

候选流从来不是公开的，因此用户不会看到“校验前的临时输出”。校验通过后，如果结果是长正文、差异、报告或候选稿，它在 Artifact/文档区作为已提交结果查看，不伪装成 Assistant token stream。如果任务契约需要对话结论，它由独立 `final_public` 调用生成并真实流式发布；如果模型没有产生公开结论，只展示结构化结果，宿主不补一句固定模板。

`PublicFactBundle` 是 PurrA 通用契约，只包含已提交、可向用户公开的事实和产品引用，不包含业务正文、候选 payload、内部 Trace、数据库 ID 或未公开状态。剧本、小说和其他 Profile 只实现“已提交结果 -> 公开事实”的 typed port，不编排第二次调用、不推送 chunk、不提交 Conversation。这些顺序仍由 PurrA 通用响应事务拥有。

现有 Writing Agent 的连贯性、数量和摘要长度校验，以及 Screenplay Agent 的候选稿/审阅报告校验，都通过这一套通用事务运行，不在 PurrA 中增加任何 writing 或 screenplay 分支。

## 10. 生命周期计时

`AgentOperationController` 为模型调用、工具调用、校验、上下文压缩和子 Run 统一建立 Operation。业务只能附加受限的显示元数据，不能自行声明开始、完成或耗时。

工具调用示例：

```text
tool.started
  operationId, runId, invocationId, toolCallId, toolName, startedAt

tool.completed
  operationId, status, finishedAt, durationMs, errorCode
```

`startedAt` 和 `finishedAt` 使用可持久化壁钟，`durationMs` 由生命周期组件使用单调时钟结算，避免系统时钟跳变造成负耗时。`AgentOutputProcessor` 原样排序、持久化并传递这些结构化事件。前端在运行中显示 `now - startedAt`，收到终态后固定使用 `durationMs`。刷新页面后使用持久化时间恢复，不能重新从零开始。

处理器自己的 `emittedAt` 仅用于发布延迟与审计，不能用于计算工具耗时。

## 11. 持久化、顺序与重放

处理顺序固定为：

```text
receive
  -> authorize and normalize
  -> allocate sequence in repository transaction
  -> persist AgentOutputEvent and related state mutation atomically
  -> publish SSE
```

数据库提交必须先于 SSE。持久化失败时，处理器停止发布并使 Run 进入可恢复失败，前端不能看到数据库中不存在的输出。Run 终态与对应终态事件、`final_public` 流 commit 与 Conversation 正式回答投影，都必须各自在同一个事务中提交。

通用持久化包含：

- output stream 记录：Invocation、Intent、CommitMode、来源、状态和终态；
- canonical event journal：有序增量、结构化事件、时间戳和幂等键。

具体 SQLite 表和 Repository 位于基础设施层，PurrA 只定义端口。实施时应优先升级现有 `agent_run_events` outbox，不再叠加一份并行事件真源；仅 output stream 状态需要独立记录。`eventId` 和来源序号形成稳定幂等约束；重复投递不能产生重复 UI 内容。

并行子 Run 按处理器实际接收顺序分配 Turn 级 `sequence`，同时保留 `sourceRunId`、`parentRunId` 和 `invocationId`。前端以最后收到的 `sequence` 作为 cursor，重连只读取其后的规范事件。

Conversation 的最终文本可以保留为查询投影，但只能由已 `committed` 的 `final_public` 输出流生成，不再是独立真源。

## 12. 失败、中断与恢复

每个模型输出流状态为：

```text
open -> committed
open -> aborted
```

- `execution_public` 中断：保留已持久化的真实过程文字，标记该段未完成，不补写总结；
- `final_public` 中断：已显示内容标记为未完成，不进入正式 Conversation 回答；
- 重试最终回答：创建新的 output stream，只重试最终文本调用，不重复候选生成、工具、副作用、校验、Artifact finalize 或 Revision 发布；
- 新的最终流成功后替换未完成的临时展示，并成为唯一正式回答；
- SSE 订阅断线不取消服务端 Run，重连按 cursor 补齐；
- Provider 中断、持久化失败、用户取消和输出策略拒绝使用不同错误码；
- 错误通过结构化状态显示，不作为 Assistant 文案。

## 13. 安全与策略

- `structured_private` 和 `reasoning_private` 不能在运行中切换为 public；
- PurrA 提供通用 `AgentOutputPolicy` 端口，产品可注入业务特定的禁止内容规则；
- 策略拒绝公开增量时，处理器终止相应输出流并保留诊断原因；
- 策略不能生成替代文案；
- 最终回答调用只接收公开完成事实，从源头避免正文泄漏；
- 工具参数和 Artifact 内容只能以结构化私有数据流转。

## 14. 旧链路删除与数据迁移

### 14.1 删除生产逻辑

- 删除业务层直接组合 `AgentRunController` + `ManagedModelExecutor` 的完整 Agent 路径；
- 删除 `AgentRuntime` 直接调用 `ModelGateway`的分支，统一经过 `AgentModelInvocationManager`；
- 删除 `AgentComposition.append_run_event()` 这个任意应用事件写入口；
- 删除 `AgentRunService` 对子 Run 事件的手工持久化和多路复用，改由通用委派协调与输出处理器完成；
- 删除 iterator/HTTP/SSE 关闭触发 `consumer_disconnected` Run 取消的逻辑；
- 删除 `JsonStringFieldProjector` 和结构化 JSON 字段公开投影；
- 删除 `visible_stream_chunks` 事后拆字；
- 删除 `host_progress`、`progress_items` 和候选参数摘要投影；
- 删除剧本业务直接写 `delta`、`commentaryDelta` 的接口；
- 删除 `screenplay_agent_chunks` 作为第二输出真源的生产读写路径；
- 删除 Task Admission、Long Task receipt 或错误映射将宿主 `message` 字段写成 Assistant 正式回答的路径；
- 删除 PurrA Runtime 绕过处理器直接发布公开文本的分支；
- 删除 SSE 层对旧公开文本字段的语义判断；
- 删除前端使用到达时间或本地状态推测每项 Operation 开始/结束时间的路径；
- 删除前端按旧 chunk 来源猜测、拼接或降级的路径；
- 禁止新旧输出协议双写和运行时 fallback。

### 14.2 定向清理旧剧本 Agent 数据

迁移程序先从剧本项目拥有的 Session、Turn、Task 和 Run 解析完整关联集合，输出逐表行数和标识范围，再在单一事务中删除：

- 对应剧本 Agent 的 Conversation 输出记录；
- `screenplay_agent_chunks` 和 `screenplay_agent_events` 中的旧输出；
- 对应 Turn、Operation、Task、Run 的旧运行时投影；
- 与这些 Run 直接绑定的 Todo、Delegation、Long Task、Work Item、Approval 和输出诊断记录。

必须保留：

- `screenplay_projects`；
- `screenplay_revisions`、Revision parts、source refs 和 inputs；
- `screenplay_deliverables`、project heads 和 working copies；
- `ai_agent_artifacts`、Artifact batches、claims 和已发布候选内容；
- 审阅决定与接受事件；
- Book、Outline、Article、人物、世界设定和 Story Memory 数据。

迁移必须提供只读 dry-run 清单。任何范围解析、外键或事务步骤失败时整笔回滚，禁止模糊字符串匹配和全表清空。

## 15. 测试与发布门禁

### 15.1 PurrA 单元与架构门禁

- 业务生产代码不得 import PurrA Runtime、RunController 或具体 ModelGateway；
- 所有 Provider 调用均可追溯到 `AgentModelInvocationManager`；
- 公开文字只能由 `AgentOutputProcessor.acceptProviderChunk()` 产生；
- 每个 Invocation 必须声明不可变 `AgentOutputIntent` 和 `OutputCommitMode`；
- 带全文 Validator/Judge 的 Invocation 不得使用 `live`；
- 只有 `AgentRunSupervisor` 能保活/终止执行 task，Transport 不得拥有 Run 寿命；
- 只有 `AgentOperationController` 能产生 Operation 开始/终态事件；
- `DomainEventProjector` 不得改写输出来源、可见性、通道或终态；
- PurrA 不包含任何剧本业务语义；
- Runtime、Tool 和 Domain 接口无法构造公开文本；
- 完整文本结束后再拆成单字的实现必须被门禁拒绝；
- `npm run check:agent-refactor` 必须通过。

### 15.2 流式与持久化测试

- 模拟 Provider 使用有真实等待间隔的多个 chunk；首个 SSE 必须在 Provider finish 前到达；
- Provider chunk 内容、顺序和数量与规范公开增量一致；
- 数据库提交先于发布；
- 重复事件由幂等键拒绝；
- 重连从 cursor 精确补齐且无重复；
- 父 Run、子 Run 和并行工具保持稳定 sequence 与来源；
- 实时 reducer 和从零重放 reducer 得到完全相同状态。

### 15.3 生命周期与恢复测试

- 每个工具使用后端权威开始、结束和耗时；
- 模型、校验、压缩和子 Run 同样具有成对 Operation 事件；
- 页面刷新不会重置运行中或已完成计时；
- SSE 断线不取消 Run；
- 显式用户取消会通过 Supervisor 取消 Run 并得到唯一终态；
- `final_public` 失败不会重复工具、Artifact 或 Revision 发布；
- `gated` 候选在校验完成前不产生任何公开文本；
- 部分最终流不进入正式 Conversation 回答；
- 持久化失败不会产生前端可见的幽灵事件。

### 15.4 业务与前端验收

- Planner/候选 JSON、`executionSummary`、`processSummary` 和工具参数不再成为公开文案；
- 没有模型公开文字时，只显示结构化执行状态，不生成替代句；
- 真实过程增量在执行面板中边到边显示；
- 连续操作仍按已确认规则收起，展开后显示具体操作和各自耗时；
- 任务需要对话结论时，结果提交后出现一次真实流式最终回答；不需要时不生成模板回答；
- Writing 与 Screenplay Profile 使用相同的调用、输出、校验和操作生命周期契约，PurrA 中不出现领域分支；
- 任务结束且最终输出完成后，进度胶囊消失；
- 刷新后文字、操作分组、计时和最终回答与实时状态一致。

### 15.5 真实 Provider 发布门禁

必须运行至少一次小范围真实 Provider E2E，并记录时间证据：

1. Provider 首个内容增量时间；
2. `AgentOutputProcessor` 首个持久化事件时间；
3. 前端首个可见增量时间；
4. Provider finish 时间；
5. 工具开始与完成时间；
6. Revision 发布时间；
7. 最终回答首个增量和 commit 时间。

E2E 必须证明过程文字和最终回答在 Provider finish 前已开始显示，且没有结构化字段投影、宿主文案或事后拆字。没有真实凭据而跳过的 E2E 是发布阻断项，不能以模拟测试替代。

## 16. 验收标准

1. 完整 Agent 任务只能从 `AgentCore.submit()` 进入，业务不得组合内部 Runtime/Controller。
2. 所有 Provider 调用均可追溯到唯一 `AgentModelInvocationManager`。
3. 所有用户可见输出均可追溯到唯一 `AgentOutputProcessor` 事件。
4. 公开自然语言均可追溯到对应 Provider 增量，不存在宿主编排文案。
5. 过程文字与最终回答是真实流式，不是完整结果的事后动画；受全文校验的候选在提交前永不公开。
6. 服务端 Run 由 `AgentRunSupervisor` 拥有，任何订阅断开都不会取消执行。
7. 每个模型、工具、校验和子 Run 操作均由 `AgentOperationController` 提供成对状态、时间戳和耗时。
8. 工具、进度、计时和错误保持结构化，并与文字共享稳定顺序。
9. 实时、断线恢复、刷新重放和 Conversation 投影完全一致。
10. 输出失败不会导致副作用或候选生成重复执行。
11. 旧剧本 Agent 输出数据被定向清理，新代码不包含旧协议兼容路径。
12. PurrA 保持通用边界，Writing、Screenplay 和其他业务只通过稳定契约装配。
13. 自动化门禁和真实 Provider E2E 全部通过后，才能宣布重构可发布。

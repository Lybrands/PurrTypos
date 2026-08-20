# PurrA 与剧本对话重构基线

> 基线日期：2026-08-09。本文记录 Phase 0 的可重复验证入口、已知结构债务和代码处置边界。具体数字是准备时快照；自动化测试和债务 ratchet 才是后续权威。

## 1. 当前健康信号

准备阶段开始前已经确认：

- 后端完整 pytest 通过；
- TypeScript typecheck 通过；
- 前端/桌面单元测试 139 项通过；
- `git diff --check` 通过；
- Core 的标准库/自身依赖门禁通过；
- V2 Project、Operation、Revision、Candidate projector 和持久化边界已有确定性测试。

这说明重构可以采用 characterization-first 的提取方式，不需要重写 Core 的稳定状态机和持久化语义。

## 2. 当前结构热点

| 文件 | 准备时规模 | 主要混合职责 |
|---|---:|---|
| `packages/purra/src/purra/runtime.py` | 3341 行 | 模型轮次、工具轮次、恢复、预算、终止与最终回答 |
| `packages/purra/src/purra/engine.py` | 2814 行 | Context、Planner、Admission、inline/durable dispatch |
| `packages/purra/src/purra/contracts.py` | 2083 行 | 多个独立子域的公共类型 |
| `backend/infrastructure/screenplay/artifact_tools.py` | 3827 行 | 多阶段 Artifact 写入与验证 |
| `backend/infrastructure/screenplay/tool_catalog.py` | 1842 行 | 所有剧本能力注册与宿主绑定 |
| `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py` | 2800+ 行 | 整个剧本聚合的全部 SQLite 行为 |
| `src/ScreenplayAgentPage/index.tsx` | 5797 行 | 项目、对话、Run、Operation、LongTask、Revision 与导出 UI |

规模不是单独的失败条件，但这些文件已达到无法安全局部推理的程度，必须通过保持行为的提取逐步缩小。

## 3. 已知依赖债务

### 3.1 剧本 Domain 直接读取数据库

当前只允许以下四条历史依赖继续存在并逐步删除：

- `domains/screenplay/context.py` → `database.crud.screenplay_episode_documents`；
- `domains/screenplay/context.py` → `database.crud.screenplay_head_projection`；
- `domains/screenplay/task_admission.py` → `database.crud.screenplay_episode_documents`；
- `domains/screenplay/task_admission.py` → `database.crud.screenplay_head_projection`。

目标：Application 先读取权威快照，Domain 只消费不可变输入。

### 3.2 通用 Application 被剧本产品逻辑污染

当前债务文件：

- `application/agent_composition.py`；
- `application/agent_run_service.py`；
- `application/request_mapping.py`；
- `application/sse_mapping.py`。

目标：全局 Composition 只选择产品 factory；剧本 Operation 绑定、Projector 和事件映射全部进入 `application/screenplay_agent`。

### 3.3 通用传输协议包含剧本字段

`ChatStreamRequest` 当前仍包含项目、Operation、Stage、Document 与 Draft Scope 字段。`routers/ai.py` 仍提供 screenplay/long-task 产品路由。

目标：通用 `/ai/chat/stream` 只服务通用 Writing Chat；剧本使用 `/api/screenplay/v2/...` 的专有命令与事件接口。

### 3.4 通用前端聊天 reducer 包含剧本状态

当前剧本引用仍存在于：

- `src/Workspace/AiPanel/hooks/chat.types.ts`；
- `chunkHandlers/index.ts`；
- `chunkHandlers/sideEffects.ts`；
- `chunkHandlers/subAgent.ts`；
- `chunkHandlers/terminal.ts`；
- `chunkHandlers/types.ts`。

目标：这些文件的 screenplay 引用全部归零。新剧本 UI 使用自己的持久化 Turn/Snapshot/Event 类型。

以上债务由 `backend/tests/test_agent_refactor_boundaries.py` 使用上限基线保护：删除任意债务不需要修改 allowlist，任何新增出现都会失败。

## 4. 保留、拆分、删除清单

### 保留并强化

- `purra/run_state.py` 与 `run_controller.py` 的状态迁移；
- `SqliteRunRepository` 的 lease、取消线性化、原子 RunCommit 和 Outbox；
- Core Recovery policy/ledger；
- Core Tool schema、授权、审批、幂等和 effect-state；
- Screenplay V2 Project/Operation/Revision/Head/Candidate 聚合；
- `ScreenplayV2ProposalProjector` 的事务投影原则；
- Artifact、Source Receipt、Work Item 和 Revision lineage。

### 保持行为后拆分

- `runtime.py`、`engine.py`、`contracts.py`、`ports.py`；
- `agent_composition.py`；
- `artifact_tools.py`、`tool_catalog.py`；
- `sqlite_screenplay_v2_repository.py`；
- `ScreenplayAgentPage/index.tsx` 中非 AI 的项目、Revision、Working Copy 和导出 UI。

### 新链路通过后删除

- 剧本页面当前 AI stream、accumulator、Run monitor 和多权威 stop 逻辑；
- `longTaskConversationAdapter.ts`；
- 通用 chunk handlers 中的 proposal/revision screenplay 分支；
- `ChatStreamRequest` 的 screenplay 字段；
- `AgentRunService` 的 screenplay Operation 绑定和结算；
- `routers/ai.py` 的 screenplay/long-task conversation 路由；
- 旧 `SqliteScreenplayProposalSource` 与对话 Proposal 全文兼容路径；
- 所有“保存/再次保存 proposal”语义与客户端权威 conversation 写入。

## 5. 可重复验证入口

快速边界检查：

```bash
npm run check:agent-refactor-boundaries
```

V2 剧本确定性验收：

```bash
npm run test:screenplay-acceptance
```

完整重构门禁：

```bash
npm run check:agent-refactor
```

完整门禁包含静态边界、V2 验收、TypeScript、前端单测和后端完整测试。任何 Phase 在该入口未通过时不得继续删除旧实现。

## 6. Phase 0 完成条件

- [x] 层级结果与状态所有权已冻结；
- [x] Core 与剧本对话目标结构已冻结；
- [x] 保留/拆分/删除清单已建立；
- [x] 当前已知跨层依赖已建立只减不增门禁；
- [x] 旧 v1 screenplay acceptance 入口已替换为 V2 验收集；
- [x] 每阶段测试与删除门槛已定义；
- [x] 历史“最终架构”文档已标记为非本轮权威；
- [x] 完整准备验证通过。

Phase 1 可以直接从 Core 的无行为拆分开始，不再需要补充架构决策。

## 7. Phase 1 收口记录

Phase 1 已把四个历史单文件入口迁为兼容包，并完成第一轮真实职责提取：

| 兼容入口 | Phase 0 | Phase 1 收口 | 已迁出的职责 |
|---|---:|---:|---|
| `runtime/orchestrator.py` | 3341 行 | 2899 行 | `model_round`、`tool_round`、`response_finalization` |
| `engine/orchestrator.py` | 2814 行 | 1975 行 | `options`、`context_phase`、`planning_validation`、`durable_execution` |
| `contracts/__init__.py` | 2083 行 | 1947 行 | 基础枚举与五个按族导入边界 |
| `ports/__init__.py` | 775 行 | 53 行 | model、context、planning、tools、persistence、run lifecycle 全部实际迁出 |

这一阶段没有改变公共入口、事件类型、错误码、Run 状态迁移或 Repository 提交顺序。现行 `test_purra_structure.py` 同时保护三件事：旧单文件不能恢复、已经迁走的定义不能回流、兼容 facade 与 orchestrator 只能继续缩小。

Contracts 中相互引用密集的数据类仍由兼容 facade 定义，但 `messages`、`planning`、`context`、`tools`、`runs` 的稳定导入边界已经建立；后续只能沿这些边界继续向外迁移，不能再新增到 facade。Phase 1 的目标是建立可回归的拆分接缝，不在同一次提交中重写这些数据类的构造和校验行为。

## 8. Phase 2 收口记录

Phase 2 已完成 Core/Application 所有权收紧：

| 边界 | Phase 2 结果 | 权威位置 |
|---|---|---|
| Run 与宿主命令关联 | Core 仅保存不透明且不可变的 `RunBinding` | `purra/contracts/host.py`、`ai_agent_runs.binding_*` |
| 持久工作流 | Core 只校验通用有向无环步骤；剧本宿主编译机械流程 | `ExecutionRecipe`、`domains/screenplay/workflow_compiler.py` |
| 领域结果投影 | Repository 调用通用 `DomainEventProjector`，剧本 Projector 在同一事务中产出 Revision 引用 | `purra/ports/projection.py`、`ScreenplayV2ProposalProjector` |
| 剧本权威读取 | Domain 只依赖 `ScreenplayQueryPort`，SQLite 查询位于 Infrastructure | `domains/screenplay/query_port.py`、`infrastructure/screenplay/agent_query.py` |
| 产品请求与执行 | 通用 DTO/映射/Run Service 不再含剧本字段或分支 | `schemas/screenplay_agent_run.py`、`screenplay_agent_request_mapping.py`、`screenplay_agent_run_service.py` |
| 产品事件 | 原生 Conversation 直接投影所需事件；通用 SSE/Run snapshot 不再认识剧本事件 | `screenplay_conversation_service.py` |
| 产品装配 | 剧本 Query、Admission、Projector 与 Long Task 生命周期由产品 Composition 拥有 | `screenplay_agent_composition.py` |

本阶段把以下架构债务固定为零：

- `domains/screenplay` 对 `database` 的直接 import；
- `application/agent_run_service.py` 中的 screenplay 引用；
- `application/request_mapping.py` 中的 screenplay 引用；
- `application/sse_mapping.py` 中的 screenplay 引用；
- `ChatStreamRequest` 的 screenplay 产品字段。

Run 的 `operation_id` 兼容列在 Phase 4 后仍暂时保留，因为 Artifact、Long Task 关联与旧数据库恢复仍可能读取它；新的不可变 `RunBinding` 已是业务命令归属的持久事实，剧本投影可以在兼容列缺失时从 Binding 恢复归属。该物理兼容列的最终清理由 Phase 5 在数据库升级边界内完成，不能与对话运行时切换混为一谈。

Phase 4 已删除曾暂时保留的 `routers/ai.py` 剧本兼容请求与 long-task 路由、通用前端聊天 reducer 剧本分支，以及旧页面的双请求启动方式。这些入口不再属于可接受债务，边界测试要求其计数保持为 0。

## 9. Phase 3 收口记录

Phase 3 已建立一条与旧 Writing Chat 完全分离、但尚未切换生产页面的剧本对话纵向链路：

| 边界 | Phase 3 结果 | 权威位置 |
|---|---|---|
| 对话事实 | User/Assistant 内容、route、status、Run/Operation/Revision 引用和执行 lease 持久化为 Turn | `screenplay_conversation_turns` |
| 增量订阅 | 每个 Turn 事件有单调 cursor 与 Turn 内 sequence；断开订阅不改变执行状态 | `screenplay_conversation_events`、Conversation events API |
| 只读问答 | 服务端结构化分流为 `read_only`，只暴露 READ 工具，Operation 数量保持为零 | `ScreenplayConversationService` |
| 正式任务 | User Turn、Operation、Command Receipt 在同一取消线性化事务提交 | `SqliteScreenplayConversationRepository.begin_turn` |
| 执行恢复 | 业务输入从持久化 Operation 恢复；模型密钥不落库，恢复时重新注入；lease 有独立心跳 | `execute_turn`、`resume_turn` |
| 前端事实源 | cursor 只触发失效，Snapshot 重建完整剧本消息状态 | `conversationClient.ts`、`conversationState.ts` |
| 取消语义 | 仅显式 cancel 请求 Turn/Run/Operation 取消；SSE/HTTP 订阅断开不触发 cancel | Conversation cancel API |

Phase 3 的门禁新增以下约束：

- 五个原生 Conversation 路由缺少任意一个都会失败；
- 新剧本 Conversation 前端引用 Writing stream、chunk reducer 或旧历史解析器会失败；
- Conversation Repository 必须拥有 Turn/Event 持久化，但不得通过 SQL 直接拥有 Operation 表；
- `test:screenplay-acceptance` 现在包含 Conversation 原子提交、零 Operation 问答、游标、租约、恢复与 HTTP 契约测试。

## 10. Phase 4 收口记录

Phase 4 已将生产剧本页面切换到原生 Conversation 纵向链路，并一次性删除旧实现：

- 页面从持久化 Snapshot 重建消息，只通过 Turn submit/cancel/resume 改变执行状态；
- 正式命令不再先创建 Operation 再发聊天请求，Operation 与 User Turn 在服务端原子提交；
- 删除页面旧 SSE reducer、Run monitor、长任务对话 adapter 和历史消息编辑；
- 删除通用 Writing chunk 中的剧本 Proposal/Revision 分支；
- 删除通用 `/ai/chat/stream` 的剧本请求联合类型和旧 long-task conversation routes；
- 删除通用 `ai_conversations.screenplay_revision_ref` 契约；Revision 归属只存在于原生剧本 Turn；
- 删除旧长任务对话拼接器与 Proposal source，不保留运行时 fallback。

新的 Phase 4 ratchet 会在上述文件或标记被恢复、通用 Conversation 再出现 screenplay 字段、通用 AI Router 再出现剧本/long-task 路由时立即失败。

## 11. Phase 5 收口记录

Phase 5 不再把旧剧本 Agent 数据可读作为约束。测试阶段数据库升级采用删除策略，不提供迁移、收养或回填路径：

| 收口边界 | Phase 5 结果 | 唯一权威 |
|---|---|---|
| 产品装配 | 通用 `AgentComposition` 不导入、不识别 screenplay；宿主 factory 注入产品 extension 与 projector | `application/composition_factory.py`、`screenplay_agent_composition.py` |
| Operation 归属 | 创建 Run 时必须一次性写入绑定；之后不可补写、改绑或从 Session 猜测 | `RunBinding(namespace="screenplay.operation", aggregate_id, command_id)` |
| 通用持久化 | Run、Long Task、Artifact 不再保存 `operation_id` | Core lineage、Long Task bindings、Artifact owner lineage |
| Artifact 投影幂等 | 不再把业务 Operation 字段兼作投影标记 | `ai_agent_artifact_projections` |
| 产品请求 | 删除迁移期 `ScreenplayAgentStreamRequest`，不保留 alias | `ScreenplayAgentRunRequest` |
| 旧数据 | 删除旧 Project 所有权闭包、v1 表、对话 Proposal 字段及通用 runtime 业务列 | `init_screenplay_v2_runtime_schema` |
| 架构债务 | `agent_composition.py` 的 screenplay 上限从 24 清为 0 | `test_agent_refactor_boundaries.py` |

保留的 `purra.engine` 与 `purra.runtime` 是 Core 实现包，不是宿主兼容面；完整 Run 统一从 `purra.api` 进入，宿主需要的契约和适配器端口从对应公共模块导入。这些包不是旧剧本数据兼容层，因此不在删除范围。Provider 协议兼容、Fountain 文本兼容等也不参与剧本状态归属，不能与旧数据迁移混为一谈。

Phase 5 完整门禁与故障注入矩阵记录在 [`purra-screenplay-phase5-verification.md`](purra-screenplay-phase5-verification.md)。

## 12. native Conversation 二次收口记录

第一版 native Conversation 已被进一步收紧：剧本 Run 输入不再继承 Writing Chat DTO；咨询和正式任务分别使用只读/提案工具权限与独立宿主规则；Session 不再允许渲染进程维护的临时发送队列。Turn 增加 execution attempt，重启恢复会清除旧 Run 引用和半截 Assistant 内容后再创建新的 Core Run。只有 completed Turn 进入后续模型历史。

持久化事件不再保存通用 SSE chunk 或 Proposal 全文，只记录 Turn 已变化与 Revision 引用。前端现在真正订阅 cursor SSE，并在收到通知后重取权威 Snapshot；低频轮询仅用于订阅故障恢复。Conversation HTTP 已从项目聚合路由拆到 `routers/screenplay_conversations.py`。

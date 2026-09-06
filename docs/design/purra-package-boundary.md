# PurrA 独立包边界

## 定位

PurrA 是可独立发布、仅依赖 Python 标准库的通用 Agent 框架。PurrTypos 是它的一个宿主，不是框架内部的特殊分支。

框架负责完整 Agent Run 的通用不变量：规划、上下文与输出预算、模型结束
原因、恢复额度、工具授权、Artifact、Work Item、检查点、取消、终态和标准事件。
产品负责领域上下文、业务任务编译、工具实现、持久化适配、Provider 适配和传输映射。

## 公开面

PurrTypos 各 Agent 的材料准备遵循
[业务公共编排规范](agent-read-reuse-contract.md)。读取缓存、材料去重、预装和
业务证据准入由宿主实现，使用既有 PurrA 上下文与证据接口。该项接入待实现，
不涉及框架源码、公开契约或依赖版本变更。

- `purra.api`：完整 Agent Run 的唯一执行入口；
- `purra.model_execution`：宿主模型型 Hook 的受管无工具调用入口；
- `purra.model_invocation`：Composition Root 装配受管 invocation 的公开契约；
- `purra.operations`、`purra.output`：宿主持久化适配和公开事件投影契约；
- `purra.contracts`：宿主可以构造的稳定值对象；
- `purra.ports`：宿主与基础设施实现的端口；
- `purra.retrieval`：宿主检索接口、结构化命中与标准只读工具；
- `purra.tools`、`artifacts`、`work_items`：需要宿主注册或实现的通用契约。

`purra.engine`、`runtime` 和模型轮次实现不是应用入口。产品代码不得自行
复制其中的预算、截断、修复或生命周期逻辑。

## 依赖方向

```text
Writing / Screenplay Domain
            ↓
PurrTypos Application Host
            ↓
purra.api + public contracts
            ↓
Agent Framework Runtime
            ↓ ports
Provider / SQLite / transport adapters
```

Adapter 在 Composition Root 注册给框架。领域和普通应用用例拿不到原始 Provider
Gateway，不能自行建立第二条 Agent 模型执行链路。

## 受管模型执行边界

普通 Application、Domain、Router 和 Service 不得直接构造 `ModelInvocation`
或取得原始 `ProviderModelGateway`。明确的装配白名单为 Composition Root，以及
私有后台生成的 `application/model_request_service.py`；后者要求任务 owner 和结果容量，
使用已有预算契约，不创建第二条 Agent 执行链路。静态架构测试禁止其余业务旁路。
三类 Agent 和后台调用的公共解析、SDK 参数核验及协议职责遵循
[模型请求公共入口与适配规范](2026-09-06-model-request-boundary-refactor.md)。

记忆重排、响应裁判、会话压缩和剧本结构化生成都只向
`ManagedModelExecutor` 提交 `ModelRequest`、`OutputBudgetPolicy`、工作单元数量和
推理模式。业务结果容量目标不充当模型总生成上限。PurrA 负责解析实际生成额度、构造供应商调用、记录调用参数、限制
兼容降级次数，并统一解释流式与非流式结束原因。

供应商没有给出结束原因，或者明确以 `length`、过滤或未知原因结束时，结果一律
失败关闭。尤其是已经输出部分正文后发生截断时，不得再次执行同一调用。业务层的
JSON 修复只允许处理“供应商已正常结束，但完整输出不符合业务 Schema”的结果，
不能修复或重放不完整输出。

剧本旧的 `screenplay_model_run.py` 已删除。替代它的
`screenplay_structured_call.py` 只拥有剧本提示、JSON 校验、Run/诊断持久化和可见
进度投影；它必须由唯一 Composition Root 注入受管执行器，无法自行创建 Provider
调用链。

## 打包

- 开发、CI 和分发统一通过 `backend/requirements-purra.txt` 安装 PyPI 0.5.0
  的 `purra`、`purra-openai`、`purra-anthropic`、`purra-mem0[managed]`；
- PyInstaller 分析当前 Python 环境中安装的 PurrA，无需注入源码路径；
- Electron / Web 分发从同一依赖清单安装实际包文件到后端资源；
- 框架包本身不依赖 FastAPI、Pydantic、模型 SDK 或数据库驱动。

运行时不依赖相邻源码目录或仓库内历史 wheel。已有同版本本地安装需强制
重装这四个发布包，并重启后端；安装步骤见 README。

## 当前生成预算契约（0.5.0）

`ModelRequest.max_generation_tokens` / `maxGenerationTokens` 仅表示用户显式
生成上限；模型能力快照提供 `max_generation_tokens` 能力上限。工作流的
`result_capacity_target_tokens` / `resultCapacityTargetTokens` 是结果容量目标，
用于规划和上下文预留，不是 Provider 总生成限制。三者不再共用一个字段。

PurrA 生成 `InvocationOutputBudget` v2，记录用户上限、模型上限、结果目标和
实际限制来源。实际上限受模型能力、用户显式上限及可用上下文共同约束。
Run 和 Long Task 累计用量使用 `max_run_generation_tokens` /
`maxRunGenerationTokens`，独立于单次调用；`None` 不表示无限单次输出。

Provider SDK 适配器回报最终请求参数中的 `applied_generation_limit`。缺失、
与请求不符、usage 超限或 `length` 结束均失败，不提交成功 finish。流式/非流式
共用该检查，已发生的用量和失败诊断仍保留。公开累计用量是 `generationTokens`；
Provider 未报告 reasoning 用量时保留未知，不按零计算。

Provider 原生 `max_tokens` / `max_completion_tokens` 保持其原生含义。思考模式、
强度和显式思考预算按用户配置传递，业务 Part 不再注入低强度或较小总生成上限。
受管 Hook 也必须符合持久化 Root 的模型与用户配置身份。

技术失败不作为可恢复业务暂停；合法的用户暂停和需要用户重新确定业务范围的暂停
仍有独立语义。旧字段不提供别名，不迁移历史运行状态，不删除历史数据；旧 Run /
Long Task 不支持跨此契约续跑，需要发起新任务。

## 0.5.0 Retriever 与 RAG 接入

`WritingMemoryRetriever` 和 `WritingMethodRetriever` 使用现有 SQLite 数据源，
通过 `RetrieverTool` 注册为原来的 `searchMemories`、`searchWritingMethods`，
沿用 Core 工具授权、上下文契约、结果预算和完整证据检查点，不另建工具执行链路。

模型只能提交 `query`。检索器通过 `run_id` 读取持久化的宿主绑定，校验 Writing
身份、当前作品、运行状态和方法推荐意图；模型参数、可变 `ExecutionState.domain`
或直接调用时传入的 `scope` 均不能扩大权限。无会话的作品请求也会建立
`writing.context` 绑定。旧 Run 缺少作品绑定时拒绝检索，需要发起新 Run。

记忆工具最多返回 12 条 active Memory Items；待审、归档和已替代记录只在原有
管理接口处理。方法工具最多返回 8 条当前已发布的目录候选，只在显式推荐请求中
开放，不绑定方法，也不把目录候选当成已绑定指令。每次查询读取当前数据库，返回
来源、条目 ID、证据 ID，方法另带真实版本。Memory Items 没有版本计数，不伪造
版本；历史工具证据是当时内容的快照，不代表该记忆现在仍有效。

两种工具的结果都标记为不可信资料，序列化后超过 16,000 字符整次失败，不截断
条目后伪装成完整证据。成功的记忆检索保留原有重新规划行为。

自动上下文 RAG 继续使用 `RepositoryWritingContextSource` 和
`UnifiedMemoryRetriever`：Story Memory 优先、只召回已确认且证据有效的状态，
再进行长期记忆召回、受管模型重排、去重与冲突处理、预算分配和实际注入回执。
写作方法与续写 canon 回执使用 Core 的上下文输入格式，避免来源元数据在
检查点中重复嵌套。原文按授权章节读取的逻辑保持不变。

检索源只实现 `build_memory(..., query=..., task=..., signal=...)`，返回
`MemoryContextPack`；关联资料只返回 `AssociatedContextResult`。不再探测旧方法、
改写用户消息来传查询，或把字符串/旧记忆块包装成当前结果。普通工具与 Retriever
共用一条重规划规则；Retriever 不经过旧 handler 返回值转换。预算读取只接受当前
字段，未知字段直接拒绝，不提供别名、迁移或旧版回退。

历史说明：最初 Retriever 接入阶段没有安装或启用独立的 `purra-mem0`；
当前已通过发布包接入该组件。安装 Core 本身不会自动启用 Mem0 的
Embedding、向量存储、候选提取或生命周期管理。该组件的实际接入还需要明确
Provider/存储配置、现有数据分工、来源修改与撤回事件、检查点证据重验和费用边界；
不能把当前 SQLite 检索适配描述为已完成 Mem0 RAG 迁移。

## 0.5.0 Planner 协议流接入（2026-09-01）

Planner 是 Composition 提供给 Agent 的能力，不是 Run 的隐式开关。每个
`AgentRunRequest` 都使用 `PlanningMode`：普通写作请求省略 `planning_mode` 并使用
Core 默认的 `AUTO`。第一次普通模型调用可以直接回答、调用普通业务工具，或调用
PurrA 私有 `request_plan` 提升到受管 Planner；宿主不增加复杂度分类调用。
剧本根 Run 因固定 Durable 准入契约选择 `PLANNED`；小说来源分析根 Run 使用
`AUTO`，由模型直接回答、使用普通工具或通过私有 `request_plan` 请求受管规划。
剧本执行单元、结构化/最终响应、小说分析执行单元和追问因明确禁止重复规划选择
`REACTIVE`。工具存在、Agent 模式和 PlanningPolicy 都不参与模式判断。显式
Planned 但 Composition 未配置 Planner 时保留 Core 的 `planning_unavailable`
失败关闭语义。

`request_plan` 不注册到业务 ToolCatalog，不进入业务 Tool Executor、公开 canonical
事件、SSE 或 UI。只有产品契约明确要求副作用前必须通过计划准入的工具才使用
`planning_requirement=ToolPlanningRequirement.REQUIRED`；现有 Writing 工具继续由
PROPOSE、审批、作用域和幂等边界约束，不因风险等级被批量升级为 required。

PlanningPolicy 只在已经选择 Planned 后提供步骤、工具、fallback 和 validator 等
约束。宿主不保留 `ReactivePlanningPolicy`、`ToolPlanningPolicy`、`should_plan`
或历史参数别名，也不通过关键词或额外模型调用判断复杂度。

参考 Planner 使用 `purra.planning-stream/v1` 的严格 JSONL 流，并通过 Core 当前
planning scope 下的受管 invocation 执行。Provider 可以在最终私有 plan 前输出
有界的公开 `planning.progress`；宿主不解析其他文本格式，也不提供旧单 JSON 或
非流式 Planner 回退。

Core 独占 planning operation 生命周期。宿主已经删除 `observe_planner` 和
`agent_planning.py`，只在 Composition Root 向 Core 提供 operation controller。
规划阶段使用 `kind=planning`，revision 区分初始规划与调整；内部 model operation
只保留为诊断子操作，UI 不重复展示。

SQLite 输出流持久化 protocol、planning Run/operation/revision/attempt，并把这些
字段列入不可变 stream identity。公开进度只有在 Run 仍运行、planning operation
仍活跃、scope 一致，且 text/recordIndex/UTF-8 span 与同 invocation 已持久化的
Provider bytes 完全匹配时才能提交。规划流整体禁止提升为公开 commentary。

前端 canonical reducer 只接收 Provider、public commentary、当前 schema 的 typed
planning progress；按真实 `eventId` 去重并按 `sequence` 续读。时间线区分确定性
阶段、模型撰写的规划意图、已校验的计划摘要和执行结果，不把私有 plan bytes 混入
普通 commentary，也不把“将要执行”显示为“已经执行”。repair 的旧 attempt 保持
原 invocation/attempt 关联；终态后的迟到输出只推进游标，不产生新 UI 项。
`run.todos_updated` 继续使用 PurrA 的有界、已校验公开计划投影。

规划诊断沿用 PurrA 的 private 事件，分别记录首个 transport activity、首个公开
说明、完整计划收到、校验及完成时间。Provider Adapter 没有 HTTP/SDK 证据时，
request sent、first byte 和 HTTP attempt 保持空值；宿主不从阶段时间推测模型
thinking，也不把 prompt、计划正文、reasoning、密钥或 headers 提升到公共流。

### Auto 直接回答与提升边界

Auto 的首次调用同时拿到普通业务工具和私有 `request_plan` 时，Provider 输出先按
private stream 持久化。若本轮没有工具调用并直接形成答案，Core 从同一已提交 stream
原样投影 public final canonical event，不增加 tool-free 模型调用，也不重新生成文案。
final 投影拒绝包含 Provider tool call 的 stream；`request_plan` 控制、参数、reasoning
和原始 Provider stream 始终保持 private。模型在请求规划时同时写出的用户说明会从
同一已提交 stream 原样投影为 public commentary，并在 Planner operation 之前提交；
没有模型正文时不补造说明。

动态 delegation 在首次 runtime 准备阶段绑定根 Run，并跨 `request_plan` 提升继续
使用同一绑定；planned runtime 不再重复绑定，Run 结束时仍只释放一次。
`test_writing_auto_direct_answer_uses_one_normal_model_call`、
`test_writing_auto_request_plan_uses_shared_core_and_public_progress` 和 SQLite final 投影
测试覆盖真实 Writing Composition、持久化、公开事件及幂等回放。PurrTypos 没有增加
兼容 wrapper、双轨执行、业务复杂度判断或强制 Writing 全部进入 Planned。

### 普通 Agent 的公开阶段进展

普通模型流使用 `purra.agent-progress/v1` 的 `agent.progress`，与 Planner 的
`planning.progress` 分开。PurrA Core 只接受 Provider Adapter 明确提供的
`ModelStreamChunk.progress_delta`：先持久化 private Provider 来源，再发布 public
projection。PurrTypos 的 SQLite 仓库逐字核对 Run、invocation、stream 和 source chunk，
SSE、历史回放和前端时间线消费同一条 canonical event。当前 PurrTypos 的 Provider
profiles 全部声明 `public_progress=unavailable`，因此不会从 reasoning、普通正文、固定
步骤、定时器或额外模型调用伪造阶段文案；只有以后某个 Provider Adapter 具备独立原生
进展通道并明确声明支持后，纯模型阶段才会出现这类事件。

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

初始计划仍是可修订路线，但正常成功执行默认沿用已经编译和授权的计划。Runtime 只会在可恢复失败、协议或授权漂移之后，或者宿主工具的权威结果因选择了分支而显式返回 `ToolPlanningDisposition.REPLAN` 时调用 `DynamicTaskPlanner`。普通的 `PROGRESSED` 与 `COMPLETED` 批次不再额外消耗一次 Planner 模型调用。

工具调用成功不再自动等于 Planner 步骤完成。工具适配器用 `ToolStepDisposition.CONTINUE` 表明“本批已可靠提交，但当前步骤仍需继续”，Core 将其聚合为 `ToolBatchOutcome.PROGRESSED`，保留当前工具授权且不把该步骤写入完成历史；只有适配器返回 `COMPLETE` 才会激活依赖步骤。该语义用于所有有界批次工具，避免一次 append 成功后提前进入 finalize。

Runtime 的轮次约束同样区分“单调的有效分批进展”和“停滞执行”。每个 `PROGRESSED` 轮次可以从 `RuntimeLimits.max_progress_rounds` 解锁一个独立、有上限的进展轮次；普通成功、重试、失败和格式错误都不会获得该额度。这样大型 Artifact 不需要依赖某个领域专属的固定轮次数，同时 Core 仍保留明确的总上限。

已经完成、阻塞或失败的步骤属于不可改写的执行历史；Controller 会把每次修订后的未来计划和对应的 `run.todos_updated` 事件原子持久化。Runtime 会为请求范围内的候选工具保留上下文预算，但每一轮只暴露最新当前步骤授权的工具。因此，重新规划不能绕过工具策略、人工审批、作用域校验或幂等边界。授权拒绝和人工审批拒绝不属于可恢复的工具执行失败。

`ToolPlanningDisposition` 与 `ToolStepDisposition` 相互独立：前者决定未来计划是否已经失效，后者只决定当前步骤是否仍需继续。两者都是宿主工具结果字段，模型不能通过工具参数自行要求重新规划。

## 任务准入与持久化长任务

Planner 仍是唯一的模型语义判断入口。它只把用户目标编译成 `TaskSpec`，不估算调用成本，也不直接决定是否开启长任务。Core 在计划通过约束编译和权限校验后、安装执行计划前调用 `TaskAdmissionEvaluator`；应用与领域实现负责把 `TaskSpec` 对照权威业务状态解析为精确范围，并返回 `inline`、`durable`、`clarify` 或 `reject`。这样无需再增加一次意图模型请求，也不会让 Core 理解“场景、章节、数据集”等产品概念。

`long_tasks` 定义通用的持久化任务、依赖单元、租约、检查点、重试、暂停、恢复和取消契约。Core 的 Coordinator 只认任务 DAG 和执行状态；业务层决定如何拆分、每批输入、领域完整性校验及最终合并。Application 创建 Work Item 和 Long Task，把每个单元作为独立 Agent Run 执行，并在全部单元完成后由宿主确定性汇总。暂停会释放当前单元租约，恢复从最近完成的检查点继续；失败任务只有经过显式恢复才会获得新的重试额度。

当前剧本领域只把多场正文创作准入持久长任务。Planner 在 `TaskSpec.target.executionUnits` 中动态给出完整执行图，业务层只校验场景覆盖、顺序、依赖关系和最终单元，不再按固定场数硬编码拆批；每个生成单元携带压缩连续性状态，最终单元确定性校验执行历史并组装一个完整剧本提案。列表接口只暴露进度摘要；批次正文和最终提案仅通过单任务详情读取，避免大任务轮询时反复传输全部内容。

原作分析、创作简报、结构和场景表等阶段提案不再进入“持久任务单元内再启动完整 Agent”的应用旁路。它们始终留在发起请求的主 Run，通过普通 Agent 的动态 Planner、工具事件、流式输出和提案效果链执行；Artifact 只负责有界写入和完整性，不承担第二套编排或整任务重试。

## 上下文编排与压缩

Agent Core 只负责上下文预算、压缩时机、Hook 调用和技术校验。Core 根据模型窗口、输出与运行时预留、工具 Schema 和 Domain 上下文声明生成统一预算，并在规划边界以及每次模型调用前重新计算压力。默认达到可用输入预算的 85% 时调用应用注入的 `ContextCompressionHook`；Hook 获得完整源视图和硬 Token 限制，所有语义选择均由应用负责。

分阶段上下文 Provider 还可以实现 `TaskContextDemandProvider`。规划前的普通需求只为轻量候选清单分配空间；Core 编译出 `TaskSpec` 后，再解析仅属于该任务的附加需求，并在正式检索正文前重新生成最终预算。因此，无关的新问题不会因为存在未完成大产物就被预占一块上下文，领域也不需要写死局部上下文窗口。

`context_orchestration` 不再拥有摘要 Schema、摘要持久化、保留回合数、语义目标或摘要失败降级。没有配置 Hook 时，Core 才启用基础结构裁剪：以最近 20 条消息为起点，再按实际 Token 预算收缩，并保持完整工具调用协议；配置 Hook 后这套默认裁剪不会参与。Core 只校验特权指令、当前用户请求、Tool Call/Tool Result 连续性和最终 Token。语义摘要、摘要状态、业务保留规则及降级链全部由 Application 组装，Domain 提供业务语义规则。

持久化和前端展示始终以原始会话回合作为唯一事实源；压缩结果只是当前模型请求的运行时投影视图，不会反写原始会话。当前 Application 策略只额外保存带覆盖范围和摘要指纹的派生摘要，通过 Application 自己的 Repository 与 Summarizer 端口注入实现，并限制单次语义压缩的推进轮数。若依赖故障导致结果仍超过硬预算，也由 Application 明确选择最近消息应急投影，不会让 Core 在 Hook 后再偷偷追加默认裁剪。

## 模型输出与工具数据边界

`model_protocol` 统一解释不同供应商的结束原因。只要供应商声明达到长度上限，本轮输出就属于不完整结果：残缺的文本不会被当作最终回答，残缺的工具调用不会执行，也不会写入后续模型历史。Runtime 只允许一次无副作用的干净重试；再次截断时保留 `tool_call_truncated` 或 `model_output_truncated` 根因和安全诊断。

每个已审计工具通过 `ToolDataContract` 声明模型生成字段、宿主绑定字段和宿主派生字段。宿主字段不得出现在模型可见 JSON Schema 中。长内容工具应优先使用 `delta`、`batch` 或 `resource_reference`，模型只生成新的语义增量，标识、版本、谱系、累计正文和完成状态由宿主绑定或计算。

`ToolSchema.name` 始终是不可翻译的协议标识；面向用户的多语言名称独立保存在宿主管理的 `display_names` 中，语言键使用 `zh-CN`、`en-US` 等标签。Core 根据本次请求语言把展示名用于 Planner 标题和模型叙述提示，但发送给供应商的函数调用仍只有真实名称、描述与参数。运行事件会携带完整展示名映射，前端可以确定性选择语言，不需要让模型改写函数名。

`ToolExecutionLimits.max_argument_chars` 现在只表示可配置的原始 JSON 传输安全包络，不是上下文分配，也不是领域数据预算。JSON 解码及受限的结构恢复完成后，Core 会再次强制执行注册 Schema 的必填字段、类型、枚举、文本长度、数组数量、数值范围和额外字段规则。因此，空白和 Unicode 转义不再消耗一个无关的 32K 工作流预算；领域仍通过版本化 Schema 决定有用语义数据的大小。校验失败会返回工具名、失败阶段、Schema 路径、实际测量值和允许上限，但不会回显被拒绝的正文。

## 受控恢复策略

`recovery` 是 Runtime 内所有自动恢复的统一决策层。供应商兼容降级、流中断、截断、缺失或越权工具调用、空回答、回答修复及工具输入修正不再各自维护布尔开关；每次候选动作都必须经过同一个 Run 级恢复账本，检查取消状态、剩余模型轮次、按根因配置的尝试额度、正文是否已经对用户可见，以及工具副作用是否可能已经开始。

领域适配器可以在 Composition Root 注入 `RecoveryPolicy`，但只有 Core 可以消费额度和批准恢复。JSON、Schema 或工具调用包络在整批预检阶段失败时，Core 可以要求模型修正一次；工具处理器尚未启动这一事实由 `ToolBatchResult.effect_state` 显式证明。写工具处理器一旦启动而提交状态未知，Core 会拒绝自动重放或失败后的自动重规划。所有允许和拒绝决定都通过现有 Run Trace 持久化，`evaluation.recovery` 只投影根因、动作、额度和安全拒绝码，不复制模型正文或工具参数。

## 稳定性评估

`evaluation.stability` 从持久化 Core 事件中生成不包含业务正文的可靠性指标。它关联工具发起、完成和结果事件，明确区分显式失败与“已经发起但始终没有终态结果”的未收口调用；同一份报告还聚合协议错误码、模型中断与重试、上下文溢出、压缩降级和压缩失败。`evaluation.recovery` 另外解释每次恢复为何被允许或拒绝。基础设施层可以补充 Artifact 数量与完成度，但不得把提示词、工具参数或生成正文复制进稳定性报告。

`StabilityTrendPolicy` 对一个有界、按新到旧排列的 Run 窗口应用由调用方注入的预警和失败阈值。比例指标达到可配置的最小样本量后才触发告警，但连续失败属于绝对信号，不会被样本门槛隐藏。SQLite 适配器只投影 Trace 计数、调用 ID、工具名和错误码；历史提示词、参数及工具结果正文不会进入趋势评估器。

用户主动取消的 Run 不进入趋势窗口，避免一次正常中止被误判为工具未收口回归，也不会稀释真实失败率。

`failure_classification` 把明确且不包含正文的运行证据转换为稳定根因码，同时不会给证据不足的事件强行编造原因。工具协议错误、工具生命周期未收口、上下文故障、规划契约错误、工具处理器失败和模型中断分别保留独立分类及检查建议；如果 Run 已失败但没有具体证据，只会标记为低置信度的可观测性缺口。

`stability_gate` 使用最新 Run 窗口与紧邻的上一窗口比较，识别失败率上升、连续失败扩大及新出现的工具错误码。Writing 领域维护的历史事故目录会在现有运行时回归框架中锁定预期根因码；本地 `check:agent-stability` 命令会在任一事故不再被正确识别时失败。

## 可恢复 Artifact 生命周期

`artifacts` 提供与领域无关的 `open → finalized/aborted` 状态机。大结果可以按有序批次提交；每批携带幂等键、内容摘要、期望 revision、sequence 和 coverage key。Core 在最终确认前校验数量、连续序号、重复/缺失覆盖项，并通过 `ArtifactValidator` 把领域正确性留给外层。具体数据库事务由 `ArtifactRepository` 实现；SQLite 适配器保证批次写入、CAS revision 与返回 receipt 处于同一个可判定提交边界。

只有同时声明为 `batch`、`PROPOSE`、取消线性化并由宿主管理持久化的同名 Artifact 工具，才允许在一个模型轮次内提交多个调用。Core 仍会在任何写入前预检整批 JSON、调用数量、授权和 Schema；普通写工具继续禁止多调用。这让大结果获得吞吐量，同时不放宽一般副作用工具的安全边界。模型轮次上限由领域适配器通过 `RuntimeLimits` 注入，Core 默认值不再承担具体产品的批次数量假设。

当前剧本适配器已经把创作简报、结构提案、场景表和剧本审阅改为“开始、分批追加、最终提交”。创作简报 Artifact 会冻结已接受原作分析版本、证据清单、阅读局限、项目形态和版本谱系；模型只提交一个简报正文条目及有界的改编决策批次。结构 Artifact 冻结创作简报版本、结构类型、单元数量及改编决策清单。审阅 Artifact 则用摘要条目、问题批次和复审核验批次承载报告；复审时宿主冻结上一轮问题顺序，并补齐问题 ID、验收标准和解决记录，模型只提交核验状态与新证据。完整剧本修订拆成受影响场景批次与问题回写批次；最终正文、执行追踪、版本谱系和 Artifact 引用均由宿主组装。

原作范围分析也使用同一生命周期：模型先声明总述和各类分析条目数量，再分批提交人物、事件、冲突、改编资产、风险、问题与证据。锁定章节总数、完整读取章节、抽样章节及未读取局限由宿主根据当前 Run 的持久化来源凭证生成；证据只能引用该凭证清单内的来源。模型不再返回章节覆盖 ID 数组。

## Work Item 与 Artifact Scope

`work_items` 把可跨多次执行的稳定任务身份与单个 Run 分开。Work Item 只维护小型内容生命周期 `open → completed/canceled`；Run 通过不可变关系记录自己是创建者、续写者还是只读引用者。Run 关系是独立审计记录，不推进 Work Item 内容 revision，避免一次只读引用使正在执行的任务快照失效。创建者关系不可在事后替换；持久化实现必须在创建 Work Item 时原子写入。

Artifact 连续性契约区分 `run` 与 `work_item` 两种作用域。Run Scope Artifact 只能由创建它的 Run 使用；Work Item Scope Artifact 可以被关联 Run 读取，但任何写入都必须先由 Core 校验 Artifact revision、Artifact 状态、Work Item 身份和 Work Item 状态，再取得原子、独占、可过期的写入 claim。这里处理的是确定性的执行安全；自然语言意图和领域兼容性仍由应用层及领域层负责。

SQLite 已持久化 Work Item、不可变 Run 关系、Artifact Scope 与独占 claim。旧 Artifact 在迁移时保持 Run Scope，并把原 `run_id` 回填为创建来源；Run Scope 与 Work Item Scope 使用互不混淆的唯一索引和查询入口。Work Item Scope Artifact 创建时会校验 Work Item 所有权和创建 Run 的写关系；claim 通过数据库事务原子竞争，只有 `created/continuation` Run 可以取得写权，过期后才允许接管。

剧本应用层现在只发现同一 Session、当前阶段兼容且仍处于 open 状态的候选；复用现有主 Planner 语义选择 `continue`、`reference` 或 `ignore`，Core 会拒绝任何不在可信候选清单内的 ID。只有被选择的候选才会在规划后声明上下文需求。领域层随后重新校验精确作用域、持久化当前 Run 关系；续写会取得独占写 claim，并注入只包含完整元数据字段和完整批次的有界恢复投影；引用则明确为只读。Run 结束或连接关闭时主动释放它持有的全部 claim，TTL 继续作为进程崩溃后的兜底。

剧本 begin/append/finalize 工具链已经迁移到 Work Item Scope。begin 会在单一事务中创建 Work Item、不可变创建者关系、Artifact 和初始写 claim；续写按当前 Run 关系解析原 Artifact。每次 append 都在批次提交的同一 SQLite 事务内重新校验不透明 claim，并同步推进 Artifact 与 claim revision、续租；finalize 则原子完成 Artifact、Work Item 并删除 claim。历史 Run Scope Artifact 继续走原路径读写，作为迁移兼容。

Artifact 的长期维护同样按职责分层：Core 只定义与存储无关的策略和报告契约，应用层负责启动扫描与周期调度，SQLite 在单一取消线性化事务中执行清理。过期 claim、持有 Run 已非 running 的 claim，以及 Artifact、Work Item、revision 或写关系已失效的 claim 都属于可安全回收的执行租约。任何 open 持久状态都不会被 GC；终态内容默认无限保留，只有显式配置 `PURRTYPOS_AGENT_ARTIFACT_TERMINAL_RETENTION_SECONDS` 才会开启保留期清理，即使开启，仍有 open Artifact 或仍关联 running Run 的 Work Item 也会保留。结构矛盾只做无正文计数和告警，不删除诊断证据。孤儿 Run 恢复会在 Run 终态事务内同时释放其 writer claim。诊断接口提供有界且不含正文的 Work Item、Artifact 状态与 claim 分类快照，不暴露 claim token 或生成内容；宿主的手动维护入口固定为租约清理，无法开启终态内容保留期 GC。

## 可复用持久化端口

- `RunRepository`：保证 Run 生命周期状态与 Outbox 事件的原子持久化。
- `ExecutionLeaseStore`：管理执行所有权、心跳续租和持久化取消请求。
- `DelegationRepository`：管理父子任务队列、并发认领和结果回传。
- `CheckpointStore`：提供持久化、基于游标的 Run 快照。
- `ApprovalGateway`：管理只能决议一次的人工审批。
- `ToolIdempotencyGateway`：保证具有副作用的工具调用可以安全重放。
- `ContextCompressionHook`：由 Application 实现压缩策略；Core 只负责调用并校验返回结果。
- `ArtifactRepository`：保存可恢复的大结果批次，并保证 revision、顺序和幂等回执原子提交。
- `ArtifactValidator`：由领域注入批次和最终完整性规则，不把业务结构写进 Core。
- `WorkItemRepository`：保存稳定任务身份、内容生命周期和不可变 Run 关系。
- `ArtifactClaimRepository`：为 Work Item Scope Artifact 提供独占、可过期的写入权。
- `ArtifactMaintenanceRepository`：原子回收无效租约、输出不含正文的一致性报告，并执行显式配置的终态保留策略。

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

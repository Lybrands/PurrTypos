# 小说来源分析：状态驱动的可靠性治理计划

> 日期：2026-09-11。状态：PurrTypos 的基础暂停/保留成果语义及 PurrA 候选产物接入已通过确定性验证；Provider 与 Electron 的真实验收尚未完成。全项目后端、前端/Electron 单元与新空数据目录启动烟测的证据见变更记录；这不替代真实 Provider 或 Electron 窗口验收。
>
> 本文是后续工作的唯一任务台账。实现、验证或发现新风险时更新对应任务、决策记录和变更记录；不得以“代码已改”替代验收证据。

## 1. 目标与边界

小说来源分析是长时间、多单元、依赖外部模型 Provider 的工作流。网络抖动、限流、Provider 过载、进程重启和运行预算耗尽都可能发生；目标不是声称消除所有故障，而是将故障变成可解释、可恢复、不会丢失已完成成果的状态转换。

成功标准：

- 暂时性故障不把整个工作流误记为永久失败，不取消未开始的单元，不丢弃已完成的 checkpoint。
- 本次对话可以正常给出阶段性说明；工作流是否仍可继续由独立、可查询的领域状态表达。
- 恢复具有持久化的重试时间、预算和原因；不会因为网络或 Provider 已知故障立即重复请求。
- 系统能量化每种失败的频率、恢复时长和最终成功率，并据此调节 Provider 负载。

非目标：

- 不把 Provider 超时简单无限延长，也不承诺任意网络状况下永不暂停。
- 不把所有业务域强制改为同一领域状态机；本计划先以小说来源分析为纵向样本，复用的部分再下沉到 PurrA。
- 不把前端断流、用户取消和服务端工作流失败混为同一种错误。

## 2. 当前已实施的基础

以下行为已经在 PurrTypos 来源分析链路中实现，且仅具有确定性测试证据。它们依赖现有 PurrA 候选包提供的基础接口；不得据此推断 PurrTypos 已使用 PurrA 工作区随后产生的所有源码改动。

1. 可重试的 Provider 故障在单元重试耗尽后产生 `PAUSE_RECOVERABLE`，而非直接成为永久失败。
2. 来源分析将流中断、限流、服务不可用及运行活性/进度/调用截止视为系统性可恢复故障；任务进入 `PAUSED`，受影响单元进入 `BLOCKED`，其他未完成单元保留为可调度状态。
3. 已完成单元及其输出引用被保留。恢复时只给阻塞单元追加一次受控尝试，不重跑成功单元。
4. 当任务暂停或失败时，根对话以阶段性结果正常收束；若没有任何成果，也明确说明“已安全暂停”，不会伪造分析结论。
5. ZAI 流式读取时限从单一 60 秒改为连接、读取、写入和连接池分离的时限；读取上限当前为 125 秒，与运行时活性边界对齐。这是临时对齐，不是最终重试策略。

当前已知缺口：已具备按 Provider/模型/端点隔离的最小健康状态、半开探测，以及按 Provider/端点共享的容量策略（默认 2，用户可显式设为 1–16）。探测成功后同端点会先以 1 并发斜坡 30 秒再恢复策略容量；30 秒与单探测租约对齐，是保守的临时策略而非网络超时。当前仍缺少基于真实样本的 Provider 默认建议、告警阈值和生产级验证；真实 Provider/Electron 验收也尚未完成。自动恢复目前只覆盖绑定到本机已保存且身份未变的模型配置；配置缺失、变更或凭据不一致时必须保持暂停，等待用户明确恢复。

## 3. 目标状态模型

### 3.1 三个独立的状态维度

不要用一个 `done` 或 `failed` 同时表达下面三件事：

| 维度 | 责任 | 推荐状态 |
| --- | --- | --- |
| 对话回合 | 本轮回复是否已经收束、是否可以安全展示 | `STREAMING`、`FINALIZED` |
| 工作流 | 小说分析是否仍在执行、等待恢复或已经不可继续 | `QUEUED`、`RUNNING`、`RETRY_WAIT`、`PAUSED_SYSTEM`、`PAUSED_USER`、`PAUSED_BUDGET`、`COMPLETED`、`PARTIAL`、`FAILED_PERMANENT`、`CANCELED` |
| 分析单元 | 具体提取/归纳单元是否已完成、等待调度或被阻塞 | `PENDING`、`RUNNING`、`SUCCEEDED`、`RETRY_WAIT`、`BLOCKED_TRANSIENT`、`FAILED_PERMANENT`、`CANCELED` |

`FINALIZED + PAUSED_SYSTEM` 是合法组合：本轮对话已向用户正常说明暂停，工作流仍保留可恢复工作。任何 API 或 UI 不得仅凭根 Run `done` 显示“分析已完成”。

### 3.2 转换规则

```text
Unit RUNNING --temporary failure--> RETRY_WAIT --due--> PENDING
Unit RUNNING --retry budget exhausted--> BLOCKED_TRANSIENT
Unit RUNNING --invalid input/auth/contract bug--> FAILED_PERMANENT

Workflow RUNNING --systemic provider fault--> PAUSED_SYSTEM
Workflow PAUSED_SYSTEM --provider healthy and retry due--> RUNNING
Workflow RUNNING --aggregate budget reached--> PAUSED_BUDGET
Workflow PAUSED_BUDGET --user grants bounded next allocation--> RUNNING
Workflow RUNNING --all required units succeed--> COMPLETED
Workflow RUNNING --some outputs usable, remaining permanently impossible--> PARTIAL
Workflow any active state --explicit user cancel--> CANCELED
```

每次转换必须持久化：`reasonCode`、分类、作用域（单元/系统）、发生时间、尝试次数、`nextRetryAt`、关联 Provider/模型、导致转换的事件标识。状态写入必须幂等，并以租约或版本号防止并发恢复重复认领同一单元。

## 4. 错误处置契约

| 分类 | 示例 | 默认处置 | 不应做的事 |
| --- | --- | --- | --- |
| 短暂 Provider/网络 | 连接中断、429、5xx、读超时 | 有界退避；系统性时暂停同 Provider 任务 | 立即并发重放全部单元 |
| Provider 容量/限流 | 持续 429、过载 | 熔断或降并发，等待健康恢复 | 把限流记为用户输入错误 |
| 输入/证据可修复 | 缺失来源、引用格式不合法 | 阻塞相关单元，要求补充或走限定修复 | 消耗 Provider 重试预算 |
| 授权/权限 | 凭据失效、无资源权限 | 明确暂停或永久失败，要求授权操作 | 无提示地自动重试 |
| 程序/契约错误 | 非法状态转换、持久化不变量失败 | 失败关闭、保留诊断和 checkpoint | 伪装为网络异常后无限恢复 |
| 调度资源 | runtime budget、宿主关闭、进程崩溃 | checkpoint 后重新排队或暂停；预算扩容必须显式授权 | 将预算耗尽直接等同于业务失败或自动无限扩容 |

错误正文、原始请求和敏感 Provider 信息仅进入受控诊断；公开状态只暴露稳定错误码和可执行的下一步。

## 5. 分阶段任务台账

状态值：`完成` 仅代表已达到本任务的验收条件；`进行中` 表示有正在验证的实现；`未开始` 表示未授权或尚未排期；`阻塞` 必须写明外部依赖。

| ID | 阶段与交付物 | 当前状态 | 完成条件 |
| --- | --- | --- | --- |
| R0 | 基础暂停、成果保留与阶段性收束 | 完成（确定性） | PurrTypos 定向测试通过；PurrA 候选产物的跨仓库可追溯性另列 R8；真实 Provider 验收另列 R7 |
| R1 | 双状态契约与 API 投影 | 实施完成，待验收 | 回合、工作流、单元状态可同时查询；Web/Electron 只使用新状态契约，不再将根 Run `done` 当成工作流完成 |
| R2 | 持久化退避调度 | 进行中 | `nextRetryAt`、指数退避、抖动、最大尝试/等待预算、进程重启恢复和幂等认领均有测试 |
| R3 | Provider 健康与熔断 | 进行中 | 按 Provider/模型追踪健康；故障时降并发或暂停；恢复后限速放量；不影响无关 Provider |
| R4 | checkpoint 与运行预算治理 | 进行中 | 预算耗尽能在安全 checkpoint 收口为可恢复调度事件；无重复成果、无漏记单元 |
| R5 | 用户恢复体验 | 进行中 | 自然语言阶段说明、可见恢复条件和明确“立即重试/稍后自动恢复/取消”入口；不暴露内部异常正文 |
| R6 | 指标、告警与事故回放 | 进行中 | 可按错误码、Provider、模型、阶段聚合；覆盖恢复成功率、暂停时长、重试放大率和永久失败率 |
| R7 | 真实端到端验收 | 未开始 | 重启后端后新建真实 Provider Run，并在 Electron 验证暂停、恢复、最终产物与历史重放；不以 mock 代替 |
| R8 | PurrA 新框架语义的跨语言候选产物与 PurrTypos 接入闭环 | 完成（确定性） | Python/TypeScript 状态契约一致；候选 wheel、清单、依赖锁定和实际安装内容的精确哈希一致；PurrTypos 消费新契约并通过跨仓库测试。真实 Provider/Electron 仍属 R7 |

## 6. 实施顺序

### R1：先固定状态与接口语义

- 设计持久化字段、枚举、状态转换图；定义历史 `FAILED` 记录的展示与迁移策略。
- 将 `partialCompletion` 从补充布尔值演化为可版本化的工作流状态投影；所有当前调用方只消费工作流投影。
- 为非法转换、重复恢复、取消与恢复竞争、根 Run 先结束但任务暂停等情形补测试。

### R2：把“重试”从调用循环变成调度能力

- 在任务仓储记录下一次允许执行的时间和重试策略快照，重启后仍有效。
- 使用指数退避与随机抖动；重试预算至少区分单元、任务、Provider 三层，防止一个小说任务耗尽全局容量。
- 调度器仅认领到期且满足依赖条件的单元；人工恢复只能明确地覆盖相应预算，不能隐式重置所有失败历史。

### R3：治理 Provider，而不是让单个任务猜测网络状态

- 建立 Provider/模型的闭合、打开、半开健康状态和滑动窗口统计。
- 在打开状态下不发起新的高成本调用，任务进入 `PAUSED_SYSTEM`/`RETRY_WAIT`；半开状态使用少量探测任务恢复。
- 实现每 Provider 的并发上限、故障降载和恢复斜坡，避免多个分片同时放大故障。

### R4–R5：收紧预算语义与用户控制面

- 将运行预算视为可检查点的调度边界；每个阶段提交可校验的 output/checkpoint 后再切换。
- 给出恢复原因、下次自动尝试时间和剩余工作；默认保持普通对话语言，详情仅在用户展开时展示。
- 用户取消必须是明确领域命令；关闭页面或 SSE 断开不得取消后台工作流。

### R6–R7：以数据和真实链路关闭计划

- 将错误分类、状态转换和调度决定写入可查询事件；定义告警阈值前先采集基线。
- 真实验收至少覆盖：网络中断、Provider 限流、读取超时、进程重启、用户取消、历史恢复和正常完成。
- R7 通过后，比较来源分析任务的端到端成功率、平均恢复时长和无成果中断率；仅在样本量与时间窗口明确时发布改善结论。

### R8：先让框架、产物和宿主说同一种状态语言

这不是把 PurrTypos 专属逻辑搬进 PurrA。框架只拥有可复用的长任务状态、失败处置和恢复契约；PurrTypos 仍拥有 Provider 健康/容量策略、SQLite 业务投影、自动恢复授权和用户界面。

1. **固定 PurrA 的跨语言契约。** Python 与 TypeScript 都必须把预算耗尽、瞬时 Provider 失败、永久失败、部分完成和用户取消表达为相同的任务/单元状态与稳定原因码。当前 TypeScript durable repository 在预算耗尽时仍将所有未完成单元和任务直接置为 `failed`；应改为由显式失败处置决定 `paused`、`partial` 或 `failed`，并保留已完成单元及恢复所需的原因、剩余工作和 checkpoint。
2. **补齐 TypeScript 与 Python 的同构测试。** 用同一份 conformance fixture 覆盖：预算耗尽、可恢复 Provider 故障、恢复后只执行未完成单元、部分完成、不可恢复输入/授权错误、取消与恢复竞争。任何一端语义变化都必须同时更新 fixture，禁止只修一个 SDK。
3. **重建可追溯的 PurrA 本地候选产物。** 本地候选可继续使用 `1.0.0`，其身份由精确 wheel SHA-256 而非版本号识别；不构成发布。隔离构建 Python wheel；更新 `purra-candidate.json`、`requirements-purra.txt` 和 vendored wheel，使三者 SHA-256 一致；再在干净环境确认实际安装 distribution 与该产物一致。
4. **接入 PurrTypos，而不是假设它自动继承框架改动。** 用新 wheel 替换当前候选包后，明确映射 PurrA 的处置/状态到 `workflowStatus`、`workflowPauseKind`、单元状态与任务事件。删除与框架新语义冲突的应用侧重复转换；保留 PurrTypos 的 Provider 策略和面向用户的阶段性收束。
5. **验证发布链。** 先跑 PurrA Python、PurrA TypeScript 及 SDK parity/conformance；再在 PurrTypos 新虚拟环境跑小说分析、LongTask 状态、恢复和前端契约测试；最后执行 R7 的真实 Provider/Electron 验收。前两类通过不替代 R7。

R8 当前事实：本地候选仍为 `purra-1.0.0`，不是发布版本；其身份由 wheel SHA-256 `9a3695b9fa2f37bc11b140c0077c5c1273c5b352fc2e13579af99ff859a03c2c` 识别。vendored wheel、`purra-candidate.json` 与 `requirements-purra.txt` 已一致；候选清单覆盖当前 PurrA 源树、Python/TypeScript 测试与契约共 427 个文件，逐项重新计算为零差异。PurrTypos 已实际重装该 wheel 并通过定向集成验证。不得把这些确定性证据延伸为真实 Provider 或 Electron 验收结论。

## 7. 验收与记录规则

每次更新任务时，在本节的变更记录追加一条，至少包括：日期、任务 ID、变更摘要、验证命令或真实环境、结果、未覆盖项。不得删除失败记录；修订结论时追加更正。

确定性验证与真实验收必须分开记录：mock、单元测试、候选包哈希和类型检查只能证明契约与集成，不能证明 Provider 连通性、模型行为或 Electron 用户路径。启动用于验收的服务必须在记录中写明停止方式，并在结束时确认本轮启动的进程已停止。

## 8. 变更记录

| 日期 | 任务 | 记录 |
| --- | --- | --- |
| 2026-09-11 | R0 | 已实现 `PAUSE_RECOVERABLE`、`BLOCKED`/`PAUSED` 保留语义、阶段性收束和 ZAI 分离超时；PurrA 定向恢复测试、PurrTypos 小说分析/Provider/包测试、前端会话测试与 typecheck 已通过。未执行真实 Provider 或 Electron 验收。 |
| 2026-09-11 | R1 | `conversationStatus` 与 `workflowStatus` 已分离，且公开 `workflowPauseKind`、`workflowReasonCode`、`workflowResumable`。SQLite 在任务状态上持久化暂停原因和作用域；用户暂停与 Provider 系统暂停均有回归测试。PurrTypos 小说分析/会话/进度/Schema 迁移测试、前端会话测试和 typecheck 通过。尚未完成 Electron 真实消费验证和历史数据回填。 |
| 2026-09-11 | R1 | 暂停、恢复、取消接口改为统一的任务控制回执，不再错误声明为 `NovelAnalysisRun`。异步恢复返回“已受理”及真实的当前工作流状态，避免在生命周期认领前误报 `running`；同步命令返回已完成及其状态。定向后端测试、前端会话测试和 typecheck 通过。 |
| 2026-09-11 | R1 | 历史事实收口：缺失状态原因的旧暂停任务投影为 `workflowPauseKind=unknown` 且保持可恢复；没有 LongTask 的旧对话保持独立，不被赋予虚假的工作流状态。任务计时与任务卡只消费 `workflowStatus`；旧原始字段仅供诊断，不再是 UI 回退路径。定向后端测试、前端会话/任务计划测试和 typecheck 通过。 |
| 2026-09-11 | R1 | 重启本地后端后，以已有持久化来源分析任务完成只读 HTTP 集成验证：新对话/工作流字段已由实际数据库经 API 投影。历史任务继续保留其原有 `failed/canceled` 事实，不被新状态逻辑改写。未提交新的 Provider 请求；桌面窗口因本机存在同 bundle id 的其他 Electron 实例而未形成可归因的 UI 验收。 |
| 2026-09-11 | R2 | 开始持久化单元退避：`waiting_retry` 写入指数退避与稳定抖动后的到期时间；SQLite 只认领到期单元，重启后计划仍保留。来源分析移除认领后的进程内退避，避免双重等待和重启丢失。完整回归、真实 Provider 与 Electron 验收按当前计划后置。 |
| 2026-09-11 | R2 | 增加来源分析的自动累计等待预算（当前冻结为 120 秒）。超过预算的单元不再无限等待，而是进入可恢复阻塞；用户恢复会清除该单元的自动退避账本，再从新的自动预算开始。完整回归仍后置。 |
| 2026-09-11 | R2 | 新增任务级自动恢复调度：系统暂停会记录带稳定抖动的下次恢复时间和累计等待账本；后端启动与运行期轮询只恢复到期、仍绑定本机已保存且模型身份未变的配置。模型配置按原始 ID 精确查找，绝不回退到其他可用模型。任务记录不保存 API Key；配置删除、模型/端点变更、凭据不一致、用户暂停或自动等待预算耗尽都会保持暂停。新 API 投影包含自动恢复资格与到期时间。测试、真实 Provider 和 Electron 验收按当前计划后置。 |
| 2026-09-11 | R3 | 新增持久化 `ai_provider_health` 控制面，以 Provider、模型和端点摘要隔离健康状态。429 会立即短时打开熔断器；其他已分类的暂时 Provider 故障在 60 秒窗口内连续三次才打开。打开期间不再发起新调用；到期后仅放行一个有 30 秒租约的半开探测，成功即关闭、失败重新打开。另以 Provider 与端点摘要共享的持久化调用租约，将该端点的并发真实调用暂定为 2；进程中断后的租约最长 150 秒自动失效。`provider_circuit_open` 与 `provider_capacity_limited` 都走既有系统暂停与自动恢复链路；认证、余额、请求格式和本地上下文预算均不计入 Provider 健康。尚未实现按不同 Provider 配置并发上限、恢复斜坡、指标和完整验证。 |
| 2026-09-11 | R4 | 来源分析任务声明 `budgetExhaustionDisposition=pause_and_extend` 后，聚合调用预算耗尽不再把尚未完成的单元改为失败或取消：已完成 Artifact 保留，其余单元变为 `blocked`，工作流以 `workflowPauseKind=budget` 暂停。它不会自动恢复或无限扩张预算；用户明确恢复时，基于仍未完成的模型单元，向任务预算增加恰好一轮受限尝试的调用额度，并留下该次授权记录。尚未验证运行时预算在各类预算种类、并发租约和真实 Provider 下的完整行为。 |
| 2026-09-12 | R5 | 来源分析暂停时新增自然语言控制条：不公开内部错误码，改为说明已保留/剩余步骤、系统自动恢复时间或预算恢复的受限授权。系统暂停可“立即继续”，预算暂停明确为“继续并补充一轮预算”，并提供结束本次分析入口；不可恢复状态不再显示通用继续按钮。视觉、交互和 Electron 验收尚未执行。 |
| 2026-09-12 | R6 | 新增 `ai_provider_health_events`，与规范输出日志分离。持久化记录 Provider 健康的故障观察、熔断打开、拒绝新调用、半开探测与恢复；事件只保存稳定错误码、状态、故障计数和冷却到期时间，不保存模型输入、输出或凭据。任务级状态转换事件、聚合查询、告警阈值和真实基线尚未完成。 |
| 2026-09-12 | R6 | 新增幂等的 `ai_agent_long_task_events`，将系统恢复排期、重启恢复、用户/系统暂停、预算暂停、恢复命令实际受理，以及恢复绑定不可用/命令被拒绝等事实写入同一任务时间线。已有错误报告的受控诊断投影会关联该任务，并展示工作流状态、单元进度、当前稳定原因、事件计数与最后一条事件；不保存 Prompt、模型输出、原始异常或凭据。 |
| 2026-09-12 | R6 | 现有稳定性趋势接口新增 `novelAnalysisReliability` 受控聚合：在同一 session/book/project/global 范围内，返回最多 100 个来源分析任务的工作流/单元状态、稳定原因码、恢复排期与派发事实、任务 Unit 所属 Run 的阶段计数，以及这些任务所用 Provider/模型的当前健康和事件计数。Provider 部分明确为共享端点健康历史，不能当作单任务因果归因。告警阈值、基线采集、管理界面和真实运行验证仍未完成；本轮按约定未执行测试。 |
| 2026-09-12 | R6 | 自动恢复的聚合不再把“已派发”冒充为成功：恢复生命周期会显式记录 `task_resumed.recoverySource=automatic/user`，终态统一记录为 `task_completed`、`task_failed` 或 `task_canceled`。聚合仅把一次自动派发后首次可观察的终态/再次暂停作为结果；用户随后手动恢复标为 `supersededByUser`，没有结果的保留为 `pending`。因此当前可报告的是“已完成/再次暂停/失败/待观察”的事实分布，而非未经样本验证的成功率。测试和真实基线仍后置。 |
| 2026-09-12 | R6 | 新增不可变的全局可靠性基线快照表和后台采集器：每个 6 小时时间桶至多保存一次当前的 100 任务无内容聚合，且仅在至少存在一个来源分析任务时写入。6 小时是分析存储上限（UTC 自然日最多四次写入），不是网络/Provider 超时、恢复等待或重试参数；采集器会等待到下一个自然时间桶，完全不发起模型调用，也不参与任务调度。启动时会尝试补采当前桶，失败只记录运维日志，不阻断后端。快照保留/清理策略尚未决定，不能在没有明确授权时自动删除历史诊断；真实样本、快照阅读界面和告警阈值仍未完成；本轮按约定未执行测试。 |
| 2026-09-12 | R6 | 基线现在可通过既有稳定性趋势诊断读取：仅 `scope=global` 返回最近 24 个压缩快照（最多 6 天），包含任务/单元计数、恢复结果、稳定原因码和 Provider 状态，不含任务内容或端点。session/book/project 查询明确标为全局基线不可用，避免将跨范围样本伪装成局部事实。损坏的旧快照降级为空字段，不使诊断请求失败。仍未实现面向普通用户的管理界面、快照保留清理或告警阈值；本轮按约定未执行测试。 |
| 2026-09-12 | R3 | Provider 半开探测成功后不再立即把共享端点恢复到 2 并发：持久化 `ramp_until_ms`，在接下来的 30 秒内将同 Provider/端点的真实调用上限降为 1，之后才回到固定正常容量。新的故障会清除斜坡；斜坡与单探测租约同为 30 秒，目的是保守恢复而非延长网络请求。状态和恢复事件都保留斜坡到期时间。尚无按 Provider 配置的容量策略、不同斜坡档位或真实验证；本轮按约定未执行测试。 |
| 2026-09-12 | R6 | 可靠性聚合和后续全局基线快照新增三类可核查指标：任务终态失败率、已标记 `fail_permanent` 的非展开单元失败率，以及单元认领次数相对有效单元数的重试放大率；另由追加式状态事件计算已结束和仍在进行的暂停时长。认领次数不是 Provider 实际请求次数，旧事件的秒级时间戳也不能用于 SLA 结论，空样本的比例显式为 `null`。未定义告警阈值、样本门槛或保留清理；本轮按约定未执行测试。 |
| 2026-09-12 | R3 | 新增用户可见的共享端点容量策略：设置页按“协议 Provider + 规范化端点”汇集模型，允许设为 1–16 路真实调用并恢复默认 2。服务端拒绝重复端点、未知 Provider 与非法数值；策略保存按顺序串行化，网络失败会提示未保存而不静默覆盖新值。运行时容量读取同一持久化策略；熔断半开和恢复斜坡仍优先强制为 1。损坏的本地旧策略保守回落默认 2。尚未完成不同 Provider 的默认建议、告警阈值及真实验证；本轮按约定未执行测试。 |
| 2026-09-12 | R6 | Provider 健康诊断与新采集的全局基线快照现在投影每个 Provider/模型当前生效的共享端点容量数值，但仍不返回端点或端点摘要。该数值是读取/采样时的策略，不会倒推为历史健康事件发生时的容量；旧快照没有该字段时显式为 `null`，不伪造为 0。 |
| 2026-09-12 | R3/R6 | 新增不可变的端点容量策略事件表。一次策略设定、变更或恢复默认，会与设置值写入同一事务，并只记录 Provider、端点摘要、前后容量和事件类别；不记录端点原文、模型内容或凭据。来源分析可靠性聚合和后续基线只返回所涉端点集合内按 Provider 汇总的变更计数，不能把它解释为单模型或单任务的因果关系。损坏的旧设置替换为经校验的新策略时不伪造未知历史变更。 |
| 2026-09-12 | R3 | 设置页不再只从当前模型列表推导端点策略：没有任何当前模型使用的已存策略仍会显示，并可恢复默认。这样删除或修改最后一个模型配置不会让有效策略变成不可见的“幽灵配置”；未来再次使用同端点前，用户仍能检查或移除它。 |
| 2026-09-12 | 验证 | 新增端点容量策略测试覆盖端点规范化/去重、跨模型共享并发、损坏设置的保守默认、策略与事件原子写入，以及可靠性聚合不泄露端点原文；`backend/tests/test_provider_capacity_policy.py` 为 6 通过。小说分析、进度、对话与稳定性聚合为 126 通过；认领/运行控制/会话路由/查询/ZAI 传输为 99 通过。前端 `npm run typecheck` 通过，来源分析/运行选择器/Electron 后端进程相关单测共 58 通过。期间修复了缺失 `Mapping` 导入、完成工作流遮蔽根对话失败，以及测试夹具仍使用旧状态字段的问题。完整 `test:backend` 与 `test:unit` 均受宿主单条命令约 30 秒窗口截断，未取得最终退出码；不得将它们记录为整套通过。 |
| 2026-09-12 | R7 | 当前命令环境未提供 `PURRTYPOS_DATA_DIR`，工作区也没有本机应用数据库；因此无法在不猜测用户数据位置或读取/输出凭据的前提下创建真实 Provider Run。真实 Provider 与 Electron 验收仍未完成，不代表失败，也不能由确定性测试替代。 |
| 2026-09-12 | R8 | 发现跨仓库产物闭环缺口：PurrTypos 正在使用标为 `1.0.0` 的本地 PurrA wheel，但实际 vendored wheel/候选清单 SHA-256 为 `931ca…`，`requirements-purra.txt` 注释为 `24fd…`；候选清单基线为 `707e16b`，不能证明它包含当前 PurrA 工作区后续源码。PurrTypos 已有应用侧状态、恢复、Provider 与 UI 改动，但尚未完成对最新框架语义的重新构建、安装、映射与跨仓库验收。 |
| 2026-09-12 | R8 | PurrA Python 与 TypeScript 将运行预算耗尽统一为显式 `pause_recoverable`/`fail_permanent` 契约：默认暂停、未完成单元阻塞、已完成结果保留；TypeScript 不再将该暂停二次记为普通单元失败。PurrTypos 小说分析通过 `BudgetExhaustionDisposition.PAUSE_RECOVERABLE` 接入框架，保留自己的“用户授权后补充一轮预算”机制。重建本地 `purra-1.0.0` wheel，逐文件核验 wheel 中 195 个 Python 源文件与当前 PurrA 源码完全一致；更新 vendored wheel、依赖哈希与 427 文件候选清单。干净 Python 3.11 虚拟环境安装成功；PurrA Python 全量 839 通过、TypeScript 全量 `npm run check` 通过、PurrTypos 后端定向 134 通过和前端 typecheck 通过。真实 Provider/Electron 仍未执行，保留在 R7。 |
| 2026-09-12 | 全项目确定性验收 | PurrTypos 全后端 `npm run test:backend` 为 2422 通过；全前端/Electron 单元 `npm run test:unit -- --test-concurrency=1` 为 492 通过。验收期间发现并修复旧 PurrA 工具契约导入、LongTask 创建幂等性遗漏预算处置字段，以及三项与当前产品契约不一致的测试夹具。使用全新临时 `PURRTYPOS_DATA_DIR` 启动后端，发现 40 个写作技能目录，`GET /health` 返回 `status=ok, db=up`；服务已停止并确认 18765 无监听。旧数据库不在兼容范围。Vite 前端生产编译成功；随后 `build:web` 的本地后端资源装配因离线环境尝试从 PyPI 取得 `fastapi` 等依赖而失败，属于可复现的离线打包缺口，不影响当前已安装运行环境的启动烟测，也不构成 Electron 成品包验收。 |
| 2026-09-12 | R5 | 修正暂停态的前端状态投影：来源卡片仅在实际执行时显示“分析进行中”，暂停时显示“分析已暂停”；通用会话面板在暂停/恢复中不再显示只能终止实时 Provider 调用的“停止生成”按钮，仍保留领域级继续和结束入口；Root 已终态时，和最终答复字面相同的阶段检查点回放不再重复渲染。新增回归测试；`npm run typecheck` 与前端/Electron 单元 494 通过。此修复不把真实 Provider 容量耗尽伪装成成功，也未替代 R7 的真实 Electron 验收。 |

## 9. 关联实现入口

- [来源分析状态与阶段性结果](../../backend/application/novel_analysis_agent_profile.py)
- [来源分析错误分类](../../backend/application/novel_analysis_executor.py)
- [来源分析 API 投影与恢复](../../backend/application/novel_analysis_service.py)
- [LongTask SQLite 状态转换](../../backend/infrastructure/persistence/sqlite_long_task_repository.py)
- [Provider 健康状态](../../backend/infrastructure/persistence/provider_health_repository.py)
- [ZAI 传输时限](../../backend/infrastructure/models/zai_chat.py)
- [PurrA 失败处置](../../../purra/src/purra/recovery/disposition.py)

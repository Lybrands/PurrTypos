# Pure Agent Handoff

> 更新时间：2026-08-11  
> 适用仓库：`/Users/liuyubin/Lybrand_project/PurrTypos`  
> 用途：在新的对话中快速恢复本轮重构的真实背景、边界、踩坑记录和当前实现状态。

## 1. 先读结论

这轮工作的最终方向不是继续修补旧剧本对话，而是：

1. 将原 `agent_core` 重新定位并抽取为通用框架 **PurrA**；
2. 删除旧剧本 Agent 的 AI 对话执行链路，以原生 Conversation、Turn、Operation、Task 和 Revision 重建业务流程；
3. 剧本 Agent 通过 PurrA 已有的模型工具循环、Artifact 生命周期和共享 Agent chunk 协议工作；
4. PurrA 只拥有通用运行时语义，剧本阶段、剧本工具、数据库查询、候选稿校验和发布全部留在剧本业务侧；
5. 模型按需调用工具读取项目和原作，不再由后端在每次调用前把大量正文暴力塞进 Prompt；
6. 模型生成的正文通过候选 Artifact 交付，不直接倾倒到聊天消息中；聊天中只展示面向用户的执行说明、工具操作和最终简短结论。
7. 长任务不再依赖一次模型调用完成全部产物，而是由产品层编译稳定的语义 Part Manifest，PurrA 按 Part 执行、持久化、恢复和汇总；
8. 模型差异只进入版本化 Capability Profile 和 Provider Adapter，PurrA Core 不按模型名或厂商名分支；
9. Operation 是产品任务的唯一完成权威：所有必需 Part 完成并原子发布 Candidate/Revision 后，才写入唯一一次正式 Assistant 结果。

长任务重构已在提交 `fddffcc` 完成并快进合并到 `feat/0.5.2`，本地合同门禁和完整测试通过。但截至本文更新时，仍没有使用用户真实 Provider/API key 完成跨模型端到端创作。因此，不能把 Fake Gateway、Profile 合同测试或因缺少凭据而 skip 的测试表述成“真实模型链路已经验证可用”。

## 2. 用户真正关心的目标

这次重构的评价标准不是函数数量少、代码行数少或快速让按钮恢复响应，而是：

- 逻辑通顺，没有非必要代码；
- 通用框架与业务层职责不会互相倒置；
- 不用重复包装、重复状态和重复正文增大维护成本；
- 对话过程可观察、可诊断、可恢复；
- 用户自然语言和产品按钮最终进入同一套语义理解与任务执行能力；
- 正文、过程信息、诊断信息各自进入正确的产品区域；
- 失败后保留已经完成且可复用的 Artifact，不盲目重新生成；
- 在测试阶段不为无价值的旧剧本 Agent 数据承担兼容成本。

“精简”不是把多行机械压成一行，也不是为了减少函数而删除抽象。只有当分支、适配器、重复包装或状态确实没有独立语义时才应收敛。

## 3. 本轮出现过的主要产品问题

### 3.1 对话链路不可用

先后出现过：

- “创作下一集”按钮无响应；
- 页面一进入执行就死循环；
- 首次对话后，再次发送没有反应；
- 对话报错，但页面只给出模糊失败；
- 历史输出、消息重新编辑并发送的能力丢失；
- 对话诊断面板消失或信息不完整；
- 正在执行计时器不递增，或者 Run 已完成后仍继续计时；
- 计划显示正在执行第三步，但计划面板只有第一步完成；
- 操作面板固定在整个对话底部，没有跟随对应的 Assistant Turn。

这些现象不能继续被当作几个互不相关的 UI bug。它们共同指向 Conversation、Turn、Operation、Run、SSE/replay 和前端投影之间缺少单一权威状态。

### 3.2 对“思考过程”的理解出现偏差

用户最初希望像 Codex 一样看到执行过程，旧实现曾尝试直接展示模型 reasoning 或诊断中的思考文本，造成：

- reasoning 不是流式到达，最后一次性出现；
- 面板中出现结构化 JSON；
- 用户看不到真正有意义的执行动作；
- `thinkable=true` 被误解为“必须把模型私有思维完整展示给用户”。

最终需求应理解为：

- 用户可见的执行过程不是模型私有 chain-of-thought；
- 过程区域展示模型主动输出的公开操作说明、工具调用、工具结果摘要和宿主生命周期事件；
- reasoning 只可作为受控诊断信息，不能成为产品过程区的唯一内容来源；
- 不得编造“正在分析”“正在整理”之类实际没有对应动作的编排文案；
- 工具调用是执行过程的重要组成部分，但任务步骤已经有单独区域，不应在过程面板重复展示步骤列表。

### 3.3 流式体验失真

出现过后端聚合大块文本后才下发、前端一块一块跳出的问题。需要坚持：

- 优先原样消费模型/Provider 实际产生的增量 chunk；
- 后端负责协议标准化、隐私过滤和持久化，不应等待完整文本后伪装成流式；
- 前端可以用短队列做平滑逐字呈现，但不能因此改变语义顺序或伪造执行事件；
- SSE 断线只影响订阅者，不能等价为取消 Run；重连通过持久化 cursor/snapshot 恢复；
- 计时器以权威 Run/Operation 的开始和终态为准，不能只靠组件本地 interval 判断是否结束；
- 历史消息、Operation、工具记录等延迟恢复时必须保持稳定滚动锚点和布局占位，不能在面板打开一两秒后插入内容造成整段对话抖动。

### 3.4 正文错误地出现在对话中

剧本正文曾被当作 Assistant 最终回答直接显示。正确产品形态是：

- 聊天只说明做了什么、调用了什么、产物是否准备好；
- 候选正文进入 Artifact/Revision；
- 对话消息旁提供查看或应用候选稿的产品操作；
- 工具参数投影必须隐藏候选正文，避免正文又从 tool call 区域泄漏；
- 同一正文不能同时保存在工具 payload、`sceneText` 和 `contentText` 多份副本中。

### 3.5 计划与过程区域职责重复

已明确的 UI 规则：

- 操作/过程面板跟随对应对话 Turn；
- 执行步骤由独立计划区域展示，过程面板不重复步骤；
- 连续工具/操作事件之间没有真实公开 commentary 时，合并进同一个可折叠过程块；只有真实用户可见文本才形成分隔；
- 计划只有 1～2 个步骤时不展示计划胶囊；超过 2 个步骤才展示；
- 同一轮用户对话是诊断面板最外层记录，内部通过选择器查看本轮产生的 Planner、Execution 等不同 Run；
- 每个 Run 的计时必须在该 Run 终态停止，不能用整轮对话是否结束替代。

### 3.6 项目文档暴露内部结构、内容空间被挤占

用户打开项目文档的目标是阅读和编辑，不是查看 Revision payload。最终确定的 UI 规则：

- 默认形态是“普通文档目录 + 单篇 Markdown 阅读器”，不能按 JSON 层级把内部对象全部展开；
- 优先读取 `contentText`，只有旧数据缺少正文时才把结构化 payload 转成可读 Markdown，并过滤 ID、digest、schema、storage mode 等技术字段；
- 集数、number、版本等已经在标题或导航出现的信息不要在正文中重复；
- 只有超长目录列表自身滚动，弹窗主体和内容阅读区不要出现多重滚动条；完整单文档没有目录价值时，不保留空洞侧栏；
- “基于此版本编辑”直接把原阅读区切换为 Markdown 编辑态，不再叠加第二个结构相同的编辑弹窗；
- 剧本正文允许正文与对应审阅并排对照，但审阅面板不得挤占主对话页；
- 候选稿入口必须携带确切文档类型、Revision/Artifact 目标，并跟随产生它的历史 Turn，打开后直接定位到对应文档。

### 3.7 审阅失败与真实审阅意见混在一起

本轮曾出现八集审阅几乎全部写成“当前环境未提供正文”“没有读取工具”。这不是八条内容缺陷，而是审阅输入/工具链失败。正确语义是：

- Review Manifest 必须引用被审阅 Draft Revision，Application/Context Port 必须按引用提供正文；不能让模型用“检查当前候选稿是否为空”代替读取正式稿；
- 内部异常、工具错误、正文装配失败、Provider 错误只进入 Run/Part failure 和诊断事件；UI 最多显示“第 N 集审阅失败”及恢复入口；
- 系统失败不得转成 Review Issue、不得进入 Issue 数量、Verdict 或批量处理，也不得发布不完整 Review Candidate；
- 真正的审阅意见由用户逐条标记为已解决、不成立或接受风险；可提供一键批量处理，但“确认定稿”始终由用户显式点击；
- 正文与审阅应在项目文档的剧本正文视图中并排查看，减少来回切换。

## 4. 输出长度问题的最终认识

这轮多次遇到输出超过上限，其中一个明确讨论的 Run 是 `run_a374276653694e87`。真正根因不是某个模型“不可控”，而是旧系统把一次 Invocation 的输出上限误当成整个业务任务预算，并在 `finish_reason=length` 后用相同 messages、Part 和 max tokens 原样重试。

需要保留的概念结论：

- `max_output_tokens`/`max_tokens` 是实际传给 Provider 的单次 Invocation 上限，不是模型必须生成的目标，也不是宿主生成后再自行比较的统计阈值；
- 默认值来自版本化 Model Capability Profile，用户显式覆盖值原样传给 Provider，但不能超过 Profile 声明的客观上限；
- 删除任务 `base_tokens`、`safety_factor`、reasoning reserve、hard cap 和按上下文百分比猜测输出保留区的逻辑；上下文不足时应减少可选输入、缩小语义 Part 或明确报能力不兼容，不能静默改写上限；
- Long Task/Operation 的累计 usage 只用于观测、费用、告警和用户/组织显式配额，不设置隐藏的 160K、256K、384K 总任务上限；累计消耗本身不决定后续任务能否继续；
- `length` 不是普通重试：有有效检查点就继续未完成 Part，可按业务边界细分就拆分，最小 Part 仍无法完成则返回 `model_task_mode_incompatible`；
- 不依赖 DeepSeek Beta continuation 或任何厂商续写接口保证正确性。纯文本续写只能是 Profile 明确支持时的优化，关闭后任务仍必须正确；
- 一个有限业务产物可以大于任意一次模型调用的输出上限，前提是产品层能把它拆成有界、可验证、可持久化的语义 Part；
- “一集一个 Unit”“一场一个 Part”“何时组装并发布候选稿”属于剧本业务 Manifest Compiler；PurrA 只处理不透明 Part key、检查点、失败处置和状态聚合；
- `max_model_rounds`/`max_progress_rounds` 只是单个 Run 的循环保险丝，不是 Long Task 的最多 Run 数，也不是内容预算；
- 已经产生非幂等 effect 或有效 Artifact 后，禁止整体重放请求。必须依据 effect state、Artifact digest、validation receipt 和完成覆盖范围恢复。

## 5. PurrA 与剧本业务的最终边界

### 5.1 PurrA 拥有

- Run 状态和生命周期；
- 模型轮次、工具轮次与标准事件；
- 通用 Tool Catalog/Policy/Schema 契约；
- Invocation 输出上限传递、归一化终止原因和 usage；
- 通用 Part 状态、检查点、失败处置与恢复协议；
- Artifact 通用生命周期和并发控制；
- 取消、lease、恢复、幂等和审计证据；
- 共享 Agent chunk/SSE 协议；
- 与任何具体业务无关的领域适配端口。

PurrA 不应出现 `screenplay`、Stage、Episode、Scene、Revision 等产品语义，也不能以“不走某条 Core 内置流程就报错”的方式强迫所有业务采用同一种编排。

### 5.2 剧本 Agent 拥有

- Conversation、Turn、Operation 与剧本任务拆分；
- 用户自然语言意图到业务任务的映射；
- Task Capability Requirements 与业务能力预检；
- 稳定语义 Manifest、Part 依赖、最小拆分边界与组装策略；
- 创作阶段、分集范围、来源书籍授权范围；
- 剧本工具名称、参数和动态开放规则；
- 项目、原作、人物、世界设定、背景和正文查询；
- 候选稿业务校验；
- Candidate Artifact 到正式 Revision 的发布；
- Apply 的产品交互和原子、幂等业务提交。

### 5.3 产品装配层拥有

`backend/application/composition_factory.py` 可以把剧本 Profile、Adapter 和 Tool Catalog 注册到通用 `AgentComposition`。通用 `AgentComposition` 只能接收抽象的 `profile_registrations`，不能反向 import 剧本模块。

初始工具调用接入没有要求修改 PurrA 源码；后续长任务重构只向 PurrA 增加模型无关的 Profile、Part、termination、checkpoint、cancel 和 usage 契约。剧本领域导入 PurrA 的公共契约是正常依赖，不等于把业务写入 PurrA。

## 6. 被否定的实现方式和踩坑原因

### 6.1 前端解析结构化 JSON 并自行聚合 Run

问题：前端同时承担协议解析、Run 聚合、过程文案和业务投影，导致流式、重放和实时路径行为不一致。

结论：删除前端结构化 JSON 提取和独立 Run 聚合。后端把领域事件适配为共享 Agent chunk，前端只消费标准协议。

### 6.2 用宿主预写的编排文案假装执行过程

问题：文案与模型和工具的真实动作无直接关系，诊断面板有 reasoning，产品过程区却没有有效内容。

结论：删除强行编排的过程文案。系统提示要求模型在工具调用前输出简短、公开、Markdown 友好的操作说明；过程区同时投影真实工具事件。

### 6.3 把私有 reasoning 当作 Codex 式执行日志

问题：reasoning 可能不可得、不可流式、包含内部结构或不适合向用户展示。

结论：Codex 式可展开过程的产品含义是公开的操作日志，不是完整 chain-of-thought。

### 6.4 后端调用模型前主动加载全部数据库上下文

问题：绕过模型的按需判断，增加输入 token，并把授权、检索和生成耦合成单次巨型调用。

结论：宿主只注入硬约束和最小身份/清单；正文、人物和世界信息通过 scope-checked 工具按需读取。分集生成启动时只预取 `episode_manifest` 中用于业务校验的 Scene ID 等最小清单。

### 6.5 确定性按钮完全绕过 Planner/语义理解

问题：用户可以直接输入“创作后三集”等没有预置按钮的自然语言，不能依赖按钮文案穷举业务命令。

结论：按钮与自由文本进入同一 Planner；正式按钮携带宿主不可变的 action/target/scope 边界，模型补全 instruction，但不能把正式命令降级为 answer。已经持久化并拆好的剧本 Task 不应再在 PurrA 内嵌套生成一份重复 Planner 计划。

### 6.6 失败后整体重试超长模型调用

问题：浪费已经生成的 token，可能重复非幂等写入，且无法利用已持久化候选稿。

结论：先查 Run、事件和 Artifact，再根据覆盖范围恢复。候选写入使用 CAS、幂等键和 Run 绑定。

### 6.7 把剧本 Profile 直接注册进通用 Composition

问题：曾触发架构门禁 `Generic application screenplay debt grew`，说明通用应用层反向知道剧本。

结论：注册移到产品侧 `composition_factory.py`；`AgentComposition` 只保留领域无关扩展点。

### 6.8 为每个只读工具写一个几乎相同的 handler

问题：产生十几个只有函数名不同的一行包装，后续修改 receipt、取消或编码逻辑时容易遗漏。

结论：使用统一 `bind_read(...)` 闭包绑定查询方法。这里的精简来自共享语义，而不是追求少写函数。

### 6.9 在多个位置保存同一候选正文

问题：正文同时进入模型上下文、工具结果和 Artifact 多个字段，造成 token 浪费、状态不一致和泄漏风险。

结论：Episode 候选正文只保存一次；聚合 `contentText` 在读取/发布时派生。对话和可见工具调用中对候选 payload 脱敏。

### 6.10 为测试阶段旧数据保留复杂兼容层

问题：旧剧本 Agent 尚未投入生产，兼容代码会继续污染新边界。

结论：允许定向删除不兼容的旧剧本对话数据，不恢复旧 fallback；不能误删 Book、Outline、Article、人物、世界设定等非剧本对话数据。

### 6.11 把 `length` 当成“提高预算后原样重试”

问题：相同请求只改变 attempt 或再次发送，不会增加完成概率；已产生的长输出被丢弃，非幂等工具还可能重复执行。

结论：Provider 只报告归一化 termination。PurrA 根据检查点、Part 是否可拆和 effect state 选择 resume、split、pause 或 fail；相同 request fingerprint 的 `length` 请求不得自动重放。

### 6.12 围绕 DeepSeek 写特例

问题：把 reasoning、tool arguments、finish reason 和输出上限写成 DeepSeek 分支后，换模型就需要重写 Runtime，通用 Agent 框架名存实亡。

结论：每个模型使用同一个版本化 Capability Profile schema，差异只在 Profile 和 Provider Adapter。PurrA Core 不读取模型名或 Provider 名；业务声明能力要求，不声明具体模型。切换模型必须由用户显式发起并重新预检，已完成 Part 不重做，不兼容时任务保持 `paused`。

### 6.13 用累计 token 充当隐藏任务预算

问题：把 Operation 已消耗 100K、160K 或 384K 当作后续是否允许执行的依据，仍会让大于该数字的有限任务必然失败，并形成另一个没有协议依据的魔法值。

结论：累计 usage 是 Run 幂等汇总的观测数据，不是默认完成预算。真正边界是每次 Invocation 的 Profile 上限、用户显式配额、语义 Part 可完成性和任务状态机。

### 6.14 完成一个 Run/第一步就把任务标为完成

问题：Run、Unit、Long Task、Operation 和 Turn 的终态被混为一谈，导致第一集或第一步结束后显示任务完成，后续步骤却仍在执行。

结论：Run 只代表一个 Part 的一次尝试；Long Task 只有全部 required leaf Parts 完成才成功；Operation 是唯一产品完成权威。部分完成、局部失败、等待恢复都不能投影为 `succeeded`。

### 6.15 把内部失败写成正式审阅意见

问题：某集读取失败后继续审阅下一集，最后把八次同类工具错误包装成八条严重审阅问题，既污染业务数据，也掩盖真正系统故障。

结论：系统性故障立即停止领取后续同类 Unit；已证明的局部故障进入恢复队列。内部 failure 与 Review Issue 使用不同存储和 UI 投影，缺少任一必需集时不得发布 Review Candidate。

### 6.16 把子 Run final 和固定模板当作产品最终回答

问题：执行中穿插正式输出，输出后又回到过程区；请求开始和结束还出现“正在理解请求”“剧本任务已完成”等宿主硬编码文案。

结论：公开 commentary、工具活动和子 Run final 都属于执行事实。只有 Candidate/Revision 与 Operation 在同一原子 finalization 中成功后，Application 才写入一次 `ASSISTANT_FINAL`。暂停、失败、取消没有成功文案和产物面板；实时与历史重放读取同一个 finalization receipt。

### 6.17 用关闭页面、SSE 或本地组件状态实现取消

问题：断开订阅会误杀后台任务；或者前端显示已停止，但 worker 仍领取 Unit、活动 Invocation 仍写入半截 Artifact。取消与完成竞争还会出现两个终态。

结论：取消是幂等、持久化的控制命令。短事务写 `cancel_requested` 和 receipt，提交后再通知 worker/Provider；调度器停止领取新 Part，未验证输出不得提交。SSE 断开、页面关闭和刷新都不等于取消。

### 6.18 前端自行推导 Operation 成功和产物状态

问题：前端根据最后一个 Run、最后一个 chunk 或候选卡片猜测任务是否完成，实时流与刷新重放会得到不同结果。

结论：前端只投影持久化 Operation、Part progress、finalization receipt 和 Artifact/Revision 引用。发送新任务、继续执行、取消和查看产物的可用性都由权威状态决定，不能从展示文本反推。

### 6.19 把本地合同测试等同于真实 Provider 验收

问题：Fake Gateway 和 Profile 合同可以证明协议与状态机，却不能证明真实 Provider 的流式 finish、reasoning、tool argument、usage 和取消语义完全符合假设。

结论：仓库不维护或运行付费真实 Provider E2E。DeepSeek reasoning on/off、非 DeepSeek Provider、显式换模恢复、真实截断/中断以及取消与历史重放由项目所有者手工验收。

### 6.20 测试后遗留服务或按端口误杀进程

问题：只看到 `5173`/`18321` 就直接 kill，可能误停其他项目；只终止 Vite 或 Python 子进程，又会留下 `npm`/`concurrently` 父进程和另一端服务。

结论：先用 cwd 和父子进程树确认服务属于当前仓库，再终止 `npm run dev:web` 根启动链，让 `concurrently -k` 关闭 Vite 与后端；最后分别用 `lsof` 确认相关端口无监听。不要把历史 PID 写进操作手册。

## 7. 当前已经落地的工具调用设计

领域定义：

- `backend/domains/screenplay_agent/tools/catalog.py`
- `backend/domains/screenplay_agent/tools/schemas.py`
- `backend/domains/screenplay_agent/agent_context.py`
- `backend/domains/screenplay_agent/adapter.py`

基础设施实现：

- `backend/infrastructure/screenplay/tools/tool_catalog.py`
- `backend/infrastructure/screenplay/tools/query.py`
- `backend/infrastructure/screenplay/tools/candidate_artifact.py`

应用执行：

- `backend/application/screenplay_agent_profile.py`
- `backend/application/screenplay_agent_task_executor.py`
- `backend/application/screenplay_candidate_model.py`
- `backend/application/screenplay_structured_call.py`
- `backend/application/screenplay_agent_context.py`
- `backend/application/composition_factory.py`

模型可见工具循环由 PurrA Runtime 统一执行，应用层不再维护平行的
`screenplay_tool_calling.py`。

命名与 Writing/Novel Agent 保持一致：

- 领域文件名：`catalog.py`
- 基础设施文件名：`tool_catalog.py`
- Builder：`build_screenplay_tool_catalog(...)`

不要重新引入 `Toolkit` 或 `ToolkitLog` 命名。

### 7.1 当前 17 个模型可见工具

剧本读取：

1. `inspectScreenplayProject`
2. `readScreenplayDeliverable`
3. `searchScreenplayDeliverables`
4. `getScreenplayEpisodeContext`

原作读取：

5. `inspectSourceStructure`
6. `readSourceChapters`
7. `searchSourceText`
8. `listSourceCharacters`
9. `readSourceCharacters`
10. `listSourceWorldEntities`
11. `readSourceWorldEntities`
12. `readSourceBackground`
13. `querySourceStoryFacts`
14. `readSourceOutline`
15. `readSourceStyle`

候选稿：

16. `writeScreenplayCandidatePart`
17. `inspectScreenplayCandidate`

工具按照 `sourceAnalysis`、`creativeBrief`、`structure`、`sceneList`、`screenplayDraft` 和 `review` 阶段动态开放。原创项目不开放原作工具。`projectId`、`taskId`、`unitId`、目标阶段、来源书籍和授权范围由宿主绑定，不能让模型在参数中修改。

### 7.2 Candidate Artifact 生命周期

正确顺序：

```text
模型按需读取 -> writeScreenplayCandidatePart
              -> Run 级 open Artifact
              -> 剧本领域严格校验
              -> Artifact finalize
              -> Task output
              -> Candidate Revision 发布
              -> 用户显式 Apply
```

如果模型最终回答截断或失败，但已经写入完整且校验通过的候选 Artifact，可以继续 finalize；如果校验失败，Artifact 保持 open 供诊断，不要立即重新生成。

所有实际原作读取写入 `screenplay_source_receipts`。最终 Revision 聚合规划、生成和完成阶段全部相关 `source_run_ids`，不能只记录最后一个 Run。

### 7.3 当前长任务、Operation 与模型能力设计

权威聚合层级：

```text
Turn
└── Operation
    └── LongTask / ArtifactManifest
        └── required Part / Unit
            └── Run
                └── ModelInvocation
```

- Model Profile 声明 context、单次输出、reasoning、tool calling、structured output、stream finish、cancel 和 usage 能力，并持久化稳定 digest；
- Task Requirements 声明当前任务需要哪些能力。创建有副作用的 Run 前预检；不能静默关闭思考、静默换模或把工具任务降级成纯文本回答；
- Product Application 把任务编译成稳定 semantic key、依赖和 required leaf Parts；Artifact 是 Part 内容的唯一来源，LongTask 数据只保存 ref/digest/validation receipt；
- `length + checkpoint` 从未完成 Part 恢复；`length + splittable` 由业务 Compiler 生成稳定子 Part；最小 Part 无检查点则明确暂停/失败，不循环；
- 用户显式换模恢复时，新模型通过同一 Requirements 后从下一个未完成 Part 继续；不兼容时保持 `paused`；
- Candidate Revision、Operation `succeeded`、唯一 `ASSISTANT_FINAL` 和 finalization receipt 在一个原子边界收口；
- cancel receipt、Run 终态、Part/LongTask/Operation/Turn 结算必须幂等，重启后从持久化状态恢复。

## 8. 诊断真实 Run 时必须遵守的顺序

本轮用户提供过的代表性 Run ID 包括：

- `run_ea6b8aeee52f471b`
- `run_b6e1511bc65c4cb3`
- `run_a374276653694e87`
- `run_4f9a84cdf42646ab`
- `run_a48a76c8249d4341`

这些 ID 只能作为事故索引。新的对话不能仅凭本文件猜测它们当时的精确错误。

遇到新的 Run 报错时：

1. 优先查询 Electron userData 数据库：`/Users/liuyubin/Library/Application Support/purrtypos/purrtypos.db`；
2. 查看 Turn/Operation/LongTask/Part/Run 的绑定、required Parts、当前 active/paused/failure 状态和 finalization receipt；
3. 查看 Invocation 使用的 Profile id/digest、reasoning mode、requested max tokens、request fingerprint、usage 和 termination reason；
4. 查看对应 Artifact ref/digest、写入覆盖范围、validation receipt、effect state 和是否 finalize；
5. 查看 tool events、cancel receipt、SSE cursor/replay 与实时投影是否一致；
6. 再判断是 Provider、模型能力、上下文装配、工具契约、业务校验、持久化事务、SSE 投影还是恢复状态机问题；
7. 对非幂等 POST、effect unknown 和候选写入禁止自动整体重试。

不要因为源码中搜不到 Run ID 就下结论，也不要把 Provider 余额不足、HTTP 402 等外部错误误判成 Agent 架构失败。

## 9. 已完成的验证

长任务实现锚点为提交 `fddffcc test(agent): gate model-agnostic long-task execution`。该提交在功能分支和快进合并后的 `feat/0.5.2` 上分别执行完整门禁：

- `npm run check:agent-refactor` 通过：45 项架构边界、12 项模型合同/事故回放、92 项剧本验收、TypeScript、160 项前端测试、1341 项后端测试；
- `npm run check` 通过：Purr Components、TypeScript、160 项前端测试、18 项稳定性测试、1341 项后端测试；
- `git diff --check` 通过；
- PurrA Core 厂商名和剧本业务名边界检查通过；
- 真实 Provider 行为不在仓库自动化结果中声明，由项目所有者另行验收。

工作树验证踩过一个环境坑：隔离 worktree 没有自己的 `.venv`，直接设置 `PURRTYPOS_PYTHON="$PWD/.venv/bin/python"` 会在稳定性测试阶段找不到 pytest。应显式使用主工作区解释器：

```bash
PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python npm run check
```

以上数字只证明 `fddffcc` 对应树；当前分支继续产生新提交后必须重新执行相关门禁，不能把历史结果冒充当前结果。

## 10. 项目所有者手工完成的真实验证

在具备真实凭据的环境完成一次受控的真实模型端到端验证：

1. 选择一个小型测试剧本项目；
2. 提交一个需要读取原作并生成单个候选 part 的请求；
3. 确认模型先输出公开操作说明，再发出所需工具调用；
4. 确认工具调用和结果摘要在过程区域流式出现；
5. 确认正文没有出现在聊天、reasoning 或可见 tool arguments；
6. 确认 Candidate Artifact 被写入、校验、finalize 并发布为 Revision；
7. 确认来源 receipts 和全部 `source_run_ids` 完整；
8. 再次发送消息，确认 Turn 不会卡死；
9. 刷新页面并重放，确认过程、计时终态和操作面板归属一致；
10. 分别验证 reasoning 开启和关闭，并至少使用两个非 DeepSeek Provider 走同一正式协议；
11. 显式换模后从未完成 Part 继续，确认已完成 Artifact 不重做；
12. 主动制造一次 `length` 或传输中断，确认从检查点恢复/拆分，不会原样重试；
13. 取消活动任务，确认待领取 Part 停止、半截输出不提交、重复取消幂等；
14. 对审阅任务确认正文按 Revision 引用可读，系统失败不进入 Review Issue；
15. 刷新与历史重放只出现一次与权威 Candidate/Revision 一致的最终回答。

如果启动了 Web/Electron/backend 测试进程，交付前必须停止本轮启动的全部进程，并确认相关端口没有监听者。

## 11. 新对话禁止直接做的事情

- 不要先写补丁；先复现、查数据库 Run 和 Artifact；
- 不要恢复旧剧本 Agent conversation fallback；
- 不要在 PurrA 中添加剧本专属判断；
- 不要让通用 `AgentComposition` import 剧本模块；
- 不要恢复前端 JSON 提取或自建 Run 聚合；
- 不要把完整 reasoning 当作用户执行日志；
- 不要在聊天中显示候选正文；
- 不要把全量原作正文预加载进 Prompt；
- 不要用任务估算、上下文百分比或 reasoning reserve 静默缩小 Profile 的单次输出上限；
- 不要靠无限提高 `max_output_tokens`、累计 token 硬上限或增加相同请求重试解决巨型任务；
- 不要在 PurrA Core 中按 DeepSeek、智谱、Kimi、MiMo 或任何模型名分支；
- 不要让 `length` 进入 JSON repair、reasoning fallback 或原样重试；
- 不要在有有效 Artifact 时整体重试；
- 不要把子 Run 成功、第一步完成或部分 Unit 完成投影为 Operation 成功；
- 不要把内部失败写进 Review Issue，也不要发布缺少必需 Part 的 Candidate；
- 不要在请求开始和任务结束显示宿主硬编码的编排文案；
- 不要用 SSE 断开、页面关闭或本地 loading 状态代替持久化取消；
- 不要把同一正文复制到多个字段；
- 不要为了“代码少”破坏清晰边界，也不要为十几个同语义工具保留重复 handler。

## 12. 新对话可直接使用的恢复提示

```text
请先阅读 docs/design/Pure Agent Handoff.md，
再阅读 docs/design/purra-screenplay-refactor-charter.md 和
docs/design/screenplay-agent-api-v2.md，并阅读
docs/superpowers/specs/2026-08-11-purra-model-agnostic-long-task-execution-design.md。

先核对当前工作树和上述文档是否漂移。不要恢复旧剧本 Agent 链路，
不要修改 PurrA 来容纳剧本业务，不要为单一模型写 Core 特例。
先用真实模型完成一次小范围端到端验证。如果出现 Run 错误，先查
Electron SQLite 中的 Turn、Operation、LongTask、Part、Run、Invocation、
events、Artifact、finalization/cancel receipt，再提出结论。所有修复都必须
通过 npm run check:agent-refactor，并单独说明真实 Provider E2E 是通过、失败
还是因缺凭据而阻塞；skip 不能算通过。
```

## 13. 文档优先级

发生冲突时按以下顺序判断：

1. 当前代码和数据库真实状态；
2. `docs/design/purra-screenplay-refactor-charter.md` 的架构边界；
3. `docs/superpowers/specs/2026-08-11-purra-model-agnostic-long-task-execution-design.md` 的长任务、能力和恢复契约；
4. `docs/design/screenplay-agent-api-v2.md` 的业务与持久化契约；
5. 本交接文档的历史问题和恢复提示；
6. 旧设计文档、旧 Run 文案或被删除实现。

本文件是恢复上下文，不是替代代码审查、数据库诊断或真实端到端验证的权威证据。

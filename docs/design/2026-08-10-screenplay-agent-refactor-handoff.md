# 剧本 Agent 与 PurrA 重构对话交接记录

> 更新时间：2026-08-10  
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

截至本文生成时，工具调用阶段已通过自动化门禁，但还没有使用用户真实 provider/API key 完成一次线上模型端到端创作。因此，不能把“测试通过”表述成“真实模型链路已经验证可用”。

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
- 计时器以权威 Run/Operation 的开始和终态为准，不能只靠组件本地 interval 判断是否结束。

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
- 计划只有 1～2 个步骤时不展示计划胶囊；超过 2 个步骤才展示；
- 同一轮用户对话是诊断面板最外层记录，内部通过选择器查看本轮产生的 Planner、Execution 等不同 Run；
- 每个 Run 的计时必须在该 Run 终态停止，不能用整轮对话是否结束替代。

## 4. 输出长度问题的最终认识

这轮多次遇到输出超过上限，其中一个明确讨论的 Run 是 `run_a374276653694e87`。还出现过“已经生成约 1.2 万 token 后再整体重试”的错误恢复思路。

需要保留的概念结论：

- `max_output_tokens` 是上限，不是模型必须输出到的目标长度；
- 不应删除所有上限。没有边界会放大成本、上下文和终止风险；
- 单纯把上限提高到模型极限，只会掩盖“一次请求承担太多工作”的编排问题；
- PurrA 负责通用预算、终止原因、截断信号和可恢复协议；
- “一集一个工作单元”“分集候选怎样续写”“何时提交候选稿”属于剧本业务编排；
- 正文应通过工具分段或按业务单元写入持久化 Artifact，而不是依赖一次超长最终回答；
- 已经产生非幂等写入或大段有效输出后，不能无条件从头重试；应检查持久化 Artifact 和完成覆盖范围，从断点继续或直接进入校验；
- 对输出截断的恢复必须区分：尚未写入、幂等写入、已经写入但未 finalize 三种状态。

## 5. PurrA 与剧本业务的最终边界

### 5.1 PurrA 拥有

- Run 状态和生命周期；
- 模型轮次、工具轮次与标准事件；
- 通用 Tool Catalog/Policy/Schema 契约；
- output budget、终止原因和 usage；
- Artifact 通用生命周期和并发控制；
- 取消、lease、恢复、幂等和审计证据；
- 共享 Agent chunk/SSE 协议；
- 与任何具体业务无关的领域适配端口。

PurrA 不应出现 `screenplay`、Stage、Episode、Scene、Revision 等产品语义，也不能以“不走某条 Core 内置流程就报错”的方式强迫所有业务采用同一种编排。

### 5.2 剧本 Agent 拥有

- Conversation、Turn、Operation 与剧本任务拆分；
- 用户自然语言意图到业务任务的映射；
- 创作阶段、分集范围、来源书籍授权范围；
- 剧本工具名称、参数和动态开放规则；
- 项目、原作、人物、世界设定、背景和正文查询；
- 候选稿业务校验；
- Candidate Artifact 到正式 Revision 的发布；
- Apply 的产品交互和原子、幂等业务提交。

### 5.3 产品装配层拥有

`backend/application/composition_factory.py` 可以把剧本 Profile、Adapter 和 Tool Catalog 注册到通用 `AgentComposition`。通用 `AgentComposition` 只能接收抽象的 `profile_registrations`，不能反向 import 剧本模块。

这一轮工具调用接入没有要求修改 PurrA 源码。剧本领域导入 PurrA 的公共契约是正常依赖，不等于把业务写入 PurrA。

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

- `backend/application/screenplay_tool_calling.py`
- `backend/application/screenplay_agent_task_executor.py`
- `backend/application/screenplay_agent_context.py`
- `backend/application/composition_factory.py`

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
2. 查看 Run 终态、error code、model invocation、tool events、usage 和 termination reason；
3. 查看对应 Artifact 是否已经创建、写入多少、是否 finalize；
4. 查看 Conversation Turn、Operation、Task、Run 的绑定是否一致；
5. 再判断是 Provider、模型输出、工具契约、业务校验、SSE 投影还是恢复状态机问题；
6. 对非幂等 POST 和候选写入禁止自动整体重试。

不要因为源码中搜不到 Run ID 就下结论，也不要把 Provider 余额不足、HTTP 402 等外部错误误判成 Agent 架构失败。

## 9. 已完成的验证

工具调用实现完成后记录的门禁结果：

- `npm run check:agent-refactor` 通过；
- 架构边界检查 32 项通过；
- 剧本验收测试 43 项通过；
- 前端单元测试 131 项通过；
- 后端测试 1188 项通过；
- TypeScript `tsc --noEmit` 通过；
- 最后一次候选稿/上下文专项测试 26 项通过；
- `git diff --check` 通过。

重点新增测试：`backend/tests/test_screenplay_tool_catalog.py`，覆盖阶段/来源工具门控、宿主参数隐藏、候选 Artifact 正文单一存储、来源 receipt、模型公开 commentary、真实 tool-call chunk 和候选正文脱敏。

注意：最后一次 `episode_manifest` 小调整之后重新跑了专项测试和 43 项剧本验收；完整门禁是在该小调整之前通过。该调整没有改变 PurrA 或公共协议。

## 10. 仍需完成的真实验证

下一阶段首先应做一次受控的真实模型端到端验证，而不是继续扩展功能：

1. 选择一个小型测试剧本项目；
2. 提交一个需要读取原作并生成单个候选 part 的请求；
3. 确认模型先输出公开操作说明，再发出所需工具调用；
4. 确认工具调用和结果摘要在过程区域流式出现；
5. 确认正文没有出现在聊天、reasoning 或可见 tool arguments；
6. 确认 Candidate Artifact 被写入、校验、finalize 并发布为 Revision；
7. 确认来源 receipts 和全部 `source_run_ids` 完整；
8. 再次发送消息，确认 Turn 不会卡死；
9. 刷新页面并重放，确认过程、计时终态和操作面板归属一致；
10. 主动制造一次截断或中断，确认不会从头重复生成已写入正文。

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
- 不要靠无限提高 `max_output_tokens` 解决巨型任务；
- 不要在有有效 Artifact 时整体重试；
- 不要把同一正文复制到多个字段；
- 不要为了“代码少”破坏清晰边界，也不要为十几个同语义工具保留重复 handler。

## 12. 新对话可直接使用的恢复提示

```text
请先阅读 docs/design/2026-08-10-screenplay-agent-refactor-handoff.md，
再阅读 docs/design/purra-screenplay-refactor-charter.md 和
docs/design/screenplay-agent-api-v2.md。

先核对当前工作树和上述文档是否漂移。不要恢复旧剧本 Agent 链路，
不要修改 PurrA 来容纳剧本业务，先用真实模型完成一次小范围端到端验证。
如果出现 Run 错误，先查 Electron SQLite 中的 Run、events、Artifact、
Conversation Turn 和 Operation 绑定，再提出结论。所有修复都必须通过
npm run check:agent-refactor，并单独说明是否完成真实 provider 验证。
```

## 13. 文档优先级

发生冲突时按以下顺序判断：

1. 当前代码和数据库真实状态；
2. `docs/design/purra-screenplay-refactor-charter.md` 的架构边界；
3. `docs/design/screenplay-agent-api-v2.md` 的业务与持久化契约；
4. 本交接文档的历史问题和恢复提示；
5. 旧设计文档、旧 Run 文案或被删除实现。

本文件是恢复上下文，不是替代代码审查、数据库诊断或真实端到端验证的权威证据。

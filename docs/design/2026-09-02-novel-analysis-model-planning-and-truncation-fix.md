# 小说来源分析：模型规划与工具调用截断修复记录

## 当前状态

- 问题一已完成代码修复、确定性回归和真实模型来源核验。
- 问题二已完成失败分类与有界重试代码修复和确定性回归；尚未用修复后的真实 Provider Run 验证，因此不标记为运行态已修复。

## 范围

本记录跟踪两个相互独立的问题。实施时先修复计划来源，再用新的运行证据判断工具调用截断是否仍会发生。不能在没有新证据时把两者合并为同一个根因。

## 问题一：宿主固定公开计划

### 已确认事实

- 小说来源分析 Profile 曾由 `_NovelAnalysisPlanner` 固定返回三个 `WorkStep`：故事概览、事实脉络、写作技法。
- `run_f34895776ba344be` 的 planning operation 用时 14ms，`modelAttempts=0`，没有 `planning.progress`；它不是模型规划。
- 前端从 LongTask 的 `analysisPlan` 渲染这三个步骤，因此用户看到的是宿主模板。
- 前端还曾在 `analysisPlan` 缺失时，把 durable Recipe 的 `units` 包装成一份“分析进度”计划；这同样混淆了模型计划和工作流执行状态。
- 新 Run `run_8535635e8ea6413c` 记录了 1 次 planning 模型调用、423 批 Provider 输出和一条 `planning.progress`。其原始 `provider.content_delta` 返回了标题“小说全局分析”以及 `story-overview`、`fact-threads`、`writing-technique` 三步；这份最新计划来自模型，不是旧宿主 Planner。
- 最新计划仍与旧计划相似，是因为产品请求明确要求同时交付“全局故事概览、事实脉络、写作技法”。相似性本身不能用来判断计划来源。

### 修复契约

- 删除宿主固定 Planner 和固定 `WorkStep` 内容。
- 由 `AgentComposition` 装配 PurrA `AgentPlanner`，计划标题、目标、步骤、描述和依赖全部来自模型。
- 宿主只保留确定性约束：当前来源范围、只读边界、审核边界，以及步骤只能是模型分析或复核；步骤数量由模型按请求决定，不设置领域固定上限。
- `novel_analysis_method` 作为受信任的领域方法上下文同时提供给 Planner、正文分析单元和分析追问；它提供证据分层、故事动力、人物、叙事组织、表达技法、主题与技法提炼视角，但明确不是固定清单，不得被展开成宿主计划。
- 来源正文是分析范围内的权威文本证据，但不具有指令权限；正文中的命令或角色要求只能作为作品内容分析。
- 私有 durable Recipe 继续负责分片、证据校验、恢复、幂等和 Artifact 收口；它不能成为第二份公开计划。
- 小说来源分析入口使用 Auto，不再在模型第一次普通调用前强制进入 Planner。模型可以直接回答，也可以先输出公开的当前步骤标题，再通过私有 `request_plan` 进入 Planner；普通工具执行后仍可通过 `request_remaining_plan` 规划剩余工作。
- 当 Auto 的普通调用同时返回公开文字和内部规划控制时，Core 先把模型原文作为 commentary 持久化并发布，再启动 Planner；不得用固定文案、定时器或额外模型调用伪造首段输出。
- `analysisPlan` 缺失时不再从 durable Recipe `units` 补造任务计划。
- 不为旧的三步计划增加兼容分支，也不改写历史 Run。

### 来源诊断

- 开发诊断面板从已持久化的 planning stream 读取模型原始 JSONL，不从 `analysisPlan` 或 Recipe 反向合成。
- 只还原 `provider.content_delta`；`provider.reasoning_delta`、普通执行模型输出和请求参数不进入该投影。
- 面板同时显示 invocation、output stream、revision/attempt、公开 `planning.progress` 和 Planner 时序，便于核对计划来源。
- 该数据通过独立的 `planner-diagnostics` 端点按需读取，不进入普通 SSE、对话消息或历史回放。

## 问题二：`tool_call_truncated`

### 已确认事实

- 子 Run `run_da055fd3a029433e` 在第一个 `extract_section` 单元失败。
- 本次输入约 3009 tokens；输出达到调用上限 16384 tokens，其中 reasoning 为 13003 tokens。
- Provider 在工具参数仍未闭合时耗尽输出预算，因此 PurrA 正确判定为 `tool_call_truncated`。
- 该单元只绑定一个 2225 字符片段。现有证据不支持“固定三个公开步骤直接导致单元过大”的结论。
- 新 Run `run_342ecab0c3d94569` 的 Planner 已生成两步计划并成功结束；失败仍发生在第一个 `extract_section` 单元，证明公开计划改为模型生成后，截断问题仍会独立发生。
- 子 Run `run_ecab07095ae94f9c` 第二轮实际输入 3973 tokens，输出正好达到 16384 tokens，其中 reasoning 为 16329 tokens；没有正文、工具调用或工具参数，Provider 以 `finishReason=length` 结束，PurrA 正确提交 `model_output_truncated`。
- 小说分析失败分类器此前没有识别 `model_output_truncated` 和 `tool_call_truncated`，将它们错误降级为 `business_invariant` / `fail_permanent`。因此单元声明的 `maxAttempts=2` 没有得到第二次执行机会。

### 修复契约

1. `model_output_truncated` 和 `tool_call_truncated` 归类为可重试的 `model_output_invalid`；还有剩余单元尝试时，由 PurrA LongTask 新建 Run 重试，不续写旧流。
2. 删除小说分析业务层的 16384 tokens 固定上限。调用使用 PurrA 根据用户显式配置和模型 Profile 解析出的上限，并继续由 Core 结合上下文窗口、Run 预算和 Provider 能力做准入；业务层不再用预估答案长度限制 reasoning 与工具参数共用的 Provider 输出额度。
3. 只有上一单元尝试明确因上述两种截断失败时，下一次新 Run 才加入私有恢复指令，要求只保留有直接证据的必要条目并尽快提交；不擅自改变用户或模型 Profile 的输出上限。
4. 保持用户选择的 reasoning 模式，不静默关闭思考；不改用户输入，不拼接截断 JSON，不公开 reasoning，也不把未提交结果伪造成成功。
5. 其他结构化输出错误仍沿用既有有界重试；认证、余额和协议不兼容错误不得伪装成输出截断重试。
6. 历史失败 Run 和 LongTask 不迁移、不改写；修复只作用于重启后创建或显式恢复的新执行。

## 验证边界

- 模拟 Provider 测试只能证明装配、计划来源、持久化和事件边界。
- 当前监听 18321 的用户后端未由本次修复重启，不能据此判断它已经加载新的失败分类和重试策略。
- 未进行修复后的真实 Provider 重跑前，不能把问题二标记为运行态已修复；确定性测试只能证明分类、重试决策、模型/Profile 上限传递和私有恢复提示正确接线。

## 项目级输出上限审计

- 截至本次只读数据库核验，持久化历史中有 4 个小说分析单元、14 个剧本单元因 `model_output_truncated` 或 `tool_call_truncated` 失败。这不是只发生一次的 Provider 偶发现象。
- 小说分析原先把 16384 直接作为 Provider 单次总输出上限；该数字同时约束 reasoning、正文和工具参数，已在本次删除。
- 普通写作原先使用 `min(模型或用户上限, 32768, context_window / 4)`；剧本原先把 1024、4096、8192、16384 的 Part 产物预算以及 32768 的全局值传成 Provider 单次总输出上限。这些工作流上限均已从 Provider 调用路径移除。
- 剧本失败证据中，多次调用在 4096 或 8192 的工作流上限处结束，且绝大部分额度被 reasoning 消耗；这与小说分析属于同一种“产物预算和 Provider 总输出预算混用”风险。Part 的结构与体量约束仍用于 Artifact 校验和 LongTask 成本估算，不再截断 reasoning、正文与工具参数共用的 Provider 输出。
- Provider 单次上限现在统一来自用户显式配置或模型 Profile。只有当该值不小于模型上下文窗口时，共享运行时解析会预留四分之一上下文、且至少 4096 tokens 给输入，避免声明一个在当前窗口内物理上无法成立的输出额度；这属于上下文可行性约束，不是领域答案长度上限。
- 三类预算已分离：模型或用户允许的单次调用上限、领域产物结构与体量校验、LongTask 的累计输入/输出/reasoning/调用次数预算。PurrA 负责准入和执行运行预算，PurrTypos 只声明产品策略。

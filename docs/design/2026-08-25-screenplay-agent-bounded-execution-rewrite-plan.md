# 剧本 Agent 有界执行单元与提示词契约重写技术方案

> 状态：Phase 0 至 Phase 7 已完成；本地 PurrA Root Run journal 宿主兼容迁移
> 已完成；真实 Provider 十章结构 Candidate 验收通过（2026-08-26）。
>
> 用途：作为后续阶段的实施依据。设计时记录的“当前事实”仍按其原始快照理解；
> 最新实现事实以代码、测试和下述进度记录为准。
>
> 现状快照：2026-08-25，`feat/0.6`，`90e5c46`。工作区存在大量未提交改动，
> 后续实施前必须重新读取相关文件和 `git diff`，不得把本文的行号或快照当成最新事实。

## 实施进度（2026-08-26）

- Phase 1 已完成显式 Part Contract、单 Part 预算、总任务预算与 usage settlement；
- Phase 2 已完成 `readScreenplayTaskDependencies`、直接依赖范围校验和业务正文注入清理；
- PurrA 已支持 Tool JSON Schema `uniqueItems`，应用层已恢复
  `partKeys.uniqueItems=true`；
- Phase 3 已完成 series arc、episode、character arc 三类 index/fragment Durable split；
- `hooks` 已改为宿主确定性投影，不创建模型 Run；
- assembled structure validator 已校验阶段、连续集号、人物弧引用和 hooks 一一对应；
- 十集 scripted 隔离测试、剧本验收、架构边界门禁和完整项目回归已通过；
- Phase 4 生产实现已完成授权叶子章节枚举、逐章 digest Run、固定 12 路 reduction tree、
  五类严格 source analysis section、单章工具范围和 source receipt/sourceRunIds 回归；
- PurrTypos SQLite 宿主已兼容本地 PurrA 新增的 requested Run identity、Run scope、
  Root journal 和 Root-scope 原子预算，Phase 4 完整项目回归通过；
- 原作正文不再进入 source analysis 子 Run 的 user payload，内部 digest Part 不进入最终文档；
- 当前任务的 `max generated units` 按已解析的有限 source scope 精确持久化；尚未得到产品决策的
  跨项目统一章节数上限没有被伪造为隐藏限制；
- Phase 5 已将五个 Creative Brief section 切换为独立严格 Candidate contract，候选与组装
  两层都校验精确字段；`coreCharacters` 上限为 12，`worldRules` 和
  `adaptationRules` 使用 24 项应用安全上限，超限均返回 typed validation failure 而不截断；
- Creative Brief 默认工具 profile 已收窄为精确交付物读取、直接依赖读取和候选写入；书改创建
  只授权 accepted `sourceAnalysis`，正式 revise 额外授权锁定的 `creativeBrief` baseline，
  handler 拒绝其他 role、旧 Revision 和“用任意读取冒充交付物读取”；
- 原创首次创建没有可读 Revision 时持久化显式空 Revision scope，不伪造无意义工具调用；
- Phase 6 已把 sceneList 收紧为按集身份、精确 structure Revision 和当前集读取；draft/review
  的集号与 Revision 都由宿主 scope 校验，draft 场景提交前必须存在成功的
  `getScreenplayEpisodeContext` 操作；metadata 只接受精确三字段对象并读取最后场景依赖；
- review 每个维度的 issue 上限等于当前集唯一 scene ID 数量，validator 超限直接失败且不截断；
- final response 仍由模型基于已校验公开事实生成，所有业务 Part 继续使用独立 contract cap；
- Compiler 已一次性切换到 `recipeVersion` 6，执行 DAG 未改变；
- Phase 7 审计确认不存在活动 recipe v5 Task；历史六个 v5 Task 均已进入终态；
- 本地实际加载 PurrA 0.4.0。真实验收首次启动暴露 PurrTypos SQLite `RunRepository`
  未实现新 `get()`/execution checkpoint 合同；宿主适配器和既有数据库迁移已补齐，未修改 PurrA；
- Phase 7 的首个真实十章结构任务在 `series_arc:index` 连续两次命中 provisional `4096`
  输出上限；该失败样本与保留的 evidence Artifact 继续作为 cap 校准依据；
- 关停日志进一步证明 Operation 最终显示的 `execution_lease_expired` 并非模型首因：恢复后的
  Root 本应再次写入 paused，但旧 transition digest 把“pause → resume → 同原因再次 pause”
  错判为重复命令并触发唯一键冲突，Root 随后才被 orphan recovery 终结。PurrTypos 已让命令
  ID 参与 transition digest，并增加“同一命令重放幂等、恢复后新 pause 可提交”的事故回归；
- 应用层只把 `structure.series_arc_index` cap 校准为 `8192`，并对六类 structure
  index/fragment Part 禁用 reasoning；没有统一抬高所有 Part cap，也没有延长 deadline；
- 后续真实尝试继续暴露并修复了 Root journal 局部迁移冲突、五类 Candidate 工具 scope
  漏项、子 Run 丢失宿主绑定 scope，以及 `invalid_tool_arguments_schema` 错误分类；
- 新正式 recipe v6 Turn 已完整生成结构 Candidate：34/34 Unit 完成，10 集、10 个全剧阶段、
  7 条人物弧和 10 个钩子均通过组装校验；Candidate 未自动接受，既有两个 Project Head 未修改；
- Phase 7 最终回归通过 267 项剧本验收、53 项架构边界、395 项前端测试和
  1643 项后端测试；真实 Provider 验收不再是当前发布阻断；
- 本地 PurrA 0.4.0 尚未通过 `purra.ports` 导出 `RunSnapshot`，宿主暂时从
  `purra.run_state` 导入；发布依赖仍固定为 `purra==0.3.0`。本地验收不能替代 0.4.0
  正式发布和依赖版本对齐。

## 1. 结论

剧本 Agent 的正式生产任务应采用以下边界：

1. Root Planner 只生成用户可见的语义计划，不枚举每个内部 Part，也不决定并行度；
2. 应用层 Resolver 和 Manifest Compiler 根据持久化项目事实生成确定性的有界执行图；
3. 一个模型 Run 只生成一个可以独立校验、独立恢复和独立复用的业务 Part；
4. 已知边界由应用层直接拆分，未知数量先生成有界索引，再由宿主展开子 Part；
5. 业务正文和证据只能由模型通过已授权工具读取，宿主只可直接提供控制元数据、
   权限范围、不可变引用和最终答复所需的已校验公开事实；
6. 提示词、业务校验、工具范围和单元输出预算共同形成边界，不能只依赖提示词；
7. 应用层还必须限制每个正式任务可生成的总 Unit 数和总模型预算，不能用无限小 Run
   替代一个超大 Run；
8. PurrA 继续拥有通用 Run、工具循环、生命周期、持久化、恢复、取消和 delegation；
   本方案不修改 PurrA。

本次重写的重点不是延长模型单次超时，而是让每个 Run 从语义上不再承担超大任务。

## 2. 已核实的当前事实

### 2.1 所有权边界

- `build_screenplay_planning_policy()` 明确规定 Planner 不决定 Revision、重试次数、
  并行度、发布顺序或内部 Recipe/Part；
- `ScreenplayAgentProfile.evaluate()` 解析模型 TaskSpec 后，由
  `compile_screenplay_manifest()` 编译正式 `ExecutionRecipe`；
- `ScreenplayToolCallingService.run_candidate()` 为每个 AI Part 创建真实、持久化的
  PurrA Run；
- Candidate 只有经过业务校验、Artifact finalize 和 Operation finalization 后才会形成
  Candidate Revision；部分完成的 Part 不得提前发布正式 Revision。

因此，执行粒度属于 PurrTypos 应用层，而不是 PurrA 或 Root Planner。

### 2.2 当前模型执行粒度

| Deliverable | 当前模型 Run 粒度 | 当前判断 |
| --- | --- | --- |
| `sourceAnalysis` | 人物、故事、世界、主题、改编风险各一个 Run | 已按五个文档章节拆开，但尚未按原作章节拆分，前三类仍可读取和生成过多内容 |
| `creativeBrief` | 定位、前提、人物、世界、规则各一个 Run | 基本可控，但只有定位和前提有明确字段 |
| `structure` | 全剧主线一个、分集索引一个、每集一个、全部人物弧一个、全部 hooks 一个 | `series_arc`、`character_arcs`、`hooks` 仍是高风险大单元 |
| `sceneList` | 每集一个 Run | 合理 |
| `screenplayDraft` | 每个场景一个 Run，每集另有一个元数据 Run | 合理 |
| `review` | 每集乘以五个审阅维度 | 合理 |
| 最终答复 | 整个 Operation 一个短答复 Run | 合理 |

### 2.3 已复现事故证据

SQLite 中 `run_609d1ffa3eb64cfd` 的事实：

- 绑定单元：`section:structure:series_arc`；
- 错误：`model_invocation_deadline_exceeded`；
- 该 Long Task 在失败时有 8 个初始单元，只完成了 `document:evidence`；
- `episode_plan:index` 尚未开始，因此每集拆分尚未发生；
- Run 总历时约 129 秒；持久化快照中的单次 Provider deadline 是 120 秒；
- 失败的第四次 invocation 在截止前仍持续产出，共持久化 442 个
  `provider.delta_batch`，payload 约 1.51M 字符；它不是无数据的静默等待；
- Root Run 和失败 Part Run 的 `ai_agent_delegations` 数量为 0；
- `delegateToAgents` 当时已在模型工具列表中，但 PurrA 不会按任务重量自动强制模型调用它。

上述事实说明：deadline 正常停止了一个仍在扩张的模型轮次，但没有制造任务过重的问题。

### 2.4 当前根因

1. `_document_section_tool_instruction()` 是多数前半程章节共用的通用提示词；
2. 除 `creativeBrief.positioning` 和 `creativeBrief.premise` 外，通用提示词只要求
   `contentJson` 是任意对象；
3. `_validate_document_section_candidate()` 只检查 section key、标题、正文非空和
   `contentJson` 为对象，不检查章节的业务字段、项数和禁止字段；
4. `writeScreenplayCandidatePart` 的模型可见 Tool Schema 只有
   `{"candidate":{"type":"object"}}`，它是传输信封，不是业务 Part Schema；
5. 所有候选 Run 共用最高 32,768 token 的工作流输出上限，没有按 Part 区分；
6. 大多数文档单元使用 `tool_access="all"`，模型可以读取远多于当前 Part 所需的材料；
7. `dependencySections`、`episodePlanEntry`、`previousSceneTail` 和 `finalSceneTail`
   仍会把前序业务内容直接放入 user payload，没有经过可记录的读取工具；
8. assembled structure 的当前 `_validate_structure()` 只强制校验 `episodes` 的身份、标题和
   摘要，没有校验 series arc、character arcs、hooks 之间的跨 Part 关系。

### 2.5 证据索引

为避免把设计判断写成现状事实，本文的现状结论对应以下当前代码或持久化证据：

| 结论 | 当前证据位置 |
| --- | --- |
| Planner 只拥有公开语义，Application 编译 Recipe | `backend/domains/screenplay_agent/prompts.py`、`backend/application/screenplay_agent_profile.py`、`backend/application/screenplay_manifest_compiler.py` |
| 当前 section/draft/review 粒度与依赖 | `backend/application/screenplay_manifest_compiler.py`、`backend/application/screenplay_task_resolver.py` |
| 当前全部应用层业务提示词、直接内容注入和 validator | `backend/application/screenplay_agent_task_executor.py`、`backend/application/screenplay_checkpoint_planning.py` |
| 候选写工具只暴露通用对象信封 | `backend/domains/screenplay_agent/tools/schemas.py` |
| 当前剧本工作流绝对输出上限为 32,768 | `backend/application/screenplay_model_policy.py` |
| 当前工作区 Provider deadline 配置为 300 秒 | `backend/domains/screenplay_agent/adapter.py` |
| 当前 `.venv` 实际导入本地 PurrA，默认 delegation 为每次/并行最多 3 个、隔离上下文、只读且不可递归 | `.venv/bin/python` 的 `purra.__file__`、`backend/application/agent_composition.py`、`../purra/src/purra/delegation/policy.py` |
| PurrA 已提供 Long Task invocation/input/output/reasoning 总预算和原子 usage 记账 | `../purra/src/purra/long_tasks/contracts.py`、`../purra/src/purra/long_tasks/dispatcher.py` |
| 事故 Run 的 deadline、usage、事件和 Unit 状态 | SQLite 的 `ai_agent_runs`、`ai_agent_run_events`、`ai_agent_long_task_units`、`ai_agent_long_task_runs`、`ai_agent_delegations` |
| 当前步骤标题、思考兜底、连续操作折叠和工具动作名 | `src/components/AgentConversation/AssistantOutput/timeline.ts`、`src/components/AgentConversation/ExecutionLog/grouping.ts`、`src/components/AgentConversation/toolCallLabels.ts`、`backend/domains/screenplay_agent/tools/catalog.py` |

以上是 2026-08-25 快照证据；后续实现必须重新读取，不能依赖本文记录的旧行号。

## 3. 需要排除的错误前提

### 3.1 不是所有提示词都需要重写执行图

每集场景表、每个剧本场景、每集元数据、每集每维度审阅和最终短答复已经有明确边界。
这些步骤只需补齐工具范围、业务校验和输出预算，不应重新设计 DAG。

### 3.2 公开 Planner 不应枚举所有内部小单元

Planner 计划服务于用户理解和语义 checkpoint。把十集、几十个场景全部写成公开 Plan
会造成噪音，并把宿主才能确认的集数、场景 ID 和提交顺序错误交给模型。

公开 Plan 保持少量语义阶段；当前 Schema 允许 1 至 8 步。实际每集、每场、每人物的状态
来自 Durable Unit，但不在公开 Plan 中暴露内部 ID。

### 3.3 子 Agent 不是正式拆分的替代品

当前 `delegateToAgents`：

- 由主模型自行选择；
- 每次最多三个 delegate；
- delegate 使用隔离上下文和只读工具；
- delegate 不能写入正式 Candidate Part；
- delegate 结果仍需主模型综合。

因此，已知的章节、剧集、人物和场景必须由应用层编译为 Durable Unit。Delegation 可用于
临时调查，但不能成为本方案正确性的前提，也不纳入验收条件。

### 3.4 300 秒不是根因修复

当前工作区的 `ScreenplayDomainAdapter.runtime_limits` 已出现 300 秒单次 Provider deadline，
但事故 Run 的持久化快照仍是 120 秒。无论最终安全上限是 120 秒还是 300 秒，只要单元
仍可无限扩张，就仍会在更晚时失败并增加成本。

本方案不以延长 deadline 作为通过条件。deadline 只保留为最后一道运行时安全边界。

### 3.5 运行提示不是公开 Plan 标题

应用层已明确要求模型在调用工具前输出“当前步骤标题”，该 Provider 输出以 commentary
持久化；当前 UI 只在它属于活跃 invocation 且通过公开性校验时显示，没有时固定显示
“正在思考”。它不会拿公开 Plan 步骤或最新工具内部状态猜测文案。连续的 commentary/
工具步骤由执行日志统一收起，剧本工具优先显示 catalog 提供的具体动作名。

本次业务执行图重写必须保留这些行为并做回归测试，但不再发明第二套运行提示来源，也不
新增前端状态机。

## 4. 目标与非目标

### 4.1 目标

- 每个模型 Run 只写一个明确 Part；
- 动态数量使用“有界索引 -> 宿主展开 -> 每项一个 Part”；
- 前半程不再使用任意 `contentJson` 的通用业务契约；
- 所有业务材料通过工具读取并留下 canonical tool/event 记录；
- 每个 Part 拥有独立工具 profile、输出预算和业务 validator；
- 每个交付物拥有总 Unit 准入和 Long Task 总模型预算；
- completed Part 在重试、暂停和恢复后复用，不重新生成；
- Operation 仍只发布一个原子 Candidate Revision；
- 最终答复继续由模型依据已校验公开事实生成，不回退到固定模板。

### 4.2 非目标

- 不修改 PurrA；
- 不改变 Provider SDK 或模型参数协议；
- 不增加新的前端任务状态机；
- 不把 Durable Unit 全部投影成公开 Planner 步骤；
- 不要求每个任务使用 `delegateToAgents`；
- 不迁移或重写历史不可变 Revision；
- 不用无限重试或提高 deadline 掩盖失败；
- 首轮不为每种 Part 创建独立写工具，也不为 Tool Catalog 引入动态 Schema 系统。

## 5. 不可破坏的不变量

1. 一个正式 Turn 仍只有一个 Root Run 和一个 Operation；
2. Run/Long Task 不是第二个业务状态机；
3. 同一正式命令最多发布一个 Candidate Revision；
4. Candidate 发布前必须完成所有 required Part 和最终业务校验；
5. 工具读取必须受 project、task、unit、source scope 和不可变 Revision 约束；
6. 模型不能提交 project/task/unit/source scope 等 host-bound 字段；
7. 取消、暂停、恢复、lease、usage 和 canonical event 顺序继续由 PurrA 契约管理；
8. SSE 断开不取消 Operation；
9. completed Part、Artifact 和 source receipt 在恢复时不得被覆盖；
10. 旧 Revision 永远可读，新执行图不能改变其 content digest；
11. Root Planner 只拥有语义计划，Application 继续拥有业务执行图；
12. 运行时 commentary、工具记录和最终答复都必须来自同一 Root Run tree 的 canonical
    持久化事实；工具记录保留在实际执行它的 Part Run，并通过持久化父子关系归属 Root，
    前端不得合成不存在的模型或工具事件。
13. 单 Part 有界和整个任务有界必须同时成立；超出任务总量时在新增 Provider 调用前失败。

## 6. 通用 Part 契约

每个 AI Part 必须在编译时解析到一个确定的应用层契约。建议使用一个简单、不可变的
`PART_CONTRACTS` 映射，不增加工厂或插件体系。

每项契约至少包含：

```text
contract key
├── system instruction builder
├── candidate validation kind
├── tool profile
├── output token cap
├── reasoning mode
├── allowed dependency kinds
└── semantic validator
```

硬规则：未知 contract key 必须 fail closed，不得回退到通用文档提示词。

所有写入类系统提示词统一包含以下规则：

1. 只处理当前 Part，不重做上游规划；
2. 先通过授权读取工具取得所需业务内容；
3. 不把 descriptor、revisionId、part key 当成正文；
4. 明确列出唯一允许的输出字段；
5. 明确列出禁止字段和禁止跨越的相邻 Part；
6. 明确候选数组最多或恰好包含多少项；
7. 最终必须按当前 Part 的既有提交协议完成；
8. 不输出内部协议、工具参数、Run/Task/Artifact ID 或私有 reasoning。

### 6.1 当前提示词清单与重写决定

下表覆盖当前剧本工作流中所有应用层业务提示词入口。PurrA 仍会在 Provider 请求中加入
通用 Agent 协议、工具 Schema 和 canonical 输出约束，它们不属于本次业务提示词重写。
Evidence 收集、Expansion、Validation 和后续新增的 Projection 是宿主步骤，不发起模型
调用，因此没有模型提示词。

| 当前入口 | 当前承担的步骤 | 处理决定 | 重写后的单 Run 边界 |
| --- | --- | --- | --- |
| `build_screenplay_planning_policy()` | Root 语义规划 | 保留并回归测试 | 只规划用户语义目标和公开步骤，不枚举内部 Part |
| `build_screenplay_public_progress_policy()` | 工具调用前的公开当前步骤标题 | 保留并回归测试 | 只生成无内部 ID 的短标题，不代替工具日志或 reasoning |
| `_document_section_tool_instruction()` | source analysis 五章、creative brief 五章、structure 的 series arc/character arcs/hooks | 删除通用 fallback | 每个合法 role/section 必须解析到下列独立 Part Contract；未知组合调用 Provider 前失败 |
| 新 `source_chapter_digest` 指令 | 当前不存在 | 新增 | 只读一个授权叶子章节，只提交一份紧凑事实 digest |
| 新 `source_digest_reduction` 指令 | 当前不存在 | 新增 | 最多读取 12 个直接依赖 digest，只提交一份同构归并 digest |
| 新五类 `source_analysis_section` 指令 | 当前共用通用文档指令 | 新增 | 每个 Run 只提交 characters/story/world/themes/adaptation risks 中的一章 |
| 新五类 `creative_brief_section` 指令 | 当前共用通用文档指令 | 新增 | 每个 Run 只提交一章及其明确业务字段，不扩写完整简报 |
| 新 `series_arc_index` 指令 | 当前不存在 | 新增 | 只确定 1 至 12 个 phase 的 key/title/objective，不写分集 |
| 新 `series_arc_phase` 指令 | 当前 `series_arc` 一个通用 Run | 新增 | 一个 Run 只写一个冻结身份的 phase，递归禁止 episodes/scenes |
| `_structure_episode_plan_index_tool_instruction()` | 分集轻量索引 | 保留并收紧 | 只写最多 100 集的身份、摘要和来源引用；业务内容改为工具读取 |
| `_structure_episode_plan_fragment_tool_instruction()` | 每集结构 | 保留并收紧 | 只写索引指定的一集；不再直接注入 `episodePlanEntry`/dependency body |
| 新 `character_arcs_index` 指令 | 当前不存在 | 新增 | 只冻结最多 12 个核心人物 key/title，不写完整人物弧 |
| 新 `character_arc_fragment` 指令 | 当前 `character_arcs` 一个通用 Run | 新增 | 一个 Run 只写一个索引人物的人物弧 |
| `_document_section_tool_instruction(..., "hooks")` | 当前生成全剧 hooks | 移除 | 改为宿主从每集已校验 `hook` 字段确定性投影，不调用模型 |
| `_scene_list_fragment_tool_instruction()` | 每集场景表 | 保留并收紧 | 只写当前一集 `scenes[]`，只读指定结构 Revision |
| `_scene_tool_instruction()` | 每个剧本场景 | 保留并收紧 | 只写当前 scene text；前场连续性和原作材料都通过工具读取 |
| `_episode_metadata_tool_instruction()` | 每集标题和连续性摘要 | 保留并收紧 | 只写当前集短元数据；末场内容改为读取直接依赖 Part |
| `_review_dimension_tool_instruction()` | 每集每个审阅维度 | 保留并收紧 | 只审当前集当前维度，issue 数量由宿主 contract 限制 |
| `_final_response_instruction()` | Operation 最终聊天答复 | 保留 | 只依据已校验公开事实生成 2 至 5 句结果总结，不使用固定模板 |
| `_checkpoint_system_instruction()` | Checkpoint 计划修订 | 保留 | 只可在现有 Root 语义和步骤集合内修订未来步骤，不改变业务 scope |

这里的“保留”不等于原样冻结：所有保留项仍需绑定精确工具 profile、业务 validator 和
Part cap。这里的“删除通用 fallback”也不意味着删除统一的候选写入工具，只删除任意
`contentJson` 的业务语义兜底。

### 6.2 新提示词的固定结构

后续实现不允许临时自由拼接长提示词。每个 Part 指令按固定顺序组成：

```text
角色与唯一任务
-> host-bound 当前 Part 身份
-> 必须调用的读取工具与允许的来源
-> 唯一输出 Schema 和 item count
-> 明确禁止的相邻 Part/字段
-> 提交方式
-> 禁止泄露的内部信息
```

例如 `series_arc_phase` 的核心指令必须表达为：只处理 host 绑定的 `phaseKey`；先读取
`series_arc:index` 直接依赖和获准的已接受简报；只提交一个 `seriesArc.phases[]` 项；
key/title 必须与 index 相同；任何位置出现 episodes/scenes 都无效；最后调用统一候选写入
工具。具体 JSON 形状以第 8 节和 Part Contract 为唯一依据，不能在提示词、validator 和
projector 中维护三套不一致定义。

## 7. 依赖内容读取工具

### 7.1 为什么需要新工具

`readScreenplayDeliverable` 只能读取已经接受的 Revision，不能读取同一 Operation 中已经
完成但尚未发布的 Part。当前代码因此把 `dependencySections` 等内容直接放进 prompt。

为了满足“业务信息通过工具读取并记录”，新增应用层工具：

```text
readScreenplayTaskDependencies
```

模型参数只包含当前 prompt 已列出的 `partKeys`；模型不得提交 projectId、taskId、unitId
或任意 Revision ID。

宿主执行时：

1. 从当前 DomainContext 绑定 project/task/unit；
2. 只允许读取当前 Unit 的已完成直接依赖；
3. 每次最多读取 12 个 Part；
4. 返回 `partKey`、`kind`、紧凑 `contentJson`、必要时最多 1,200 字符正文尾部，
   以及 source Run refs；
5. 不返回 Artifact 内部存储路径、其他任务内容或完整长正文；
6. 持久化普通 canonical tool call/result 和来源 receipt；
7. 将依赖 Part 的 source Run refs 传递到当前 Part，最终 Revision 保留传递闭包。

首轮替换以下直接内容注入：

- `dependencySections` -> `dependencyPartKeys`；
- `episodePlanEntry` -> 对应 index Part key；
- `previousSceneTail` -> 前一场 Part key；
- `finalSceneTail` -> 本集最后场景 Part key；
- `previousEpisodeContinuity` 中的业务正文 -> 前一集 validation/metadata Part key。

控制元数据可以继续由宿主直接提供，包括用户指令、目标 role、episode number、scene ID、
授权 chapter ID、revision descriptor 和 dependency part key。它们限定权限和身份，不是供模型
直接使用的业务正文。

## 8. 目标执行图

### 8.1 Root Planner

保持现有语义：

```text
证据理解 -> 创作/审阅 -> 交付
```

允许模型用 1 至 8 个公开步骤表达用户语义，但不要求每集、每场或每人物一个公开步骤。
无需修改 PurrA Planner Schema。

### 8.2 原作分析 `sourceAnalysis`

目标执行图：

```text
document:evidence                         # host-only descriptor
  -> source-analysis:chapter:<chapterId>  # 每个授权叶子章节一个 AI Part
  -> source-analysis:reduce:<level>:<n>   # 仅在上层 fan-in 超过 12 时生成
  -> section:sourceAnalysis:characters
  -> section:sourceAnalysis:story
  -> section:sourceAnalysis:world
  -> section:sourceAnalysis:themes
  -> section:sourceAnalysis:adaptation_risks
  -> document:validation
  -> compose-final-response
```

章节 Part 必须调用 `readSourceChapters`，且只能读取自己的 chapter ID。候选内容只保存紧凑
事实摘要和引用，不复制长原文：

```json
{
  "chapterId": "chapter-id",
  "summary": "本章事件边界",
  "characters": [{"key": "name-or-source-key", "state": "状态变化"}],
  "events": [{"key": "event-key", "summary": "事件", "consequence": "后果"}],
  "worldFacts": [{"key": "fact-key", "summary": "规则或设定"}],
  "themes": ["主题信号"],
  "plotThreads": [{"key": "thread-key", "state": "opened|advanced|resolved"}],
  "adaptationRisks": ["改编风险"]
}
```

当一个综合 Part 需要读取超过 12 个章节摘要时，Compiler 生成固定 fan-in 为 12 的归并树；
归并 Part 使用同一紧凑 Schema，不增加新的业务概念。来源章节不超过 12 个时不生成归并层。

最终五个章节各自只生成其字段：

| section | 必需 `contentJson` 顶层字段 | 禁止 |
| --- | --- | --- |
| `characters` | `characters[]` | 完整逐章故事、剧本人物重设计 |
| `story` | `story.beats[]`、`story.openThreads[]` | 逐字复述原文、剧本分集设计 |
| `world` | `world.rules[]`、`world.locations[]`、`world.factions[]` | 无证据的新设定 |
| `themes` | `themes[]` | 人物档案和逐章摘要 |
| `adaptation_risks` | `adaptationRisks[]` | 直接写改编成稿 |

### 8.3 创作简报 `creativeBrief`

创作简报本身应短小且需要全局一致，不继续拆成每人物一个公开文档 Part。保留五个 Run，
但改为严格字段和严格工具范围：

| section | 必需 `contentJson` 顶层字段 |
| --- | --- |
| `positioning` | `fields.approach`、`fields.format`、`fields.audience`、`fields.tone` |
| `premise` | `fields.premise`、`fields.centralConflict`、`fields.dramaticQuestion` |
| `characters` | `coreCharacters[]`，每项含稳定 `key`、功能、欲望、阻力和变化方向 |
| `world` | `worldRules[]`、`visualIdentity` |
| `adaptation_rules` | `adaptationRules[]`，每项含规则和理由 |

`coreCharacters` 最多 12 个。超过上限时必须返回 typed validation failure，不能静默截断；
用户可通过新的正式修订 Turn 调整“核心人物”范围。

默认只允许读取已接受的 `sourceAnalysis` 和当前 `creativeBrief` 基线 Revision。只有明确证据
缺口时才允许按 source scope 调用短检索工具，不允许默认读取整本原文。

### 8.4 分集结构 `structure`

目标执行图：

```text
document:evidence
  -> series-arc:index
  -> series-arc:phase:<phaseKey>           # 每个叙事阶段一个 AI Part
  -> episode-plan:index
  -> episode-plan:episode:<number>         # 每集一个 AI Part
  -> character-arcs:index
  -> character-arcs:character:<key>        # 每个核心人物一个 AI Part
  -> hooks:projection                      # host-only，从每集 hook 确定性汇总
  -> document:validation
  -> compose-final-response
```

#### 全剧主线索引

`series-arc:index` 只输出 1 至 12 个叙事阶段身份：

```json
{
  "phases": [
    {"key": "phase-1", "title": "阶段标题", "objective": "阶段目标"}
  ]
}
```

不得出现 `episodes`、逐集梗概、场景或对白。

#### 全剧主线阶段

每个 `series-arc:phase:<phaseKey>` 只输出一个阶段：

```json
{
  "seriesArc": {
    "phases": [
      {
        "key": "phase-1",
        "title": "阶段标题",
        "objective": "阶段目标",
        "centralConflict": "阶段冲突",
        "turningPoint": "阶段转折",
        "exitState": "阶段结束状态"
      }
    ]
  }
}
```

Validator 必须递归拒绝 `episodes`、`scenes` 和逐集对象。阶段身份必须与 index 完全一致。

#### 分集索引与每集结构

保留现有“轻量 index -> 每集动态 Part”设计：

- index 只包含 number、id、title、summary 和 source chapter refs；
- index 最多 100 集，沿用当前产品上限；
- 每集 Part 的 `episodes` 必须且只能有一项；
- 每集身份必须与 index 一致；
- 每集 Part 可以包含 objective、conflict、turn 和 hook；
- 每集 Part 不得生成场景列表或剧本正文。

#### 人物弧

`character-arcs:index` 从已接受创作简报和分集结构读取最多 12 个核心人物身份。每个人物
Part 只输出一项：

```json
{
  "characterArcs": [
    {
      "key": "character-key",
      "startState": "起点",
      "desire": "核心欲望",
      "turningEpisodes": ["ep01"],
      "endState": "终点"
    }
  ]
}
```

#### Hooks

首轮不删除 `hooks` section，避免改变已有 Revision 结构和测试契约；同时不再启动一个模型
Run 重复总结全部集。`hooks:projection` 由宿主从每集结构的 `hook` 字段确定性汇总：

```json
{
  "hooks": [
    {"episodeId": "ep01", "hook": "集末钩子"}
  ]
}
```

如果未来产品明确需要“跨集埋设/回收关系”，应另立经过评审的 `hookThreads` 需求；本方案
不在没有消费方证据时扩展该模型。

### 8.5 场景表 `sceneList`

保留每集一个 Run。每个 Part：

- 只读取当前集结构；
- `scenes[]` 中所有 `episodeNumber` 必须等于当前集；
- scene ID 必须全局唯一；
- 不生成剧本正文；
- 不读取其他集正文；
- 使用 section-specific validator，不使用通用 document validator。

### 8.6 剧本正文 `screenplayDraft`

保留每场一个 Run和每集一个元数据 Run：

- 场景 Run 只生成当前 scene text；
- 读取已接受场景表、必要原作证据和前序 in-progress Part 时都调用工具；
- 不再把 `previousSceneTail` 直接注入 user payload；
- 元数据 Run 通过 `readScreenplayTaskDependencies` 读取最后场景，只生成标题和连续性摘要；
- 当前场景 Run 继续关闭 reasoning，除非真实 Provider 验收证明该策略降低质量；
- 同一集场景保持有序依赖，下一集继续依赖上一集 validation，避免连续性竞态。

### 8.7 审阅 `review`

保留每集乘以五维度的执行图：

- 每个 Run 只能调用 `getScreenplayEpisodeContext` 读取指定不可变 Draft Revision；
- 每个 issue 只能引用当前集 scene ID；
- issue 数量上限由当前 scene count 推导并在 validator 中确定，不使用无限数组；
- 五个维度仍可在同一集并行，最终 verdict 由宿主确定性聚合；
- 执行错误不得写成审阅问题。

### 8.8 最终答复与检查点 Planner

保持现状：

- 最终答复只接收已校验的公开完成事实，用 2 至 5 句自然语言回应用户；
- 最终答复不读取或复述正文，不声称 Candidate 已被采纳；
- 检查点 Planner 只接收控制摘要和 receipts，不能改变 scope、deliverable、base Revision、
  completed Part 或既有 Artifact；
- 这两类输入属于已有执行上下文或已校验公开事实，不是外部业务证据，不需要再通过工具读取。

## 9. 工具 Profile

扩展当前 `tool_access`，使用稳定的应用层 profile 名称映射精确工具集合；不允许单元默认
回落到 `all`。

| Profile | 允许的主要工具 |
| --- | --- |
| `root_planning_answer` | 当前项目内的精确只读交付物/source 工具；不允许 candidate write |
| `source_chapter_digest` | `readSourceChapters`、`writeScreenplayCandidatePart` |
| `source_digest_reduction` | `readScreenplayTaskDependencies`、`writeScreenplayCandidatePart` |
| `source_analysis_section` | dependency read、必要的短 source query、candidate write |
| `creative_brief_section` | `readScreenplayDeliverable`、dependency read、candidate write |
| `series_arc_index` | accepted analysis/brief reads、candidate write |
| `series_arc_phase` | dependency read、accepted analysis/brief reads、candidate write |
| `episode_plan_index` | accepted analysis/brief、source outline、candidate write |
| `episode_plan_fragment` | dependency read、限定 source chapter reads、candidate write |
| `character_arcs_index` | accepted brief/structure identity reads、candidate write |
| `character_arc_fragment` | dependency read、accepted brief、candidate write |
| `scene_list_episode` | accepted structure read、candidate write |
| `draft_scene` | episode context、dependency read、限定 source reads；场景正文由现有 host capture 提交 |
| `episode_metadata` | dependency read、candidate write |
| `review_dimension` | episode context、candidate write |
| `final_response` | 无工具，只接收 host 提供的已校验公开事实 |
| `checkpoint_plan` | 无工具，只接收既有计划、控制摘要和 receipts |

除工具名称外，handler/scope validator 还必须限制 `readScreenplayDeliverable.role` 和 Revision
归属，不能仅靠提示词要求模型选择正确 role。

PurrA 注入的 `delegateToAgents` 可以继续存在，但任何正式 Part 的完成都不能依赖它被调用。

## 10. 输出、任务总量预算与 reasoning

当前 32,768 token 只保留为剧本工作流绝对上限。每个 Part Contract 必须提供更小的
`output_token_cap`，有效值为：

```text
min(provider/user resolved limit, screenplay global cap, part contract cap)
```

不在本文直接冻结每类 token 数字，原因是 DeepSeek 等模型可能把 reasoning 计入输出额度，
未经真实样本校准的固定数字会把正常推理误判为截断。

实施 Phase 0 必须从本地已完成 Run 和一组 scripted fixtures 统计：

- candidate JSON token；
- content text token；
- reasoning token（仅使用 Provider 已报告的确定数据）；
- 工具轮次和 input token；
- 超时/截断位置。

然后为每个 contract 设定显式 cap。Phase 0 只能对当前已有样本的 Part 冻结实测上界；尚未
实现的索引、fragment、reduction、scene draft 和 review contract 先按 Schema 与当前相邻
样本给出保守的 provisional cap，并在对应 Phase 的 scripted test、Phase 7 的真实 Provider
验证后校准。不得把尚无样本的估算值写成已验证事实。验收要求是：任何业务 Part 都不能仅
继承 32,768，metadata、index、review 和 final response 的 cap 必须显著小于正文场景 cap。

不得新增与 Schema 无关的统一字符硬上限。边界优先由 item count、字段 Schema、Part
粒度和 output token cap 表达。

仅有 Part cap 仍不够。应用层还必须为每类 deliverable 定义 Task Contract，至少包含：

```text
max generated units
max invocation attempts
max input tokens
max output tokens
max reasoning tokens
```

其中模型调用总预算使用 PurrA 已有 `LongTaskBudgetLimits` 持久化和执行；PurrTypos 只负责
按 deliverable/scope 计算并传入它，不在应用层复制预算记账。PurrA 已提供幂等的
`LongTaskRepository.record_usage(task_id, run_id, usage, expected_revision)`，但宿主必须在
每个 Part Run 结算后、Unit 进入终态和下一个 Unit 获得执行资格前，将该 Run 的持久化 usage
恰好汇入一次。不能只传入 limits 而遗漏 usage settlement，否则任务总预算不会生效。
`max generated units` 属于业务执行图准入：静态图在 dispatch 前校验，index/reduction 等
动态 Expansion 在写入子 Unit 前校验预计总量。

Part `output_token_cap` 是单次 invocation 上限，不是整个 Part Run 的累计额度。Task Contract
计算 `max output tokens` 和 `max reasoning tokens` 时必须结合每个 Part 的 invocation attempt
额度；不能只把每个 Part cap 相加一次，否则正常的“读取工具 -> 写入 Candidate”多轮 Run
会被任务总预算过早截断。

本文不先写死总量和 token 数字。Phase 0 根据当前 source scope、剧集、场景、审阅维度、
实际 usage 和可接受成本建立已实现路径的基线与新 contract 的 provisional cap。依赖尚未实现
的动态 Unit 数量只能先定义计算公式和 scope 上界，待相应 Phase 生成真实 manifest 后冻结；
不能在 Phase 0 伪造经验样本。若授权章节或预计 Unit 数超限，返回稳定的 typed failure
（例如 `screenplay_task_scope_too_large`），要求用户用新的正式 Turn 缩小范围；不得静默
截断、自动跨 Operation 分批，也不得把余下内容重新合并进一个大 Run。

Reasoning 策略：

- 继续对场景正文和短元数据使用当前配置的 disabled 策略；
- source synthesis、series arc、episode plan 和 character arc 保留模型默认 reasoning；
- 是否进一步关闭 reasoning 必须由真实 Provider 质量与稳定性对照决定，不能为了缩短时间
  直接全局关闭。

## 11. 业务校验与提交

首轮保留 `writeScreenplayCandidatePart(candidate: object)` 作为统一传输信封，不为每个 Part
创建一个新工具。业务边界由 host-bound `candidate_validation_contract` 和 Candidate
completion projector 强制执行。

必须新增或收紧以下 validator：

- source chapter digest；
- source reduction digest；
- 五类 source analysis section；
- 五类 creative brief section；
- series arc index；
- series arc phase；
- episode index 和 episode fragment；
- character arc index 和 character fragment；
- deterministic hooks projection；
- scene list episode；
- draft scene；
- episode metadata；
- review dimension；
- assembled document cross-Part validation。

每个 Part 的 candidate validator 必须在该 Part Artifact finalize 前执行；assembled
cross-Part validator 在全部所需 Part Artifact 完成后、`document:validation` Unit 完成前执行。
任一层失败都保留诊断事实，但不得成为 completed validation Unit、Candidate Revision 或 Head。

最终结构文档至少必须满足：

```text
seriesArc.phases 非空且身份唯一
episodes 非空、集号连续、ID/标题唯一
characterArcs 的人物 key 来自 character index
hooks 与 episodes 一一对应
不存在任何 seriesArc phase 内嵌 episodes/scenes
```

## 12. 持久化、恢复和版本兼容

### 12.1 Recipe 版本

新 Compiler 将 `recipeVersion` 从 5 提升为 6。新 Unit metadata 必须保存 part contract key，
使恢复时不会按当前代码猜测旧 Run 的提示词或业务 Schema。

### 12.2 旧活动任务

实施前查询全部 queued/running/paused 的 `screenplay.*` Long Task：

- 如果不存在活动 v5 Task，不增加永久 v5 执行 fallback；
- 如果存在，不能用 v6 executor 静默继续；应先让旧后端完成，或通过现有应用取消接口由用户
  明确取消，再切换；
- failed/canceled/done 历史 Task 和 Revision 保持只读，不迁移、不删除；
- 禁止直接修改 SQLite 终态来规避兼容问题。

### 12.3 Part 和来源传递

- 每个完成 Unit 继续写入 host Part Artifact；
- reduction/fragment Part 可以是内部 Artifact，但不直接发布为 Revision Part；
- assembled Candidate 只包含交付物需要的最终 section/episode/scene/review parts；
- `sourceRunIds` 必须包含直接与传递依赖的去重闭包；
- resume 复用 completed Unit 和 Artifact，不重新调用模型；
- split/reduction 的 source key 和 semantic key 必须稳定，重复调度不会创建重复 Part。

本方案不需要数据库 Schema migration；如果实现过程中发现必须新增持久字段，应暂停当前
Phase，单独评审 migration、回滚和旧数据读取，不能临时塞入 metadata 后继续。

## 13. 实施阶段

Phase 0 至 Phase 6 是同一开发分支上的可验证切片，不是六次线上 rollout。期间不得重启
当前生产式本地后端，也不得让中间代码接收新的正式 screenplay Task；需要进程级验证时使用
隔离测试库和独立测试进程。只有 Phase 1 至 Phase 6 全部完成并通过自动化测试后，才把
最终 Compiler 标记为稳定的 recipe v6，并进入 Phase 7 的切换验收。这样避免同一 recipe
version 在持久化数据中对应多套提示词、工具或 DAG 语义。

### Phase 0：基线与事故回放

只增加测试和测量，不改变生产行为：

- 固化 `run_609d1ffa3eb64cfd` 的根因特征，不复制私有 reasoning；
- 增加 `series_arc` 越界包含 episodes 的失败 fixture；
- 固化当前 sceneList 每集、draft 每场、review 每集每维度的正确拆分；
- 查询已有 Part 输出分布，冻结实测基线并为新 contract 记录 provisional cap；
- 统计当前可观测交付物的 Unit 数、模型尝试和 token 总量，定义 Task Contract 公式及待后续
  Phase 校准的 scope 上界；
- 审计 Part Run usage 是否实际汇入 Long Task，而不只检查 PurrA 是否存在预算接口；
- 审计活动 recipe v5 Task；
- 记录当前 PurrA 实际导入路径；
- 通过后才能开始 Phase 1。

### Phase 1：Part Contract 和 fail-closed 路由

- 建立最小 `PART_CONTRACTS` 映射；
- 移除通用 document prompt fallback；
- 为仍存在于开发中间态的 `series_arc`、`character_arcs` 和 `hooks` 旧单元建立显式临时
  contract，Phase 3 删除；它们不得成为 v6 最终契约或长期兼容 fallback；
- 将 candidate validation kind、tool profile、output cap 和 reasoning mode 绑定到 contract；
- 使用现有 PurrA `record_usage` 接口接通 Part Run 到 Long Task 的幂等 usage settlement，并
  证明成功、失败和重试 Run 都不会漏记或重复记账；
- 将 deliverable/scope 绑定到总 Unit 准入和 PurrA `LongTaskBudgetLimits`；
- 未知 role/section/unit kind 在 Provider 调用前失败；
- 不改变当前执行图。

### Phase 2：依赖读取工具与上下文清理

- 新增 `readScreenplayTaskDependencies`；
- 绑定直接依赖 allowlist 和 max 12；
- 替换 dependency body、episode entry 和 scene tail 直接注入；
- 增加 tool receipt、越权、跨项目、跨任务和超量读取测试；
- 验证 transitive source Run refs。

### Phase 3：Structure 执行图重写

- 实现 series arc index/phase split；
- 保留并收紧 episode index/fragment split；
- 实现 character index/fragment split；
- 将 hooks 改为 host-only deterministic projection；
- 收紧 assembled structure validator；
- 使用 scripted gateway 和隔离测试进程复现十集任务；真实 Provider 完整验收留到 Phase 7。

### Phase 4：Source Analysis 逐章证据和归并

- [x] Resolver 只根据授权 source scope 枚举叶子 chapter ID；
- [x] 每章一个 digest Run；
- [x] fan-in 超过 12 时生成固定 12 路 reduction tree；
- [x] 五个最终 section 只读取 digest Part；
- [x] 验证原文正文不进入 planner、user payload 或最终聊天输出；
- [x] 验证 source receipts 和 sourceRunIds 完整。

实现说明：授权章节数在 Resolver 阶段已经是持久化、有限事实，因此本阶段使用 Compiler
静态生成逐章与归并 Unit，不复制 Structure 的动态 Expansion。`N <= 12` 不创建 reduction；
`N > 12` 时每层按顺序分组，每个归并节点直接依赖最多 12 个节点，直到最终 frontier 不超过
12。内部 digest/reduction Part 只参与执行和恢复，不参与最终 sourceAnalysis 文档组装。

本阶段没有擅自增加统一的“最多 N 章”产品限制。当前每个任务仍会按实际授权叶子章节数
精确计算并持久化静态 Unit 总数和模型预算，所以单次任务执行图是有限的；但跨项目统一的
成本/章节准入上限仍是产品策略变量，不能把未经确认的数字写成已验证业务规则。

#### Phase 4 宿主兼容迁移（2026-08-26）

当前 `.venv` 实际导入 `/Users/liuyubin/Lybrand_project/purra/src/purra`。该本地 PurrA
工作区除 `uniqueItems` 外还包含尚未由本方案消费的递归 Agent Tree 契约变化：

- `RunCreateParams` 新增 `requested_run_id`、`root_run_id`、`agent_id` 和
  `parent_run_id`；
- `AgentOutputEvent` 新增 Root/Agent/Parent 归属和 `root_sequence`；
- `AgentOutputRepository` 新增 `list_root_events()`；
- 内存参考适配器已把模型调用、token 和 Provider 输出预算改为 Root scope 原子累计。

最初 PurrTypos SQLite 宿主没有持久化上述 Run lineage 和 Root journal sequence，且
`SqliteRunRepository` 没有兑现 `requested_run_id`，完整检查因此在共享宿主适配测试中失败。
已完成以下独立兼容迁移：

- `ai_agent_runs` 持久化并冻结 Root/Agent/Parent identity 与 Agent Tree lease receipt；
- 历史 Root Run 回填为自有 scope，已有合法 Child lineage 保留，旧 delegation/role/depth
  列继续删除；
- `ai_agent_run_events` 持久化 Root/Agent/Parent identity、source key 和 Root 单调序列，
  历史 canonical event 按持久化顺序确定性回填；
- `requested_run_id` 冲突返回稳定的 `run_identity_conflict`，跨 Root parent 关系 fail closed；
- model attempt、Provider token usage、Provider output event/bytes 均在同一 SQLite 写事务内
  按 Root scope 原子准入，同时保留每个 Run 的计数投影；
- `list_root_events()` 只接受 Root Run，并按 `root_sequence` 游标回放唯一 canonical journal。

普通剧本 Durable Part Run 不是 PurrA Agent Tree Child，仍保持独立 Root scope 和既有
Long Task 预算；只有通过 `root_run_id`、`parent_run_id` 创建的真实 Child Run 才共享 Root
authority。共享宿主 conformance、251 项剧本验收、395 项前端测试和 1611 项后端测试通过，
可以进入 Phase 5。

### Phase 5：Creative Brief 契约收紧

- [x] 保留五个 section Run；
- [x] 实现明确 Schema 和核心人物上限；
- [x] 默认只读取 accepted sourceAnalysis；
- [x] revise 时只读取锁定 baseline Revision；
- [x] 不额外拆分本来就应短小的定位、前提和改编规则。

实现说明：五个 section 仍由 Compiler 静态生成且最多四路并行，没有引入 index/fragment
Expansion。`positioning`、`premise`、`characters`、`world` 和 `adaptation_rules` 分别使用
精确顶层及嵌套键；人物必须有稳定 key、姓名、叙事功能、欲望、阻力和变化方向。人物最多
12 个，世界规则与改编规则最多各 24 项，所有数组都要求非空且唯一，validator 不静默纠正
字段或截断条目。最终组装后再次校验完整 `creative_brief` 文档，避免合法 section 在合并时
形成不合法整体。

Resolver 不再把项目全部 Head 放入 Creative Brief 的 `sourceRevisionRefs`：书改创建只绑定
accepted `sourceAnalysis`，revise 再绑定当前锁定 baseline。Child Run 的
`deliverableRevisionScope` 进入宿主 Tool Data Contract；`readScreenplayDeliverable` 省略
Revision 时也会被改写为绑定值，显式提交其他 role/Revision 会在 query 边界失败。存在绑定
证据时，候选写入必须看到本 Run 成功的 `readScreenplayDeliverable`，dependency read 或其他
读取不能替代。原创首次创建使用显式空 scope，允许只依据当前用户输入和既有对话上下文生成。

### Phase 6：后半程收紧但不改 DAG

- [x] sceneList 使用 episode-specific contract；
- [x] draft/metadata 通过依赖工具读取 in-progress Part；
- [x] review issue 上限由当前 scene count 推导；
- [x] final response 继续使用模型生成；
- [x] 确认所有 Part 使用 contract cap 而非全局 cap；
- [x] 回归当前步骤标题、“正在思考”兜底、连续操作折叠和工具动作名，不改变其事实来源；
- [x] 在 Phase 1 至 Phase 6 的目标语义全部就绪后，将 Compiler `recipeVersion` 一次性提升为 6。

实现说明：sceneList 的每个 Unit 现在显式持久化 `episodeNumber`，其工具 profile 仅包含
`readScreenplayDeliverable` 和候选写入；Resolver 只绑定 accepted structure Revision，query
会把省略的 Revision 和集号改写为宿主绑定值，并且只返回 structure 中的当前集。场景字段
使用精确 Schema，分集 validator 限制所有 `episodeNumber` 等于当前集，组装 validator 再校验
全局 scene ID 唯一。

Draft 和 review 的 evidence descriptor 不再携带所有 Head，只绑定当前 sceneList、可选 baseline
Draft 或被审阅 Draft。Child context 使用独立的 `boundEpisodeNumber` 宿主字段，避免与工具参数
`episodeNumber` 的 model-owned 归属冲突；`ScreenplayToolCallingService` 重建 Candidate context 时
必须原样保留该集号和 `deliverableRevisionScope`。Draft scene 的 host capture 只有在本 Run 已成功
调用 `getScreenplayEpisodeContext` 后才能提交，前序 in-progress scene 仍通过
`readScreenplayTaskDependencies` 读取；episode metadata 只接受 `episodeNumber`、`title` 和
`continuitySummary`。

Review 的单维度 issue 数量上限确定为当前集唯一 scene ID 数量，即每个维度每个 scene 最多一个
问题槽位；超限、额外字段或跨集 scene ID 均返回 validation failure，不做静默截断。最终答复和
前端过程呈现没有改变事实来源，本阶段只增加回归验证。自动化门禁通过后 Compiler 版本从 5
提升为 6；Phase 7 的真实 Provider 验收与运行中旧任务切换仍保持独立。

### Phase 7：完整回归与发布前验收

- 运行目标测试、剧本验收、架构门禁和后端完整测试；
- 部署前检查活动 recipe v5 Task；无 reload 的后端需要重启以加载代码；
- 使用真实 Provider 手工执行一个十章改编到结构 Candidate 的完整流程；
- 检查 Run/Unit/Artifact/Revision/source receipt/canonical output；
- 不自动接受 Candidate，不修改用户现有 Head。

#### 2026-08-26 执行记录

第一轮真实验收结论是“自动化门禁通过、真实 Provider 验收失败”。该失败记录保留如下：

1. 活动任务审计没有发现 recipe v5 的 queued/running/paused Task；后端按当前工作区重启；
2. 首次真实 Turn 在 Planner 创建 Run 前被本地 PurrA 0.4.0 拒绝，错误为宿主
   `RunRepository` 缺少新增的 canonical `get()` 合同。PurrTypos 补齐 RunSnapshot、Plan、
   TaskSpec 和 execution checkpoint 持久化后，相关目标测试及完整回归通过；
3. 修复后新建 recipe v6 十章结构任务。`document:evidence` Unit 正常完成，随后
   `section:structure:series_arc:index` 第一次在第二轮模型输出中用满 4096 token，
   `finishReason=length`，产生 `tool_call_truncated`；
4. 仅进行一次受控恢复。相同 Unit 的新 Run 再次在第二轮用满 4096 token，
   `finishReason=length`，本次 4096 token 全部为 reasoning，产生
   `model_output_truncated`。因此停止继续恢复；
5. 第二次暂停投影又暴露 PurrTypos Operation transition 的幂等键错误：恢复前后的 pause
   拥有相同业务错误，但属于不同生命周期事件；旧 digest 未包含 command ID，命中
   `(operation_id, command_type, request_digest)` 唯一约束，导致 Root product projection 回滚，
   最后由 orphan recovery 记录成 `execution_lease_expired`。该应用层问题已修复并增加回归；
6. 两次尝试合计四次模型 invocation，任务累计 76,516 input token、9,475 output token、
   8,651 reasoning token。这个样本证明 4096 是该模型执行此 index contract 的无效
   provisional cap；它不能证明统一提高所有 Part cap 或全局关闭 reasoning 是正确方案；
7. 历史任务最终失败并取消九个依赖 Unit；只保留一个 finalized evidence Artifact。结构 Revision
   数量仍为零，项目总 Revision 仍为原有两个，两个 Head 的 Revision ID 均未变化；
8. 自动化结果：`test:screenplay-acceptance` 267 passed、
   `check:agent-refactor-boundaries` 53 passed、前端 395 passed、后端 1635 passed。

该样本只证明 `structure.series_arc_index` 的 provisional `4096` cap 不适配当前真实模型，
不能据此统一提高所有 Part cap、统一关闭全项目 reasoning，或把 deadline 延长当成修复。

#### 2026-08-26 校准与最终复验

在保留上述事故证据的前提下，应用层完成了最小范围校准，并以一个全新的正式 Turn 重新执行：

1. 仅将 `structure.series_arc_index` cap 从 `4096` 调整为 `8192`；真实
   `model.call_recorded` 记录同时显示 `maxOutputTokens=8192`、`reasoningMode=disabled`。
   该索引 Run `run_66e9ae78261047f9` 三次调用累计 74,572 input token、2,130 output token、
   0 reasoning token，最终完成，没有再次出现 `finishReason=length`；
2. 六类 structure Part（series index/phase、episode index/fragment、character index/fragment）
   的合同 reasoning 均改为 disabled。原因不是降低质量的先验假设，而是后续真实恢复样本显示
   phase Run 会把 8192 输出预算大量消耗在 reasoning；结构化 Part 的可校验正文必须优先获得
   有界输出预算。其他交付物合同没有被全局改写；
3. Root journal 迁移改为只给缺失 `root_sequence` 的历史行续排到当前 Root 最大序号之后，
   不再重编号已有 canonical cursor；Operation transition digest 继续包含 command ID，保证
   恢复后的新 pause 与旧 pause 可区分；
4. Candidate 写入工具补齐 `creative_brief_section`、`structure_series_arc_index`、
   `structure_series_arc_phase`、`structure_character_arcs_index` 和
   `structure_character_arc_fragment` 五类 scope；子 Run ExecutionState 同时保留
   `dependencyPartKeys`、`deliverableRevisionScope` 和 `boundEpisodeNumber`，由宿主继续执行
   精确依赖、Revision 和集号约束；
5. `invalid_tool_arguments_schema` 归入 `MODEL_OUTPUT_INVALID`，允许同一有界 Unit 按既有上限
   重试；它不再被误报为业务不变量错误。最终 episode 1 正是一次 schema 失败后由第二个独立
   Run 成功完成，验证了该分类而不是无限重试；
6. 最终 Turn `spaturn_e1c41080346742769309620ff69108f3`、Operation
   `spaop_c05842765506403a8d2336c2a8f141fc` 和 Task
   `longtask_b674b03571e94f8cb8f9edd57b920162` 分别进入 `completed`、`succeeded`、
   `completed`。recipe v6 共 34 个 Unit，34 完成、0 失败；三个宿主 expansion Unit 以
   `expanded` 终态计入完成；
7. 任务生成并 finalise 30 个 `candidate_part` Artifact 和 34 个 `manifest_part` Artifact。
   最终结构 Revision `sprev_a0d230fbc09b429b942f8d64f377200e` 包含 11 个持久化 Part：
   一个 60,564 字符的主文档和第 1 至 10 集十个 episode Part。主文档 schema v1 包含
   10 个 series phase、10 集、7 条 character arc 和 10 个 hook；每集均具备 id、title、
   summary、objective、conflict、turn 和 hook；
8. Source receipt 记录覆盖 10 个不同 chapter Revision 和 10 个不同 outline Revision；因有界
   重试，共有 14 条 full chapter read receipt 和 10 条 referenced outline receipt。Revision 的
   直接上游 input 是已接受的 creative brief，summary 还记录了 source analysis 与 creative
   brief 两个 `derivedFromIds`，符合“只绑定直接前置 Head、来源引用另行持久化”的现有模型；
9. 最终回复由独立模型 Run `run_592bc6ed682d4653` 生成：1 次模型调用、409 input token、
   69 output token、0 reasoning token，并持久化 19 批 Provider delta；Turn 的 106 字符
   `assistant_content` 与 Run 的 `final_response` 一致，不是应用层预排版静态结果；
10. Candidate finalization receipt 为 `spafinal_d4c533e84d842d133a8d02dac956e84b`。
    项目现在有三个 Revision、其中一个 structure Candidate；Head 仍只有原有 accepted
    `sourceAnalysis` 与 `creativeBrief`，因此验收没有越权接受 Candidate；
11. 自动化门禁最终结果为 `test:screenplay-acceptance` 267 passed、
    `check:agent-refactor-boundaries` 53 passed、前端 395 passed、后端 1643 passed。
    `npm run check:agent-refactor` 全部通过。

上述真实 Provider 证据解除本方案 Phase 7 的结构生成验收阻断。它只证明本次授权项目、当前
模型配置和 recipe v6 合同的完整路径通过；PurrA 正式发布版本与应用依赖版本对齐仍是独立发布项。

各 Phase 必须独立可提交、可回滚、可验收。上一 Phase 未通过不得把失败隐藏到下一 Phase。

## 14. 预计修改范围

生产代码优先限制在以下现有边界：

- `backend/application/screenplay_manifest_compiler.py`
- `backend/application/screenplay_task_resolver.py`
- `backend/application/screenplay_agent_profile.py`
- `backend/application/screenplay_agent_task_executor.py`
- `backend/application/screenplay_tool_calling.py`
- `backend/application/screenplay_model_policy.py`
- `backend/domains/screenplay_agent/agent_context.py`
- `backend/domains/screenplay_agent/tools/catalog.py`
- `backend/domains/screenplay_agent/tools/schemas.py`
- `backend/domains/screenplay_agent/candidate_projection.py`
- `backend/infrastructure/screenplay/tools/query.py`
- `backend/infrastructure/screenplay/tools/tool_catalog.py`
- `backend/infrastructure/screenplay/tools/candidate_artifact.py`

主要测试：

- `backend/tests/test_screenplay_agent_rewrite.py`
- `backend/tests/test_screenplay_agent_durable_service.py`
- `backend/tests/test_screenplay_tool_catalog.py`
- `backend/tests/test_application_planning_constraints.py`
- `backend/tests/test_screenplay_agent_routes.py`
- `backend/tests/test_screenplay_v2_persistence_boundaries.py`

如果实施需要修改 `purra`、前端 reducer、Conversation Snapshot Schema 或数据库 Schema，
说明方案边界发生变化，必须暂停并重新评审，不得顺手扩展。

## 15. 测试矩阵

### 15.1 确定性合同测试

- Root Planner 公开步骤不包含 Recipe/Part ID；
- 每个 role/section 解析到唯一 Part Contract；
- 未知 contract 在模型调用前失败；
- tool profile 只暴露允许工具；
- dependency tool 只能读取直接 completed dependencies；
- 跨 project/task/unit、未完成 Part 和第 13 个 dependency 被拒绝；
- business body 不再直接出现在 child user payload；
- 每个 contract 拥有显式 output cap；
- no contract cap 大于 screenplay global cap；
- 静态或动态生成第一个超量 Unit 前返回 typed failure；
- 每个正式任务都持久化非空的 Long Task 总模型预算。

### 15.2 执行图测试

- sourceAnalysis：每章一个 digest，fan-in 不超过 12；
- creativeBrief：保持五个 compact section；
- structure：每 phase、每 episode、每 character 各一个 Unit；
- `seriesArc` 中出现 `episodes` 或 `scenes` 时失败；
- episode fragment 包含两集时失败；
- character fragment 包含两个人物时失败；
- hooks projection 不创建模型 Run；
- sceneList 每集一个 Unit；
- draft 每场一个 Unit；
- review 每集五个维度且只引用本集 scene IDs。
- 活跃 invocation 有安全模型标题时显示该标题，无标题时只显示“正在思考”；
- 连续 commentary/工具步骤保持单组折叠，剧本工具显示具体 catalog 动作名。

### 15.3 生命周期和持久化测试

- completed fragment 在 retry/resume 后不重复模型调用；
- 一个 fragment 失败不删除其他 completed Artifact；
- Operation 未全部完成时不发布 Candidate Revision；
- 重复 command 不产生重复 Unit/Artifact/Revision；
- cancel 后无 running/claimed Unit 或 delegation；
- SSE 断开后任务继续并可 replay；
- sourceRunIds 包含传递依赖；
- final response 只有在 Candidate 原子完成后产生。

### 15.4 建议命令

目标阶段先运行：

```bash
.venv/bin/python -m pytest \
  backend/tests/test_screenplay_agent_rewrite.py \
  backend/tests/test_screenplay_agent_durable_service.py \
  backend/tests/test_screenplay_tool_catalog.py \
  backend/tests/test_application_planning_constraints.py -q
```

阶段完成后运行：

```bash
npm run test:screenplay-acceptance
npm run check:agent-refactor-boundaries
npm run check
git diff --check
```

真实 Provider 验收不能由 scripted/fake gateway 代替，也不能被描述为自动化测试通过。

## 16. 验收标准

### 16.1 业务正确性

- 十集结构任务不再由任何一个 Run 同时生成十集详细结构；
- series arc、episode、character arc、scene list、draft scene、review dimension 均有明确边界；
- 最终结构 Candidate 保持完整且可以被现有 Revision/Accept 流程消费；
- 老 Revision 保持可读且 digest 不变；
- 最终答复由模型基于已校验公开事实生成。

### 16.2 上下文与工具

- 除用户输入、权限/身份控制元数据、不可变引用和已校验公开事实外，模型使用的业务内容
  都来自本轮已记录工具结果；
- 不再直接注入 dependency section body、episode plan entry 或 scene tail；
- 每次来源读取都能追溯到 source receipt 和 Run；
- 工具不能越权读取其他项目、任务、Unit 或 Revision。

### 16.3 运行安全

- 每个 Part 使用显式 contract cap；
- 没有以提高 deadline 作为通过测试的条件；
- 一个 Provider deadline 只失败当前最小 Unit，不丢失已完成 Part；
- 总 Unit 或 Long Task 总模型预算耗尽时停止新增调用并保留已完成 Part；
- resume 不重复已完成的模型工作；
- 真实十章结构手工验收不再复现 `series_arc` 无限逐集展开。

### 16.4 架构边界

- PurrA 无业务改动；
- Root Planner 不拥有内部拆分和并行度；
- Application Manifest Compiler 是唯一执行粒度权威；
- Domain/Application 校验是 Candidate 业务正确性的权威；
- 前端仍只投影 canonical Run/Operation 事实，不新增业务终态推导。

## 17. 风险、成本和取舍

| 风险/成本 | 说明 | 控制方式 |
| --- | --- | --- |
| 模型调用次数增加 | 每章、每 phase、每 character 都会增加 Run | max parallelism、completed Part 复用、只在 fan-in 超限时归并 |
| 小 Run 总数失控 | 长篇 source scope 可能把一次任务扩成大量调用 | Task Contract、编译/Expansion 前总 Unit 准入、Long Task 总模型预算、typed failure |
| 总成本未必下降 | 拆分提高稳定性，不保证 token 总量立即下降 | 精确工具 profile、紧凑 digest、contract cap、运行 usage 对比 |
| Fragment 一致性 | 并行输出可能命名不一致 | index 冻结身份，fragment 不得改 key/title，最终 validator 校验 |
| 摘要损失细节 | 逐章 digest 可能丢失后续需要的信息 | 保存 source refs，允许按精确引用再次调用短读取工具 |
| 活动 v5 Task | 新 executor 不能安全猜测旧 recipe 语义 | 上线前审计并通过应用接口完成或明确取消，不保留静默 fallback |
| Provider 差异 | reasoning/token/工具行为不同 | scripted 合同测试 + 用户配置的真实 Provider 手工验收 |

## 18. 验收限制

Phase 0 至 Phase 7 执行后仍未关闭的变量：

1. 本地 PurrA 0.4.0 的 `RunSnapshot` 稳定导出，以及正式发布版本与
   `backend/requirements-purra.txt` 的版本对齐；
2. 发布验收时 PurrA 的实际导入路径、版本和活动任务状态；
3. 当前一次真实项目通过不能替代更广泛模型/项目样本的质量评估；本次结构正文只完成了
   schema、来源、持久化和流程验收，没有由自动指标证明其文学质量。

`series_arc:index` cap、structure reasoning、后续 index/fragment 工具选择、source receipts、
完整 deliverable 和 token 分布已由本次真实 Turn 实测关闭，不再列为 Phase 7 阻断。

# PurrTypos 接入 PurrA 0.5.0 记忆组件实施计划

日期：2026-08-31。状态：源码适配与离线集成验证已完成；真实数据转存、真实 Provider、运行中服务切换和完整应用 E2E 尚未执行。

当前进度、变更边界、基线失败名称和验证证据见 [执行记录](./2026-08-31-purra-memory-component-adoption-execution.md)。阶段内已完成的源码项据实勾选；需要真实数据、服务或 Provider 的退出条件继续保持未完成。真实数据转换、服务切换与发布仍遵守本文约束。

## 1. 目标与不可改变的约束

目标是让 PurrTypos 通过本地 PurrA 的独立记忆组件 purra-mem0 执行通用记忆操作，删除项目重复维护的实现。不是只给旧 SQLite 检索套上 Retriever，也不是并行增加另一套记忆系统。

- 本地依赖保持 0.5.0；Core 与记忆组件分别安装、分别验证实际导入位置。不能回退到旧版发行包。
- 通用的存取、提炼、检索、版本、幂等、状态变更、决议执行、来源撤回、证据校验由组件负责。PurrTypos 提供业务身份、来源、触发条件、授权策略和界面。
- 框架确实缺少的通用能力补在可选记忆组件；不得把小说业务字段和规则写进 Core。只有证据证明缺少必要的通用执行扩展点时，才评估最小 Core 改动。
- 管理界面、Agent 工具、自动沉淀、主动上下文加载使用同一记忆实现和同一记录身份。不存在长期双写、旧接口别名、运行时数据迁移、旧实现 fallback 或第二套记忆幂等账本。
- 不以删文件数量作为成功标准；不能删掉权限校验、来源追踪、预算、取消、审核或防止丢数据的机制。也不承诺全仓库已经没有任何冗余。
- Planner 协议流继续暂缓。本文不修改规划流、规划 Operation 归属及其测试协议，也不夹带正在进行的会话传输重构。
- 原作者数据、Story Memory 的结构化故事状态、写作方法、续写 canon 有各自权威来源；不因名称中带有“记忆”就全部搬成 Mem0 文本。
- 无兼容层不等于允许丢失现有数据。真实数据的转换、切换和旧存储清理由第 7 节单独控制，不能自动清库。

## 2. 已核实的基线与尚未证明的内容

### 2.1 仓库和依赖

| 项目 | 2026-08-31 的事实 | 对执行的要求 |
| --- | --- | --- |
| PurrTypos | 基线提交 b23a97f | 基线仅用于该阶段行为对照 |
| PurrA | HEAD 为 06f712a，Retriever、Mem0、Planner 等包含未提交工作 | 不能把 HEAD 当成完整依赖版本；记录所用源码/构建产物摘要并复核并行变更 |
| Core | requirements-purra.txt 使用本地 editable 依赖；当前虚拟环境实际导入同级 purra/src 下的 0.5.0 | 保持公共包导入；打包产物不能依赖开发机路径 |
| 记忆组件 | PurrTypos 虚拟环境尚未安装 purra-mem0 | 本文目标尚未完成，不把之前的 Retriever 适配算成 Mem0 接入 |
| Python 组件 | purra-mem0 0.5.0，mem0ai 固定 2.0.19；managed extra 固定 LangChain 依赖 | 复用受管 Provider，不另建未经计量的 SDK 调用路径 |
| TypeScript 组件 | 同版本独立包，mem0ai peer 固定 3.1.7 | 修改组件公共契约时同步两端；PurrTypos 本身只接 Python 后端，不在前端再运行一份记忆引擎 |

当前可直接复用的接口包括：add、extract、get、list、update、set_state、delete、history、review、resolve、revoke_source、validate_evidence、retrieve、MemoryContext、MemoryProviders、MemoryBudget、run_model。

组件已实现持久操作 key、同 key 不同输入拒绝、完成回执重放、命名空间内写入互斥、乐观版本校验、未知写入隔离与恢复核验。它不承诺 SDK 存储和控制账本之间的分布式原子事务，也不承诺任意新 key 下的内容天然唯一。

### 2.2 本次计划核查的证据

本次运行 Python 组件的 test_memory、test_resolution、test_sources、test_review、test_providers，结果为 **92 passed in 2.10s**。用的是当前本地源码、临时存储及模拟 SDK/Provider；未调用真实服务。

该结果不证明新增接口缺口已补齐、不证明 PurrTypos 已接入，也不证明中文语义质量。没有在本轮重跑 PurrTypos 全量门禁；此前门禁受未完成的 Planner 接入影响，实施阶段必须重新记录失败清单，不能永久沿用旧失败数量。

## 3. 必须覆盖的现有调用链

下表记录计划制定时的入口，作为修改范围清单，不是当前实现说明，也不是允许保留旧实现的清单。每一行都要有改造结果或明确的保留理由。

| 编号 | 改造前入口和链路 | 必须处理的事项 |
| --- | --- | --- |
| C1 | MemoryCenter / MemoryModal → services.memories → backendApi → routers/memories.py → long_term_memory_service.py | 创建、修改、待审、生效、归档、分类、置顶、筛选、选取、错误和冲突提示；不能只改检索 |
| C2 | Writing Agent → tools/handlers/memory_tools.py → SqliteWritingToolMemoryRepository | createMemory、updateMemory、archiveMemory、linkMemories，以及本书设定/伏笔操作；复用 C1 同一应用操作，保留工具权限边界 |
| C3 | searchMemories → WritingMemoryRetriever → SQLite memory_items | 将实际数据源改为组件；保留受信 Run/作品绑定、查询上限、取消和证据，不再沿用旧 source/version 伪装新记录 |
| C4 | WritingAgentProfile → RepositoryWritingContextSource → UnifiedMemoryRetriever → WritingMemoryContextBuilder | 自动召回、TaskSpec 查询意图、手选和置顶、来源定位、权威优先级、预算、回执与失败处理 |
| C5 | InlineEditLayer / inlineEditContext → POST /memories/context → application/writing_memory_context.py | 当前是真实使用中的独立入口，不能删作死代码；与 C4 共用组件检索/组装能力，不伪造 Agent Run |
| C6 | articles.py、chapter_diff.py、setting_diff.py → memory_deposition_service.py → memory_intelligence_service.py | 仅对符合策略的已接受内容提炼；覆盖部分采纳、重复提交、失败、撤销和来源修订 |
| C7 | characters.py、setting_entities.py、story_background.py、outlines.py、conversations.py → 自动沉淀 | 人物、设定、背景、大纲和明确“记住”指令的触发、稳定来源与操作标识；删除静默吞错和不可追踪的沉淀 |
| C8 | memory_service.py / 工具 Repository → ai_memories、ai_foreshadowing → memory_items 镜像 | 本书设定/伏笔作为业务来源保留时，只维护一条受控记忆投影路径；删除现有重复镜像及启动时旧回填 |
| C9 | StoryMemoryAnalysis / StoryMemoryEvolution / StoryMemoryLedger / story_memory_* → UnifiedMemoryQueryService | 保留章节证据、结构化变化、原子应用/撤销与叙事顺序规则；逐项去除与组件重复的通用记忆逻辑，不把结构化状态账本直接等同于 Mem0 |
| C10 | 来源修改、历史恢复、删除、整书删除、导入数据库 | 路由、CRUD、Agent 采纳入口全部扫描；修改和删除之后不能召回旧来源，恢复旧正文也必须有新来源修订 |
| C11 | Run 证据、工具结果、上下文、压缩摘要、检查点恢复 | 对记忆依赖执行重新校验；已保存的历史文本不等于允许再次发送给模型 |
| C12 | main.py lifespan、database/connection.py、routers/files.py、SettingsPage/useDatabaseActions、platform/browser、electron/database_ipc.js、后端打包脚本 | 客户端资源生命周期、持久数据目录、浏览器与 Electron 两条备份恢复链路、依赖与平台打包完整性 |

计划制定时观察到的缺口包括：人物删除、大纲删除/历史恢复没有调用相应沉淀失效入口；备份只导出/恢复 purrtypos.db，且 Electron 导入直接停后端并复制数据库文件；管理 API 与 Agent 工具各维护一份记忆 SQL；内联编辑有独立记忆组装入口。这些缺口的源码处置与验证结果见执行记录。

## 4. 状态和责任的唯一所有者

| 内容 | 唯一权威 | PurrTypos 应保留什么 |
| --- | --- | --- |
| 通用记忆正文、向量及 SDK 历史 | Mem0 SDK，由 purra-mem0 公共 API 控制访问 | 不再写 memory_items 作为另一份运行时记忆正文 |
| 记忆有效状态、版本、幂等、决议、撤回规则和用量准入 | purra-mem0 控制账本 | 传入 key、期望版本、来源及策略；不得直接写 SDK 或组件内部表 |
| 作品/章节/人物/设定/伏笔/大纲及其历史 | PurrTypos 业务存储 | 业务 CRUD、权限、来源修订；来源正文与派生记忆不是两个互相覆盖的主记录 |
| Story Memory 结构化状态、章节增量、字段演化和证据 | PurrTypos Story Memory 领域 | 章节顺序、事实权威、部分接受、整组应用/撤销；不让通用相似度取代这些规则 |
| 实际 Run 输入和工具证据、检查点及执行用量 | PurrA Core 的现有 canonical 持久化链路 | 按组件所有者分发验证，不再建一套 Run 证据账本 |
| 业务事件是否已交付给记忆组件 | PurrTypos 的业务命令/事件记录 | 只记录稳定来源引用、修订、命令 key、交付进度和组件回执引用，不复制记忆状态机 |
| 统一记忆中心的展示 | 应用查询投影 | 合并业务状态与组件结果供展示，不成为新的正文或状态权威 |

Story Memory 不整体删除，也不整体豁免重构。实施时给其中的逻辑逐项标记“领域规则 / 通用机制 / 查询投影”。例如，“新章节的人物状态是否覆盖旧章节”留领域；通用的语义候选检索与组件记录的版本校验直接复用组件。现有结构化账本自身的跨表事务不是 Mem0 记忆事务，不能为表面去重而拆散。

searchWritingMethods 保持已发布方法目录及显式推荐权限；关联章节读取、绑定的写作方法和续写 canon 不自动进入 Mem0，也不扩大到其他产品。

## 5. 接入前必须解决的组件契约差异

下列是基于当前公共 API 核实的差异。“处理决定”中要求补充的能力尚未实现；不能在宿主中偷用私有 SDK 接口绕过。

| 编号 | 当前事实与现有需求 | 处理决定与边界 |
| --- | --- | --- |
| G1 元数据与管理查询 | MemoryRecord 没有业务 metadata；list 仅支持 state/limit/after；retrieve 不提供宿主 metadata 筛选。项目使用 kind、scope、pinned、importance、来源定位、管理筛选和手选 ID | 在组件补最小受限 metadata 存取、必要的列表筛选/分页和来源定位能力。业务定义字段含义；禁止覆盖内部 scope/version/source/state。仅 metadata 的修改不得假装修改了正文。不得先扫描任意有限页再声称完整筛选或精确总数 |
| G2 待审与生命周期 | add 直接 active；extract 才产生 pending；set_state 仅 active/disabled。项目支持手动创建 pending、直接候选和审核/归档 | 在组件补“不调用提炼模型就写入待审文本”的能力，消除先 active 再 disabled 的可见窗口。统一状态契约；归档/拒绝/冲突等原因需要有可审计的表示，不能只把所有 disabled 都显示为已归档 |
| G3 选择与上下文 | MemoryContext 目前只按 query 检索后整条装配；项目还需要手选、置顶、TaskSpec 条件及领域优先级 | 组件提供可复用的受控记录选择/整条组装能力，并由 MemoryContext 复用；宿主只给选择和权威策略。聊天与内联编辑共享它，不能复制两套预算/回执算法或为普通 HTTP 请求伪造 Run |
| G4 关联关系 | 组件 resolve 支持 independent/duplicate/supersede/conflict；项目 memory_links 还表达 supports/relates_to 并参与召回 | 不把普通关联伪装成 resolve。为确实需要保留的通用记录关联补有界接口，复用组件版本、范围和操作控制；领域关系含义由宿主解释。不启用另一套图数据库或 SDK graph Provider |
| G5 重排 | 项目存在受管模型重排；managed SDK 当前不开放 reranker/graph 配置 | 先用验收场景确认语义召回所需重排。必须保留的通用重排在组件受管能力内实现/复用；故事字段权威排序仍属领域。不得用未受管 SDK reranker 替换，也不得无验证地删除当前质量约束 |
| G6 恢复和发送前校验 | validate_evidence 已有；MemoryContext 只校验新组装的块，不自动改写检查点或所有后续模型输入 | 追踪现有 Core/宿主真实调用点，证明首次发送、恢复、重用工具结果及摘要都经过校验。没有合适公共接点才补通用外部证据验证扩展；不可假定框架已有某个 hook，更不可由宿主导入/猴补私有 engine 来绕过 |

G1–G4 的公共行为先定义再实现，Python/TypeScript 同步。新增参数使用一套当前契约，不提供旧字段别名。SDK 原生能力只有经过组件授权、控制账本和行为验证后，才算组件支持。

### 5.1 必须逐字段落实的数据契约

- 记忆 id 统一使用组件返回的字符串；同步前端类型、路由 DTO、工具 schema、选择状态、关联和证据，不把旧整数类型保留为兼容分支。
- text、version、state、source、inferred、expires_at 以组件为准；MemoryScope 中 project 对应受信作品身份，user 来自宿主持久身份，不由模型选择。无账号体系时也不能用当前 Run ID 冒充用户。
- kind、scopeType/scopeId、summary、keywords、importance、confidence、pinned 逐项确认现有读写者并以受限 metadata/业务策略承载，不静默丢弃。来源对象标识与权限作用域不能混为一谈。
- 创建/修改时间与审核原因如需展示，记录真实事实；旧记录没有版本历史时不能伪造。last_used_at 等“使用”统计从实际提供给模型的证据投影，单纯浏览或生成预览不算已使用。
- 组件 active/pending/disabled/deleted、过期/来源撤回、决议 kind 与界面状态制定明确映射；显示“冲突”“拒绝”“替代”须有原因/回执，不能凭字符串推断。
- 新增接口沿用当前明确的校验/错误模型；管理接口不接受任意 dict 绕过范围、期望版本或状态转换规则。缺少必要配置或未知状态明确失败，不伪装空列表/成功。
- 工具写入保留现有 CONFIRM/审批和风险策略；前端管理命令的用户授权不等于模型拥有同等权限。组件能力默认不全量暴露为模型工具，模型不能选择 operation key、预算 key、source 权威或宿主身份。

## 6. 全链路行为合同

### 6.1 操作幂等与内容去重分开

1. 首次提交前确定并持久保留业务命令标识。同一次 UI 提交、工具执行重放、已接受修改的重复事件和重启恢复使用同一组件 key；真正的新操作使用新 key。
2. 组件负责持久幂等与冲突检查；应用不实现第二份 memory_ops。组件 receipt 是记忆操作结果，业务记录只引用它。
3. 源事件 key 绑定来源身份、真实修订及动作；不能用每次重试新建的 UUID、模型任意参数或截断正文指纹代替。一次 Run 可以包含多条操作，不能把 Run ID 单独当 key。
4. 同 key 改参数是错误；更新/删除/审核须携带所见 version。版本冲突后重新读取并交用户/既定策略决定，不能强行覆盖。
5. unknown/running 不等于回滚。先 drain/检查 operation；reconcile 前必须确认所有旧写入者已停。提炼批次不能证明完整时保持不可用，不通过新 key 重提炼掩盖失败。
6. 相同内容但不同业务事件不天然同一次操作。通用相似评审和原子决议复用 review/resolve；是否接受、保留哪一来源由业务策略决定。新实现不得复刻当前 normalize + 前 240 字指纹的误合并逻辑。
7. “已有相同记忆”的界面反馈只能来自真实的去重决议/重放结果。不同来源、临时例外、历史变化不因文本相似就自动合并，也不承诺全库语义唯一。

### 6.2 来源变更、事件交付与删除

| 来源/动作 | 触发及结果 |
| --- | --- |
| 手动笔记、明确记住 | 按显式命令直接保存或待审；修改产生可追踪修订；不存在自动上传整段聊天 |
| 已采纳章节/内联修改、人物/设定/背景修改 | 根据真实采纳结果确定来源文本；同一采纳事件至多交付一次逻辑操作；未经接受的草稿不生效 |
| 提炼结果 | extract → pending → review 建议 → 宿主授权 resolve/生效；空结果、提炼失败、待审是不同结果，不回退为“把原文写成成功记忆” |
| 人物/设定/背景/大纲/本书设定/伏笔 | 来源业务对象保留，记忆为派生结果；统一触发更新，不再由 service、tool、启动 schema 各镜像一次 |
| 来源修改或历史恢复 | 新业务修订；旧依赖先禁止复用，再更新/提炼。撤回单个 revision 与永久撤回 source ID 严格区分，不能误用永久撤回阻止合法更正 |
| 来源删除、章节/整书删除 | 删除前留下稳定撤回指令和范围；清除原对象后仍能完成撤回。禁止旧 Run、残留向量、延迟写入或再次提炼恢复可见性 |
| 取消、拒绝、部分采纳 | 未接受部分不沉淀；已完成的远程调用不谎报撤销；保留操作实际结果 |

业务 SQLite 与 Mem0 不共用原子事务。优先复用已有可靠业务命令/事件记录；不足时只补最小的持久交付记录，与来源提交同事务保存来源引用/修订和 key。交付失败可恢复，但不得无限重试或另造通用任务平台。

来源已变更而撤回尚未交付的窗口，必须在召回/发送前按宿主当前来源修订阻止旧内容继续使用；不能只依靠异步任务最终会执行。记录引用的正文必须可从对应不可变来源修订读取，不能拿“当前最新正文”冒充事件发生时的内容。

长章节/长来源按明确边界和输入额度分批，保留来源修订、原文范围及批次标识，操作 key 包含对应片段身份；覆盖无标题正文和超长单段。不得沿用静默截取前几千字再报告整份来源已处理。批次部分失败/取消要分别记录已完成、待审和未处理范围，不自动激活不完整结果，也不为此引入新的 Planner。

来源保存与记忆沉淀分别报告结果：记忆服务未配置或不可用时，不能把记忆失败伪装成功，也不应让正常编辑丢失已保存正文。自动提炼开关沿用用户选择；关闭提炼不等于删除既有记忆或关闭管理/检索，更不能转而启动旧沉淀实现。

### 6.3 召回、上下文与证据

- 工具搜索、自动上下文和内联编辑走同一组件记录；管理列表不调用向量检索冒充枚举。选取 ID/来源定位也不能退回旧 memory_items。
- 保留作品隔离、受信 Run 绑定、手选意图、TaskSpec 范围及取消；传给模型的参数不得扩大预绑定 scope。
- 宿主决定续写 canon、已确认 Story Memory、一般长期记忆的权威及预算分配；Mem0 的相似度不能推翻这些规则。
- 组件使用实际分配额度整条装配；超长条目明确未注入或提供受控读取入口，不把截断片段标成完整证据。外层不得再次截断正文却保留原完整回执。
- 本轮实际选择的条目才有注入回执；证据含真实组件 id/version/source。仍用 Core 的 canonical 路径持久化，禁止新造 memory-only Run journal。
- 恢复或再次发送前，根据受信持久化证据的所有者分发 validate_evidence；未知所有者、错误 scope、旧版本、撤回/过期要拒绝或重建，不静默忽略。
- 缓存的工具结果、压缩摘要、已解析检查点也可能包含旧记忆；必须追踪其依赖。无法证明来源有效时禁用该复用路径，而不是只重验新检索结果。
- 校验应尽可能靠近实际模型发送，并验证组装期间 epoch/来源变化；已经发出的远程请求无法撤回，不承诺跨远程服务的绝对原子撤销。

### 6.4 Provider、资源与费用

- 使用本地 purra-mem0[managed] 与明确的 LLM/Embedding 适配。现有聊天模型配置不自动等于可用的 Embedding；模型、端点、维度和超时需独立校验。
- 真实 Run 内提炼/评审复用 run_model 和实际 model_tasks；后台沉淀、管理或内联操作使用明确限额的宿主回调，不伪造 Run、不借已结束 Run 计费。
- MemoryBudget key 跨重试/恢复稳定，LLM 与 Embedding 用量分开；失败调用、未知用量、预留额度按组件契约处理，不把未知填零或自动换 key 重获额度。
- 字符上限、token 用量与金额是不同量；不能把字符数当已报告 token，也不能把回调次数宣称为真实费用。应用层选择预算归属与授权，组件负责执行其限额。
- add 即使不做 LLM 提炼仍可能使用 Embedding；更新、搜索及 SDK 内部回调都计量，禁止隐藏重试和未受管重排。配置不足显示不可用，不回退旧记忆栈。
- 明确 DATA_DIR 下的向量库、SDK history、控制 journal 路径；一个 SDK store 配对一个持久 journal。共享 store 但隔离 scope，多个进程/实例的存储锁行为须实测；不为每条记忆或每次 Run 建一套 store。
- 导入 Mem0 前关闭遥测并设置数据目录。凭据复用安全配置机制，不写进业务 metadata、备份或日志；本地存储不代表模型推理本地，使用云服务会发送授权的文本。
- lifespan 负责关闭顺序：停止新任务/事件交付 → 等待或明确处理进行中的工作 → drain → 关闭组件、SDK/向量库和 Provider 资源。超时不能提前释放还在写入的资源。

## 7. 现有数据、备份与切换

这部分在真实数据上执行前必须有可审阅的结果和明确授权；当前只规划，不转换、不删除。

1. **先做只读盘点。** 统计各书 memory_items、memory_links、来源镜像及 Story Memory 的记录数量、状态、失效来源和关联完整性；不在日志或文档输出私有正文、凭据。
2. **默认保留数据。** 制作一致性备份，在独立目录对副本做转换演练。运行时只支持新契约；如需转存旧记忆，采用独立的一次性工具，不能塞进启动、业务读取或错误 fallback。一次性转存不是自动获准的操作，也不能因为禁止兼容层而默认丢弃旧数据。
3. **转换要保持含义。** 保留正文、来源、待审/禁用等可见性、业务 metadata 和关系。组件生成新 ID，转换清单记录旧到新 ID 对应并修正选取/关系引用；真实存在的旧历史可保留为审计资料，不伪造组件过去的版本。没有来源修订的人工旧记录标明导入来源，不冒充章节原文证据。
4. **不能边导入边泄漏。** 非 active 的旧条目必须直接进入对应不可用状态，不允许先 active 再补状态。导入也要有稳定 key、预算和可恢复结果；如果需要 Embedding，列出预计调用规模和外发内容范围后再执行。
5. **备份不再只是一个 db 文件。** 当前 /database/export 和 /database/import 只处理 SQLite，必须升级为包含宿主数据库、SDK store、SDK history、组件 journal 及配对 manifest 的完整备份/恢复流程。同步浏览器文件选择/下载、设置页提示、Electron IPC/preload 和实际导入执行，移除 Electron 仅复制 db 的旁路。两种平台共用备份校验/切换规则，平台只处理文件选择与进程生命周期；复用平台标准归档，不自造压缩格式，凭据不随包导出。
6. **恢复应成套验证。** 先停止相关写入并获取一致快照；校验文件完整性、store 身份、scope 身份、模型/维度配置、版本和引用后切换。宿主持久身份须随可恢复数据保持一致，跨设备恢复不能因为随机生成新 user 而丢失可见性；如需重绑定，必须显式验证权限和映射。不覆盖单个 journal 或仅恢复业务 db 继续使用旧向量库。归档解包限制路径、大小与文件类型，拒绝路径穿越及不完整备份。
7. **切换只有一个运行时。** 副本验收通过后再停止旧进程、执行获准的数据切换、启动新运行时并核实实际导入路径。旧副本只用于离线回退；回退恢复完整配对数据和对应程序，不在新代码中保留旧读写分支。
8. **旧存储何时删除。** 先移除旧表/FTS/镜像的运行时依赖和启动回填；旧数据文件在确认转换、备份恢复和新链路通过后再单独清理。删除真实原库/备份须另行明确授权，不能把 DROP TABLE 当普通代码整理。

旧 Run/检查点不能通过改写旧 evidence ID 或字段别名强行续跑。没有可信映射与依赖证明时保留历史展示、拒绝复用并发起新 Run。删除记忆/撤回来源不等于擦除 SDK history、已有 Run、原文或备份；完整物理遗忘不在本轮默认承诺内。

## 8. 实施顺序与每阶段退出条件

所有框选项初始为未完成。阶段中间可以有独立测试夹具对照，但正式入口不做双写或新旧 fallback。每阶段记录修改路径、测试命令、结果、未验证项和可删除的旧路径。

### P0：固定基线与契约清单

- [x] 核对两仓当前工作树和本地包版本，保存源码/未提交变更基线；第一批变更只涉及 integrations/mem0 和本计划记录，没有改动 Planner/传输文件。后续触及共享 Core 文件时须再次核对。
- [x] 复查 C1–C12 的所有生产调用者、路由、类型、存储及测试；新增来源交付、组件资源和完整备份入口也已纳入。
- [x] 记录 PurrTypos 定向测试和全量门禁的失败名称；框架基线区分 Core、组件、宿主和修改前已知 Planner/会话失败。
- [x] 完成第 5.1 节字段/状态映射、来源身份与版本策略、操作 key 规则和确定性测试；G1–G6 已落实。

退出条件：每个现有行为都有“复用 / 补组件 / 保留业务 / 明确变更”的去向，没有未说明的功能删除；测试基线可复现。无需此时提供真实 Provider 凭据。

### P1：先补并验证组件公共契约

- [x] 在 purra/integrations/mem0 中实现 G1–G4 公共契约：metadata、显式分页/过滤、直接待审写入、控制状态/原因、共享选择/组装与有版本的显式关联；复用既有 journal/Provider/授权/恢复机制。宿主接线结果见 P2–P5。
- [x] G5 已核实当前重排承担人物/关系/时间线/连续性判断，并有仅选择授权候选及拒绝未知 ID 的对照测试；保留领域判断，组件负责授权选择/当前版本读取/组装，不启用未受管 SDK reranker。真实检索质量仍待 P7。
- [x] G6 已在 Core 增加公共 ModelInputEvidenceValidator，并覆盖工具回执、压缩、检查点恢复和实际 Provider 发送前校验；Planner 协议流未改。
- [x] Python/TypeScript 公共导出、共享契约、组件测试和消费者类型测试已同步；真实组件恢复探针确认撤回后 Provider 调用数为 0。

退出条件：宿主所需能力通过公共 API 可实现；metadata 不能越权、待审写入没有 active 窗口、分页/筛选不漏而假称完整，原有幂等/恢复/预算保障不退化。不得先在 PurrTypos 私下绕 SDK 再补框架。

### P2：依赖、配置和宿主资源接入

- [x] 后端声明同级本地 Core 与 purra-mem0[managed]；打包脚本逐行解析本地入口，不再把 requirements-purra.txt 当成单一路径。
- [x] 包边界与运行时依赖集合已同步；隔离站点包验证确认 Mem0、Qdrant、LangChain、Core、组件和后端运行依赖均来自分发目录。
- [x] lifespan/Composition 建立受管 SDK、受信 user/project scope、显式预算回调、项目数据路径与 Core → memory → SDK/Embedding 的关闭顺序。
- [x] 设置页增加独立 Embedding endpoint、模型、维度、凭据及显式提炼/评审模型选择；未配置不创建 SDK，也不选择默认付费服务。

退出条件：临时目录中的真实 SDK 存储与受控替代 Provider 可完成重启后 CRUD/检索；项目实际导入本地两个包；未配置、关闭中、存储锁冲突、模型维度变更有确定行为。维度变更不能静默混用旧集合，须单独重建并校验后切换。

### P3：统一写入、生命周期和来源交付

- [x] 管理 API、Agent 工具、自动沉淀接到唯一 `MemoryApplicationService`；服务端从受信对象解析 book/source/version，不信任模型或客户端声明的来源权威。
- [x] 接入 add/extract/update/set_state/delete/review/resolve/revoke_source 及 G4 关联；严格 DTO、工具 schema、错误映射和幂等/版本输入已同步。
- [x] 删除 `memory_intelligence_service` 的独立通用提炼管线；`memory_deposition_service` 只记录业务来源修订与持久交付。
- [x] 来源提交和交付记录同事务；覆盖删除、历史恢复、整书删除、部分采纳、重复事件和重启恢复。
- [x] 删除旧 source mirror 写入；提炼失败保持失败/待恢复，不写成成功记忆。

退出条件：同一逻辑操作从不同入口重放不会重复写入；来源变更后旧记忆不能继续使用；并发编辑不覆盖他人版本；不依赖旧 memory_items 维持功能。

### P4：统一读取、管理界面和上下文

- [x] `WritingMemoryRetriever` 改成组件检索；保留 `RetrieverTool`、受信 Run/作品作用域、输出预算、取消和完整证据。
- [x] MemoryCenter、MemoryModal、useMemorySelection、backendApi、services/index、types.ts 同步为字符串 ID/version/state/metadata 当前契约。
- [x] 统一查询投影承接组件与 Story Memory；列表、筛选、待审、归档、置顶、关联、历史/来源入口不以列表预取触发 Embedding。
- [x] C4/C5 共用组件选择/整条组装及同一个显式业务来源预算函数；旧语义 Builder/Repository/返回格式兜底已移除。
- [x] UI/接口使用稳定错误码展示不可用、版本冲突、来源失效和未注入事实；旧 deduped/伪使用字段已移除。

退出条件：管理界面、Agent 搜索、自动上下文和内联编辑指向同一条组件记忆；没有漏接手选、关联、预览或旧数值 ID 假设；相关 UI 在真实应用环境中验证。

### P5：领域边界与恢复闭环

- [x] Story Memory 已按领域状态账本保留；可由组件取代的通用语义读写已删除，原子事务和查询投影继续由领域拥有。
- [x] canon、Story Memory 和一般记忆按既有权威/预算组合；没有创建 Story Memory 全量向量镜像，重排失败不再回退为未经模型判断的候选。
- [x] 组件证据校验已接入真实 Run 首次/后续 Provider 调用、工具回执、检查点恢复和压缩/模型任务复用。
- [x] 来源撤回/过期、未知 owner、版本或 epoch 变化都有发送前拒绝路径；外层截断会清空相应回执。

退出条件：通过历史工具结果或旧摘要绕过失效记忆的路径被测试封住；领域事实不会被通用相似度改写；没有第二份组件状态权威。Planner 相关失败仍独立报告。

### P6：备份、数据演练、打包及删旧

- [x] 实现标准 ZIP `.purrbackup` 的宿主数据库与组件 store/history、已产生的 journal、manifest 成套备份恢复；校验哈希、SQLite、维度、路径/大小并剔除凭据，在临时副本完成恢复测试。
- [ ] 旧真实数据的只读盘点、一次性副本转换和引用核验尚未执行；不直接操作真实库。
- [x] 修改 schema 初始化，删除旧 memory_items/FTS 回填、旧表和重复镜像入口；删除重复的 `memory_service.py`，灵感/伏笔路由与 Agent 工具共用按 book 约束的业务来源仓储。
- [x] 完成第 9 节删除/保留清单与生产/测试全局引用扫描；当前架构文档及工具 SKILL 已同步。
- [x] `build:web`、实际分发资源和 Electron IPC 单测已验证；在 cwd=/tmp、`python -S` 且无同级源码路径时确认 Core/组件 0.5.0 从分发目录导入。
- [ ] 只有数据方案和演练结果可审阅后，才进行获准的真实切换；保留离线备份，不保留运行时兼容层。

退出条件：一份完整备份可以在新目录恢复同一作品及记忆；最终运行时只使用新记忆实现；发布包包含实际运行依赖，不能只在开发虚拟环境通过。

### P7：整体与真实服务验收

- [x] 执行确定性矩阵并记录各层证据；全量失败逐项与 P0 比对，记忆接入新增失败已清零，剩余名称与修改前 26 项一致。
- [ ] 整体改造完成后，使用已配置并获准的 LLM/Embedding、虚构数据和明确额度进行真实中文评估，再验证 PurrTypos 的完整记忆链路。
- [ ] 核实启动的后端实际加载最新包；如已有用户服务，需要在不会中断活动工作时切换，不擅自杀进程。
- [x] 本轮未启动常驻测试/开发进程；核对监听端口时不停止现有用户服务。

退出条件：区分“源码适配完成”“本地 SDK 集成通过”“真实服务通过”“完整应用 E2E 通过”。缺少真实验证不算通过；Planner 未完成导致整段 Agent 流程不可验时明确列为未完成项，不能因此扩展本轮 Planner 范围。

## 9. 必须删除或收缩的旧实现

| 位置 | 目标处置 | 删除前的检查 |
| --- | --- | --- |
| services/long_term_memory_service.py | 移除 memory_items CRUD、FTS/LIKE、截断指纹去重、镜像与状态 SQL；无剩余业务职责后删除文件 | 管理接口、自动沉淀及所有调用者已迁移 |
| services/memory_intelligence_service.py | 删除独立通用提炼管线与失败静默 fallback；仅实际业务提示/策略可保留到唯一调用点 | 提炼、评审、额度、空结果/失败区分已由新链路验证 |
| infrastructure/persistence/writing/sqlite_memory_repository.py | 删除旧语义召回实现 | 手选、来源定位、关联、统计和内联编辑全部迁移 |
| SqliteWritingToolMemoryRepository / WritingToolMemoryRepository | 移除组件记忆 CRUD/search/link 及重复镜像接口；保留仍属于本书设定/伏笔业务 CRUD 的部分并明确命名职责 | UI 与工具不再各维护一份业务动作实现 |
| WritingMemoryRetriever | 删除旧 SQLite 查询逻辑；如需保留，只负责宿主绑定授权和组件调用 | 不重复实现组件过滤、状态、证据或版本算法 |
| WritingMemoryContextBuilder / MemoryRecallRepository / UnifiedMemoryRetriever 的语义部分 | 移除组件已覆盖的召回、排序、整条预算和回执实现；收缩为领域组合策略 | Story Memory/canon 优先级、手选及输出事实没有退化 |
| application/memory_reranking.py | 按 G5 处置通用语义部分；有明确领域职责的部分保留 | 不把相似度、任务相关性和故事权威混成一种评分 |
| memory_service.py 与 schema.py 旧镜像/回填 | 删除重复沉淀及启动时 legacy 导入；业务来源 CRUD 不因表名旧而误删 | ai_memories、ai_foreshadowing 的真实 UI/工具使用者仍完整覆盖 |
| memory_links 及旧状态投影 | 通用关系迁到 G4；删除旧关系引擎和旧 ID 分支 | supports/relates_to 的调用及展示未丢失，resolve 的含义不被混用 |
| routers/memories.py、schemas/memories.py、前端 types/services/工具 SKILL | 只保留当前契约；移除旧原始异常暴露、误导注释、任意字段兜底 | 业务动作仍存在不等于旧实现仍存在；相同路由名称可以保持 |

全局扫描生产代码、测试、前端、打包脚本及当前文档中的旧接口、表名、状态/ID 假设；剩余引用逐项解释为“领域来源”“离线转换/审计”“测试历史材料”或删除。历史设计文档可标为历史，不为消除文本命中而重写过去事实。

不删除关联章节/大纲读取、写作方法目录、续写 canon、Story Memory 领域账本，也不删除使用中的 /memories/context 路由；替换的是其记忆实现。最终不留下“新实现异常就用旧实现”的任何分支。

## 10. 验收矩阵和命令

| 编号 | 场景 | 必须观察到的结果 |
| --- | --- | --- |
| V1 包和依赖 | 本地 editable、分发目录、无兄弟源码目录 | 两包版本一致且实际位置正确；分发可运行，不误导入全局旧包 |
| V2 基础操作 | 创建/待审、修改、启停、归档/原因、删除、历史、metadata | 所有入口同一记录；字符串 ID、真实版本、明确状态；无瞬时 active 泄漏 |
| V3 幂等与并发 | 双击、超时重发、重启、同 key 改输入、并发更新、未知 SDK 提交 | 回执重放无重复写；冲突明确；不盲目重执行；恢复不释放未停写入者 |
| V4 隔离 | 他书 ID、伪造 scope/source、恶意 metadata/filter、批量取 ID、未审批写工具 | 所有入口拒绝越权，管理/工具/直接组件调用都受约束 |
| V5 来源 | 保存、部分采纳、拒绝、修改、删除、历史恢复、整书删除、延迟事件、长来源分批 | 只处理授权来源；无漏交付；已处理范围真实；旧依赖不可用，重启和晚到结果不能重新激活 |
| V6 提炼与决议 | 独立事实、重复、替代、冲突、暂时例外、无结果、失败、不确定 | pending 不自动生效；review 是建议；resolve 原子执行；不捏造合并来源或吞错 |
| V7 查询与上下文 | 管理筛选分页、手选、置顶、关联、空结果、大记录、预算不足 | 不把漏项结果假称完整；未选/未注入有事实说明；完整条目与回执一致，列表不隐式调用 LLM/Embedding |
| V8 领域规则 | 章节先后变化、人物/关系状态、继承 canon 冲突、待审 Story Memory | 已确认且有效的权威规则保持；来源与业务结构不被泛化文本替换 |
| V9 恢复与缓存 | 撤回后恢复、过期、旧工具结果、压缩摘要、组装中变更 | 旧内容在发送前被阻止或重建；历史可审计但不自动复用 |
| V10 额度与资源 | LLM/Embedding 配额耗尽、无用量、取消、关机、存储锁、模型维度改变 | 不存在隐性续调/假零用量；预算不随重试重置；资源正确关闭且不混库 |
| V11 数据与备份 | 混合旧状态副本、关联 ID 转换、跨设备身份、浏览器/Electron 全套备份恢复、坏包/缺文件 | 内容与状态可核对；不伪造历史；无只替换 db 的旁路；不完整恢复拒绝，原数据不被演练破坏 |
| V12 应用与删旧 | 记忆中心、Agent 工具、自动沉淀、内联编辑、构建与全局扫描 | 不存在旁路旧读写；UI 错误真实；当前文档与运行实现一致 |

框架确定性验证从 PurrA 仓库执行；下面的源码路径仅用于独立框架测试，不能替代 PurrTypos 的安装后导入验证：

~~~sh
PYTHONPATH=src:integrations/mem0/python/src .venv/bin/python -m pytest -o addopts='' -q integrations/mem0/python/tests
npm --prefix integrations/mem0/typescript run check
~~~

如修改 Core，再运行对应 Core/跨语言契约测试；真实 SDK 检查复用组件已有 check_sdk.py / check-sdk.mjs，先准备其受管依赖，区分“真实 SDK + 替代 Provider”与“真实 Provider”。

PurrTypos 从定向测试开始，覆盖现有 memory、story_memory、writing_retrieval、writing_context、工具/路由、备份和 lifespan 测试；被删除实现的测试改为验证新行为，不能仅删除失败断言。然后在项目根目录执行：

~~~sh
npm run check:agent-refactor
npm run build:web
git diff --check
~~~

当前 check:agent-refactor 会执行组件规范检查、TypeScript 类型检查、前端单测与后端测试。必要时分开执行定位故障；定向 Python 测试使用项目 .venv/bin/python。真实应用验证包含需要 preload API 的 Electron 场景，不能以裸浏览器替代。

真实中文质量验证复用组件 EVALUATION.md 的虚构用例，并加入本项目的章节修订、来源撤回、未确认故事状态、明确手选和内联编辑场景。先预检，整体改造完成后才按获准配置实际调用；分别记录提炼、评审、Embedding、检索、注入和回答效果/用量，不以最终答案碰巧正确代表链路正确。

性能记录同一数据集下的调用次数、延迟、候选/注入数量、备份体积和存储开销。先测基线，不预先承诺减码比例或语义准确率，也不以关闭安全校验换取指标。

## 11. 执行时需要明确、但不阻塞当前计划的条件

- 数据切换：默认保留旧数据，先副本演练；真实转存、涉及外发的向量化和原库清理在结果可审阅后取得明确授权。未决定前不自动转换或清库。
- Provider：使用已配置的服务；缺少 Embedding 或额度时标记相关验收未完成，不回退旧实现。
- 规则：默认延续显式用户写入、AI 提炼待审、Story Memory 既有审核阈值/权威规则。自动接受策略变更要单独写明，不借组件升级偷偷放宽。
- 并行改动：会话传输与 Planner 正在变化，实施前协调共享文件；本文不授权覆盖其未提交内容。
- 发布：本计划不包含自动提交、推送、发包或部署；完成后交付实际变更、验证记录、剩余风险与数据切换状态。

## 12. 核对来源

以下链接列出接入相关代码。框架能力以源码和行为测试为准。

- PurrA 组件说明（外部仓库路径：`purra/integrations/mem0/README.md`）、Python 公共用法（外部仓库路径：`purra/integrations/mem0/python/README.md`）、组件实现（外部仓库路径：`purra/integrations/mem0/python/src/purra_mem0/memory.py`）、持久控制账本（外部仓库路径：`purra/integrations/mem0/python/src/purra_mem0/_journal.py`）、上下文组装（外部仓库路径：`purra/integrations/mem0/python/src/purra_mem0/context.py`）。
- 框架记忆范围记录（外部仓库路径：`purra/docs/plans/2026-08-31-purra-mem0-integration-scope.md`）、中文效果验收（外部仓库路径：`purra/integrations/mem0/EVALUATION.md`）。
- [管理路由](../../backend/routers/memories.py)、[管理 DTO](../../backend/schemas/memories.py)、[唯一应用操作](../../backend/application/memory_operations.py)、[Agent 记忆工具](../../backend/infrastructure/writing/tools/handlers/memory_tools.py)。
- [来源沉淀策略](../../backend/services/memory_deposition_service.py)、[业务来源仓储](../../backend/infrastructure/persistence/writing/sqlite_writing_source_repository.py)、[持久交付](../../backend/application/memory_delivery.py)、[当前 schema](../../backend/database/schema.py)。旧 `long_term_memory_service.py`、`memory_intelligence_service.py`、重复的 `memory_service.py` 和两个 SQLite 通用记忆仓储已删除。
- [Writing Profile](../../backend/application/writing_agent_profile.py)、[当前 Retriever](../../backend/infrastructure/writing/retrieval.py)、[上下文来源](../../backend/application/writing_context_source.py)、[统一记忆上下文](../../backend/domains/writing/unified_memory_context.py)。
- [Story Memory 演化](../../backend/application/story_memory_evolution.py)、[Story Memory 领域账本](../../backend/domains/writing/story_memory_ledger.py)、[统一展示查询](../../backend/application/unified_memory.py)。
- [记忆中心](../../src/Workspace/AiPanel/components/MemoryCenter/index.tsx)、[内联编辑调用](../../src/Workspace/EditorPanel/inlineEditContext.ts)、[内联记忆应用入口](../../backend/application/writing_memory_context.py)。
- [人物删除入口](../../backend/routers/characters.py)、[大纲恢复/删除](../../backend/routers/outlines.py)、[整书删除](../../backend/routers/books.py)、[当前备份/导入](../../backend/routers/files.py)。
- [浏览器备份入口](../../src/platform/browser.ts)、[Electron 数据库 IPC](../../electron/database_ipc.js)、[设置页备份操作](../../src/SettingsPage/useDatabaseActions.ts)、[工具审批策略](../../backend/domains/writing/policies.py)。
- [宿主生命周期](../../backend/main.py)、[分发资源准备](../../scripts/prepare-backend-resources.cjs)、[现有包边界](purra-package-boundary.md)、[所有权章程](purra-screenplay-refactor-charter.md)。

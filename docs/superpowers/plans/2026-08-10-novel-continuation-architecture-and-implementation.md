# 小说续写能力架构与实施计划

状态：已确认方向，仅记录计划，尚未实施

创建日期：2026-08-10

更新日期：2026-08-12
范围：原作导入、蒸馏分析、写作方法提炼、正史快照、续写创作、Continuation Agent 及 Story Memory 组合召回

## 关联规格与职责

- 本文是小说续写能力的总路线图，负责跨子系统依赖、集成顺序和端到端完成定义。
- 写作方法库的产品语义、版本模型、运行机制和退役旧风格基调的详细规则，以 [`../specs/2026-08-12-writing-method-library-design.md`](../specs/2026-08-12-writing-method-library-design.md) 为准。
- 原作事实分析与写法提炼共享同一来源修订和证据档案，但分别产出“继承正史”和“候选写作方法”，两者不得混为一个快照。

## 目标

在不破坏现有原创写作体验的前提下，建立完整闭环：

1. 读取外部作品或冻结现有原创作品；
2. 将来源保存为不可变修订；
3. 按章节蒸馏人物、关系、世界事实、时间线、剧情线和写作技法；
4. 将写法分析转成一组可审核、可发布、可组合的候选写作方法及候选方案；
5. 用户选择章末分叉点并确认截至该点的正史快照；
6. 创建独立续写作品，并由用户明确选择是否绑定已发布的写作方法或方案；
7. Writing Agent 在受控来源范围内继承正史，按作品绑定版本使用写作方法并继续创作；
8. 续写章节只更新续写项目，不反向修改原作。

## 已确认的产品决策

- 书架在入口层分为“原创作品”和“续写作品”。
- 外部原作进入独立“原作库”，不伪装成普通 `book`。
- `books` 只表示正在创作的原创或续写项目。
- 原作分为：只读原文证据、可审核/可纠正的分析档案、不可变正史快照。
- 续写项目拥有独立可编辑的发展层。
- 创建续写时冻结来源修订、分叉章节和正史快照。
- 第一版仅支持章末分叉。
- 分叉点后的原作内容默认对 Continuation Agent 不可见。
- 原作事实不写入目标书的普通 Story Memory。
- 原作硬事实与写法软约束分开管理：硬事实进入正史快照，写法进入写作方法候选。
- 写作方法库是数据库原生的全局资产库，包含自由 Markdown 方法、不可变发布版本和不可拆分的写作方案。
- 方法和方案日常编辑只覆盖草稿，只有用户主动发布才产生版本；作品绑定准确版本且不自动跟随升级。
- 原作分析生成候选方法与候选方案，用户审核并发布后也不会自动绑定续写作品。
- 一本作品可组合多个方案和独立方法；主风格常驻，专项方法按任务选择，用户可用 `/` 控制本轮已绑定方法。
- 推荐只按需触发，Agent 只能提出建议，不能自行绑定、升级、解绑或调整优先级。
- 旧“风格基调”及其现有数据直接删除，不迁移、不转换、不保留兼容读取。
- Obsidian 集成后置；当前仅保留稳定 ID、修订和投影能力。

## 架构边界

### Novel Source

拥有原作、来源修订、卷章结构、原文检索和只读访问。内部原创作品和外部文件都必须先冻结为相同的来源修订，续写不能实时依赖一个可变 `book`。

### Source Analysis

拥有蒸馏任务、事实、证据、技法信号、审核和纠错。模型输出先进入候选 Artifact，经过证据验证后才发布分析版本。分析层的技法卡是带证据的中间产物，不是用户可绑定的写作方法版本。

### Writing Method Library

拥有写作方法草稿和发布版本、写作方案草稿和发布版本、作品绑定、优先级、按需推荐及运行使用记录。它只消费已验证的来源分析引用，不拥有原作原文和正史事实，也不向方法正文复制证据材料。

### Continuation

拥有目标作品与来源之间的绑定、分叉点、正史快照、来源升级比较和继承/续写合并规则。

### Writing

继续拥有章节、大纲、人物、世界设定、伏笔、长期记忆和续写阶段 Story Memory。Writing 不拥有原作来源和继承正史；Writing 产品组合层负责把已解析的作品方法栈注入具体写作请求。

### Continuation Agent

使用独立 Agent profile。复用 Writing 基础操作，但增加宿主锁定的来源范围、只读来源工具、继承正史上下文、已解析写作方法上下文和冲突校验。通用 PurrA 不查询续写或写作方法业务表。

## 核心不变量

1. 来源原文和来源修订不可被 Writing API 修改。
2. 每个分析事实必须绑定真实来源修订、章节和原文片段。
3. 正史快照创建后不可原地修改。
4. 来源新修订不得静默改变已有续写项目。
5. Agent 不能修改 `sourceWorkId`、`sourceRevisionId`、`forkSectionId` 或 `canonSnapshotId`。
6. Continuation Agent 的来源读取不得越过分叉点。
7. 接受续写章节只产生目标书 Story Memory delta。
8. 删除/归档来源不得破坏已冻结的正史快照。
9. 失败或中断的分析不得发布部分正式事实。
10. 原作全文不得作为单个 Prompt 注入；必须按 TaskSpec 和预算召回。
11. 正史快照不得包含写作风格正文；写作方法必须通过独立发布和作品绑定进入运行时。
12. 写作方法或方案发布新版不得静默改变已有续写作品。
13. 写作方案在作品绑定层保持原子性，作品不能替换方案内部成员。
14. `/` 只能选择当前作品已经绑定的方法，且只影响本轮请求。
15. 方法正文不能声明工具权限、修改来源作用域或覆盖系统安全规则。
16. 旧 `book_style` 数据不得迁移到写作方法库。

## 数据模型

### 创作项目

- `books.creation_mode`: `original | continuation`，历史数据默认 `original`。

### 来源

- `novel_source_works`
- `novel_source_revisions`
- `novel_source_sections`

### 分析

- `source_analysis_revisions`
- `source_analysis_facts`
- `source_analysis_evidence`
- `source_craft_cards`
- `source_analysis_corrections`

`source_craft_cards` 保存可追溯的技法分析信号，作为生成候选写作方法的输入，不直接绑定作品。

### 写作方法库

- `writing_methods`
- `writing_method_revisions`
- `writing_schemes`
- `writing_scheme_revisions`
- `book_writing_method_bindings`
- `writing_method_run_receipts`

### 续写

- `continuation_bindings`
- `continuation_canon_snapshots`
- `continuation_canon_records`

## 原作分析管线

1. 确定性解析卷章并让用户预览；
2. 按章节提取事实和技法信号；
3. 跨章节归一人物与实体；
4. 聚合人物状态、关系、时间线、剧情线、伏笔和技法卡；
5. 验证事实与技法证据片段确实存在于绑定章节；
6. 生成待审核分析版本；
7. 用户整体确认并可纠正高风险事实；
8. 原子发布来源分析版本；
9. 从已验证技法卡生成一个或多个主风格候选方法、两至五个专项候选方法及一个候选方案；
10. 用户逐项审核、修改和删选后，原子发布保留的方法与方案。

每个长任务单元绑定 `source_revision_id + section_id + analysis_schema_version`。重启中断返回 `paused/pending`，不消耗业务重试次数。

步骤 8 发布的是来源分析档案，步骤 10 发布的是可复用创作资产。方法正文只保存蒸馏后的指导，原文证据继续留在来源分析档案中。步骤 9 至 10 可以在写作方法库基础能力完成后接入，不阻塞事实分析和正史快照。

## 正史快照

正史快照仅从分叉点及以前的证据构建，包含：

- 人物身份和分叉点当前状态；
- 人物关系；
- 世界硬规则；
- 已发生事件；
- 未解决剧情线与伏笔；
- 人物截至分叉点的认知范围。

依赖分叉点以后证据的总结不得进入快照。

写作方法不属于正史快照。用户可以绑定从同一原作提炼的方法方案，也可以选择其他方法或完全不绑定；任何方法选择都不能改变继承正史。

## Agent 上下文顺序

1. 宿主绑定：目标书、来源修订、分叉点、正史快照；
2. 继承正史硬约束；
3. 已接受的续写 Story Memory；
4. 当前章节、大纲和用户任务；
5. 作品绑定并经宿主解析的主风格方法，以及按任务选中的专项方法；
6. 按需检索的来源事实和原文片段。

同一 `memory_key` 只有在续写章节存在正式变化证据或用户显式批准改写正史时，续写状态才可覆盖继承基线。

## API 初稿

### 原作库

- `GET /api/novel-sources`
- `POST /api/novel-sources/import`
- `POST /api/novel-sources/from-book`
- `GET /api/novel-sources/{id}`
- `GET /api/novel-sources/{id}/revisions/{revisionId}/sections`
- `GET /api/novel-sources/{id}/revisions/{revisionId}/sections/{sectionId}`

### 分析

- `POST /api/novel-sources/{id}/revisions/{revisionId}/analyses`
- `GET /api/source-analyses/{analysisId}`
- `POST /api/source-analyses/{analysisId}/corrections`
- `POST /api/source-analyses/{analysisId}/publish`
- `GET /api/source-analyses/{analysisId}/writing-method-candidates`

### 写作方法库

- `GET /api/writing-methods`
- `POST /api/writing-methods`
- `PUT /api/writing-methods/{methodId}/draft`
- `POST /api/writing-methods/{methodId}/publish`
- `GET /api/writing-schemes`
- `POST /api/writing-schemes`
- `PUT /api/writing-schemes/{schemeId}/draft`
- `POST /api/writing-schemes/{schemeId}/publish`
- `GET /api/books/{bookId}/writing-method-bindings`
- `PUT /api/books/{bookId}/writing-method-bindings`
- `POST /api/books/{bookId}/writing-method-recommendations`

### 续写

- `POST /api/continuations/preview-snapshot`
- `POST /api/continuations`
- `GET /api/continuations/by-book/{bookId}`
- `GET /api/continuations/{bookId}/canon`
- `POST /api/continuations/{bookId}/source-upgrade-preview`

## 实施依赖

```text
阶段 1：共同契约与数据库骨架
├── 阶段 2：写作方法库基础 ───────────────┐
└── 阶段 3：原作导入 ─→ 阶段 4：来源分析 ─┼→ 原作写法候选审核发布
                                      │
阶段 4：事实分析 ─→ 阶段 5：续写项目 ─────┤
阶段 2：方法库 ─→ 阶段 6：作品方法运行时 ─┤
                                      ↓
                              阶段 7：Continuation Agent
                                      ↓
                              阶段 8：Story Memory 组合层
                                      ↓
                              阶段 9：端到端验证
```

- 阶段 2 与阶段 3 在共同契约落定后可以并行。
- 原作事实分析、正史快照和续写项目不依赖候选写作方法已经发布。
- “从原作提炼并发布写作方法”同时依赖阶段 2 的方法库能力和阶段 4 的已验证技法分析。
- Continuation Agent 同时消费阶段 5 的继承正史与阶段 6 的作品方法解析结果，但两类上下文保持独立。

## 实施阶段

### 阶段 1：设计契约和数据库骨架

- [x] 固化续写总架构、边界和不变量。
- [x] 固化写作方法库产品语义及其与续写的集成边界。
- [ ] 增加 Novel Source、Source Analysis、Continuation 和 Writing Method Library 的领域契约与 Repository ports。
- [ ] 增加续写和写作方法库 SQLite 表；除明确退役的 `book_style` 外，其余迁移不得破坏历史创作数据。
- [ ] 增加 `books.creation_mode`，并将历史书籍迁移为 `original`。
- [ ] 在删除旧风格基调前列出界面、API、工具、Prompt、Screenplay 查询和数据库表的准确调用点。
- [ ] 增加模式升级幂等性、版本引用完整性和历史书籍兼容测试。

### 阶段 2：写作方法库基础与旧风格退役

- [ ] 实现自由 Markdown 方法草稿、草稿修订号校验和手动发布不可变版本。
- [ ] 实现写作方案草稿、准确方法版本清单和原子发布。
- [ ] 实现内置内容只读、复制自定义、归档和被引用版本不可删除。
- [ ] 实现作品绑定、顶层优先级、方案原子性和手动版本升级。
- [ ] 将现有 `WritingSkillCatalog` 重命名为 `WritingToolDefinitionCatalog`，保持 Agent 工具声明与创作方法分离。
- [ ] 删除风格基调界面、API、Schema、CRUD、`getBookStyle` 工具、全部注入路径和 `book_style` 数据，不进行迁移。
- [ ] 增加方法库和作品绑定的 Web/Electron 共用 API 与回归测试。

### 阶段 3：原作导入与只读查询

- [ ] 实现 TXT/Markdown 导入。
- [ ] 实现 EPUB 导入。
- [ ] 实现章节解析预览与确认。
- [ ] 从现有原创作品冻结来源修订。
- [ ] 实现来源全文检索、修订锁定和分叉范围校验。
- [ ] 接入 Electron/Web 文件入口。

### 阶段 4：证据化来源分析与写法候选

- [ ] 定义事实、证据、技法卡和覆盖范围的分析 Artifact schema。
- [ ] 建立以 `source_revision_id + section_id + analysis_schema_version` 为单位的可恢复长任务 recipe。
- [ ] 实现逐章事实与技法提取、人物/实体归一及跨章聚合。
- [ ] 验证证据片段、来源修订、覆盖范围和分叉点后信息泄漏。
- [ ] 实现分析审核、纠错和正式分析版本的原子发布。
- [ ] 将已验证技法卡转换为一个或多个主风格候选方法、两至五个专项候选方法及一个候选方案。
- [ ] 实现候选方法和方案的逐项审核、修改、删选及原子发布；不复制原文证据，不自动绑定作品。

### 阶段 5：续写项目与正史快照

- [ ] 实现原创/续写双书架。
- [ ] 实现原作库和新建续写向导。
- [ ] 实现章末分叉点选择。
- [ ] 实现只含硬事实的正史快照预览，不混入写作方法正文。
- [ ] 原子创建目标 `book + continuation binding + canon snapshot`。
- [ ] 允许用户在创建时选择已发布的方法或完整方案，但不默认绑定原作提炼结果。
- [ ] 在工作台显示来源、来源修订、分叉点和独立的写作方法入口。

### 阶段 6：作品方法运行时与交互

- [ ] 实现方案展开、重复版本去重、多版本冲突阻断和顶层优先级解析。
- [ ] 每次写作常驻主风格方法，并按任务从轻量目录中选择少量专项方法。
- [ ] 在 AI 输入框实现 `/`“选择本次写法”，只允许当前作品已绑定方法的本轮强制使用或排除。
- [ ] 保存每次 Agent 运行实际使用、自动选择、强制使用和排除的方法版本记录。
- [ ] 实现按需推荐；Agent 只给出建议，用户确认后才修改绑定。
- [ ] 实现上下文预算降级：先减少自动专项方法，不截断方法正文；主风格超限时明确报错。

### 阶段 7：Continuation Agent

- [ ] 注册独立 profile 和宿主拥有的 DomainContext。
- [ ] 实现受控来源、证据和正史只读工具。
- [ ] 复用 Writing 操作，但只允许写目标续写书籍。
- [ ] 确定性注入继承正史、续写 Story Memory 和阶段 6 解析出的写作方法版本。
- [ ] 保存来源读取收据和写作方法使用记录。
- [ ] 增加 Prompt Injection、来源越界、方法权限越界和分叉点后内容泄漏测试。

### 阶段 8：Story Memory 组合层

- [ ] 实现继承快照与目标 Story Memory 的组合召回。
- [ ] 让续写章节分析使用组合基线，但只向目标书产生 Story Memory delta。
- [ ] 实现硬事实冲突提示。
- [ ] 实现用户显式批准的改写正史流程。
- [ ] 实现来源更新比较和手动升级，既不静默改变正史，也不静默升级写作方法。

### 阶段 9：验证与收尾

- [ ] 验证迁移、删除、归档和被引用版本生命周期。
- [ ] 验证中断恢复、重复请求和原子发布幂等性。
- [ ] 验证多续写分支、多个写作方案和方法版本隔离。
- [ ] 验证通用 Agent Core 不依赖续写或写作方法业务表。
- [ ] 验证旧风格基调不存在任何残留读取或注入路径。
- [ ] 运行前端类型检查、单元测试、后端完整测试和 Agent 架构门禁。
- [ ] 在 Web 与 Electron 中实际验证完整流程，并停止所有为测试启动的进程。

## 完成定义

只有以下闭环真实可用，才视为小说续写与写作方法集成完成：

```text
导入/冻结原作
→ 发布证据化来源分析
├→ 生成、审核并发布一组候选写作方法与候选方案（可选）
└→ 选择章末分叉点并创建不可变正史快照
→ 创建独立续写作品
→ 用户明确绑定方法或完整方案（可选）
→ Agent 按需读取继承正史，并按准确版本使用写作方法
→ 用户接受后只更新续写 Story Memory
→ 方法或来源发布新版均不静默改变已有续写作品
→ 应用重启后任务、快照、方法版本和创作状态仍然一致
```

同时必须满足：旧风格基调及其数据已经删除；原作证据只留在来源分析档案；正史快照只保存硬事实；通用 Agent Core 不拥有任何续写或写作方法业务规则。

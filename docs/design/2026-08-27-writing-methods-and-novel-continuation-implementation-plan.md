# 写作方法与小说续写实施计划（当前架构重基线）

状态：待实施；本文替代 2026-08-10 的旧执行计划，但保留已经确认的产品决策

日期：2026-08-27

范围：写作方法库、旧风格基调退役、只读原作、证据化分析、正史快照、续写项目、Writing Agent 运行时与 Story Memory 组合召回

## 1. 结论

这不是一次可以安全地合并完成的“小功能”。它包含两个可以独立验收、最后再集成的子系统：

1. **写作方法**：全局方法与方案、不可变发布版本、作品绑定、本轮选择、按需推荐和运行可追溯性；
2. **小说续写**：冻结来源、分析与审核、章末分叉、正史快照、独立续写作品和继承上下文。

实施顺序采用“写作方法基础先行，来源管线随后，最后集成”的方式。来源导入与分析的底层工作不依赖写作方法，可以在独立分支并行；但在一个对话、一个工作树中执行时，应按本文阶段顺序推进，避免同时改动数据库、写作请求和 Agent 组合根。

## 2. 当前代码事实

以下是 2026-08-27 仓库的真实接缝，实施时不得再按旧计划猜测：

- `backend/database/schema.py` 仍是主数据库初始化入口，但剧本新聚合已经用独立 schema 模块接入；新领域也应使用独立 schema 模块，而不是继续扩大主文件。
- `books` 当前没有创作模式；书架只有一个列表，创建书籍仍直接调用 `POST /api/books`。
- Writing Agent 已经由 `WritingAgentProfile`、`WritingDomainContext`、规划上下文、TaskSpec 后检索上下文、证据回执和统一 Run 账本组成。
- `RepositoryWritingContextSource` 已把 Story Memory 和普通长期记忆合并为 `MemoryContextPack`；继承正史应扩展这条组合链，不能在路由或前端拼 Prompt。
- `ai_agent_runs.binding_attributes_json` 已经是不可变的宿主 Run 绑定快照；它适合冻结可用方法版本栈和用户本轮选择，但 TaskSpec 之后才决定的实际注入版本必须进入现有 Context Evidence Receipt，不能伪装成 Run 创建前已知的数据。
- Writing Profile 当前不派发 durable long task；剧本 Profile 已经展示了 Profile、Recipe、Artifact、来源范围和证据读取回执的可恢复实现方式。
- 共享 Agent 输入器只提供输入值、提交和少量插槽；`/` 选择器需要先扩展共享 Composer 的业务扩展点，不能把作品逻辑硬编码进共享组件。
- Electron 与 Web 已有 TXT/Markdown 文本选择能力；仓库只有 EPUB 导出，没有 EPUB 导入解析链。
- 旧“风格基调”仍完整存在于界面、API、数据库、Writing 工具和 Screenplay 来源工具中，不能只删除表或表单。
- 当前工作树可能包含与剧本 Agent 和执行进度相关的未提交改动。实施者必须先检查并保留这些改动，不得借本需求重写或清理它们。

## 3. 纠正旧计划中的三个假设

### 3.1 续写不首发独立 Agent Profile

续写创作仍然使用现有章节、工作台、会话、Writing 工具、提案、正文接受和 Story Memory 更新闭环。为它复制一个 Continuation Profile 会造成两套 Writing 行为逐渐漂移。

首版做法是：

- `WritingAgentProfile.prepare_request()` 根据服务器查询到的 `books.creation_mode` 和有效 `continuation_binding`，权威地补全续写上下文；
- Writing 工具目录只在绑定校验通过时开放只读来源工具；所有写工具继续被目标 `book_id` 限定；
- Run binding 记录 `creationMode`、continuation binding 身份、正史快照身份、可用方法版本栈和用户本轮选择；实际注入版本由 Context Evidence Receipt 记录；
- 普通原创书完全不经过续写查询和上下文注入。

只有“逐章分析整部来源作品”具有独立的 durable 生命周期，因此它使用单独的 `NovelAnalysisAgentProfile`。如果将来续写创作本身需要独立 durable recipe，再以测量到的需求增加 Profile，而不是首版预建。

### 3.2 不新增写作方法 Run 收据表

旧计划中的 `writing_method_run_receipts` 取消，但不能把两个不同时点混成一个字段：

1. Run 创建时，在 `ai_agent_runs.binding_attributes_json.writingMethodBindingSnapshot` 冻结：

- schema version；
- 作品方法栈修订摘要；
- 当时全部可用的准确 method/scheme revision ID；
- 用户本轮强制和排除的 revision ID；
- 绑定快照 digest。

2. TaskSpec 形成、宿主选择实际方法并构建模型上下文时，为每个真正注入的方法添加 `CONTEXT_EVIDENCE_RECEIPTS_KEY` 回执，至少包含 method revision ID、来源方案 revision ID、`primary | task_selected | user_forced` 原因和内容 digest。回执进入现有 `RunEvidenceStore`，随 `execution_checkpoint_json.evidence_state` 持久化并用于恢复和历史读取。

历史界面组合读取 binding snapshot 和 context receipts：前者回答“本轮允许使用什么”，后者回答“实际给模型用了什么”。二者职责不重叠，不维护新的业务收据表。

### 3.3 EPUB 不进入首版闭环

首版来源只支持：

- TXT；
- Markdown；
- 从现有 PurrTypos 作品冻结。

EPUB 导入在 TXT/Markdown 全流程通过后另立任务。原因是当前只有 EPUB 导出，导入还需要容器解析、HTML 清洗、目录与正文排序、编码和异常书籍测试；这些工作不影响“冻结原作 → 分析 → 正史快照 → 续写”的核心价值验证。

## 4. 保持不变的产品决策

- 书架入口明确区分“原创作品”和“续写作品”。
- 小说来源库和写作方法库属于书架内的创作资源，不作为产品顶级首页入口。
- 外部原作是只读来源，不伪装成普通 `book`。
- 来源原文、可审核分析、不可变正史快照和可编辑续写发展层相互分离。
- 首版只允许在章末分叉，分叉点后的来源正文默认不可见。
- 创建续写时冻结来源 revision、分叉 section 和 canon snapshot；后续来源更新不自动改变续写。
- 来源事实不复制进目标书普通 Story Memory；召回时组合继承正史和续写自身 Story Memory。
- 正史只保存硬事实；风格和技法进入写作方法，不混入正史快照。
- 产品使用“写作方法库 / 写作方法 / 写作方案”，不使用 Skill 作为用户概念。
- 方法和方案只有主动发布才生成不可变版本；作品准确绑定版本且不自动升级。
- 方案在作品绑定层不可拆，用户可以创建或复制自定义方案。
- `/` 只控制本书已绑定方法的本轮强制使用或排除，不修改长期绑定。
- 推荐只按需发生；Agent 可以推荐，不能绑定、升级、解绑或改优先级。
- 来源分析生成候选方法和候选方案，用户审核发布后也不自动绑定。
- 旧 `book_style` 数据和功能直接删除，不迁移、不转换、不保留兼容读取。
- 通用 PurrA 保持产品中立，不查询来源、续写或写作方法业务表。

## 5. 目标架构

```text
全局写作方法库
  ├─ 方法草稿 ──发布──> 不可变方法版本
  ├─ 方案草稿 ──发布──> 不可变方案版本 + 有序成员
  └─ 作品顶层绑定 ──解析──> 本次准确方法栈
                                  │
只读来源 ──> 证据化分析 ──> 正史快照 ──> 续写绑定
    │              └──────> 候选方法/方案
    │
    └─受限检索──────────────────────┐
                                    ↓
Writing Profile: 规划事实 → TaskSpec → 组合召回 → 写作/修改
                                    │
                                    ├─ immutable Run binding snapshot
                                    ├─ durable context evidence receipts
                                    └─ 只更新目标书 Story Memory
```

### 5.1 信任和权限边界

- 来源正文、来源分析文本和用户方法 Markdown 都可能包含提示注入内容，作为不可信内容块传给模型。
- 宿主另提供可信策略块，声明它们只能作为故事事实或创作指导，不能获得工具权限、改变书籍范围或覆盖系统规则。
- 正史是“故事连续性上的权威”，不是“系统指令上的可信输入”。
- 任何来源读取都必须从服务器冻结的 continuation binding 推导范围，不能接受模型或前端传入的任意来源 ID。
- 方法解析、方案展开、版本冲突和上下文预算全部由宿主完成；模型只接收解析结果。

### 5.2 容量、版权和外部模型边界

- 真正可能造成存储压力的不是人工发布的短方法版本，而是完整来源 revision。来源不能自动快照；只有用户明确导入或再次冻结时才创建新 revision。
- 导入确认页显示本次字符数/字节数和预计新增存储；被续写绑定引用的 revision 不可删除，未引用 revision 才允许用户明确删除。
- 首版不预建内容寻址 blob 表。先测量真实 revision 数量和重复率；只有重复正文造成可观体积后，才引入 section digest 去重。
- 产品需要提示用户只导入其有权使用的作品。继续分析时会把受限原文片段发送给用户配置的外部模型服务，这一数据流必须在分析启动前明确展示。
- 任何模型调用都只发送当前 Unit 或 TaskSpec 所需片段，不以“模型上下文够大”为理由上传整部作品。

## 6. 数据模型

### 6.1 写作方法

采用以下六张业务表，不新增运行收据表：

1. `writing_methods`
   - 方法身份、名称、简介、`primary | technique`、标签、来源、内置标记、状态；
   - 当前可变草稿 Markdown、草稿元数据、`draft_revision` 和当前发布 revision ID。
2. `writing_method_revisions`
   - 不可变发布版本、版本号、Markdown、元数据快照、content digest、发布时间；
   - `(method_id, version_no)` 唯一。
3. `writing_schemes`
   - 方案身份、简介、可变草稿成员 JSON、`draft_revision`、来源、内置标记、状态和当前发布 revision ID。
4. `writing_scheme_revisions`
   - 不可变方案版本、版本号、元数据快照、成员清单 digest 和发布时间。
5. `writing_scheme_revision_members`
   - 发布方案的有序成员；保存 scheme revision ID、ordinal、准确 method revision ID；
   - 使用普通关系表而不是只存 JSON，便于引用检查、删除保护和按版本查询。
6. `book_writing_method_bindings`
   - 作品顶层绑定；一行只能引用一个 method revision 或一个 scheme revision；
   - 保存 priority、来源和创建时间；方案成员不能在此层替换。

当前数据库没有启用外键，Repository 必须在同一事务内显式验证引用，并由删除测试覆盖所有手动级联/保护行为。

### 6.2 来源、分析和续写

新增：

- `books.creation_mode`: `original | continuation`，历史书籍幂等回填为 `original`；
- `novel_source_works`：来源身份、标题、来源类型、可选 origin book ID、归档状态；
- `novel_source_revisions`：不可变来源 revision、整体 digest、解析器版本、来源元数据；
- `novel_source_sections`：不可变卷章结构、ordinal、标题、正文、正文 digest；
- `novel_source_analyses`：已发布分析 revision 和覆盖范围；
- `novel_source_analysis_facts`：规范化硬事实与生命周期信息；
- `novel_source_analysis_evidence`：事实或技法卡到来源 section、原文 excerpt 和 locator 的证据；
- `novel_source_craft_cards`：已验证的风格/技法分析中间产物；
- `continuation_canon_snapshots`：分叉点的不可变快照身份、digest、来源 analysis revision；
- `continuation_canon_records`：快照内硬事实及其来源 fact ID；
- `continuation_bindings`：目标 book、来源 revision、fork section、canon snapshot 的一对一绑定。

不新增专用“分析运行表”。分析执行状态、单元、重试、Artifact 和运行关系复用现有：

- `ai_agent_runs`；
- `ai_agent_long_tasks` / `ai_agent_long_task_units`；
- `ai_agent_artifacts` / batches / projections；
- 不可变 Run binding 和事件日志。

分析候选先进入通用 Artifact；只有用户发布时，才在一个业务事务中生成 `novel_source_analyses` 及其 facts/evidence/craft cards。失败、中断或部分完成不能形成正式分析 revision。

## 7. 请求和上下文契约

### 7.1 前端请求

`ChatStreamRequest`、请求 digest、前端发送队列和重发冻结上下文共同增加结构化字段：

```text
writingMethodOverrides:
  forceRevisionIds: string[]
  excludeRevisionIds: string[]
```

前端只传本轮选择。服务器根据 `bookId` 重新读取作品绑定，拒绝未绑定 revision、同一 revision 同时强制和排除、同方法多版本以及已经失效的绑定。

### 7.2 WritingDomainContext

宿主准备阶段增加两组不可由前端直接构造的字段：

- `writing_method_binding_snapshot`：准确可用版本栈、本轮强制/排除和 digest；
- `writing_method_resolution`：TaskSpec 后实际选中的版本、正文、原因和 digest；
- `continuation_context`：绑定身份、来源范围、canon snapshot 身份和只读检索句柄。

序列化进入 PurrA 的 `DomainContext` 只是已校验快照；PurrA 不解释其业务含义。

### 7.3 上下文块

在现有 Writing ContextProvider 增加：

- `writing_method_policy`：可信宿主策略，声明优先级和权限边界；
- `writing_methods`：不可信 Markdown 内容，包含准确 revision 身份；
- `inherited_canon_policy`：可信宿主策略，声明正史的故事权威和不可越权性质；
- `inherited_canon`：按 TaskSpec 召回的只读正史记录；
- `source_evidence`：仅在 TaskSpec 明确需要时，通过受限工具或检索加入的来源片段。

预算顺序：安全/绑定策略不可丢失；继承正史与当前目标优先于自动专项方法；先减少自动选择的 technique，再拒绝超限的 primary，不能静默截断一篇方法正文后继续运行。

`writing_methods` 上下文块必须为每个实际注入 revision 生成 Context Evidence Receipt。历史回放通过 PurrA 的 `RunEvidenceStore.from_checkpoint_mapping()` 读取，不直接手写解析 checkpoint JSON。

### 7.4 Run binding

扩展 Profile 宿主钩子，使 `AgentComposition.bind_run_profile()` 可以合并 Profile 提供的不可变 binding attributes，但通用组合层只处理 JSON 映射，不理解字段：

- `agentProfile` / `domainNamespace`；
- `creationMode`；
- `continuationBinding` 摘要；
- `writingMethodBindingSnapshot`；
- 对应 schema version 和 digest。

实际 `writingMethodResolution` 不能写入这里，因为自动 technique 选择发生在 TaskSpec 之后、Run binding 已经不可变。它只出现在 durable context evidence receipts 和宿主诊断中。

## 8. 来源冻结和分析

### 8.1 外部文本导入

- 新增独立 platform API，返回文件名、扩展名和正文，不修改现有 `openAndReadTextFile()` 的返回契约；
- 后端限制允许的扩展、字符数/字节数和编码结果；
- 导入预览显示新增存储和“分析会向已配置模型发送所需片段”的提示；
- 确定性解析章节标题，先显示预览，用户确认后才在一个事务中写入 work、revision、sections；
- 无法可靠识别章节时允许用户确认“单节来源”，不能让模型偷偷决定结构。

### 8.2 从现有作品冻结

在一个数据库写事务内读取书籍、写作目录、章节顺序和正文并写入不可变来源 revision，避免冻结过程中正文发生变化。删除原始 `book` 后，冻结 revision 仍然完整可读。

### 8.3 durable 分析 recipe

`NovelAnalysisAgentProfile` 只负责来源分析，不拥有续写正文写权限。最小 recipe：

1. 每 section 生成事实候选和技法候选；
2. 分批归一人物、实体和时间线；
3. 聚合剧情线、未解决伏笔和人物认知；
4. 验证每个候选 excerpt 确实存在于绑定 section；
5. 生成覆盖率和冲突报告；
6. 形成待审核 Artifact；
7. 用户纠正后发布不可变分析 revision。

每个 Unit 固定 `sourceRevisionId + sectionId + analysisSchemaVersion`。来源读取工具只接受宿主绑定的 section 范围，并生成来源证据回执。正文中的指令性文字不得改变 recipe、工具权限或来源范围。

## 9. 正史快照和续写项目

### 9.1 快照内容

只允许分叉 section 及以前证据支持的：

- 人物身份与当时状态；
- 人物关系；
- 世界硬规则；
- 已发生事件和时间线；
- 未解决剧情线与伏笔；
- 人物截至分叉点的认知范围。

技法卡、方法 Markdown 和分叉点后的事实都不能进入快照。

### 9.2 原子创建

创建续写必须在一个 cancellation-linearizable 事务中完成：

1. 再次校验来源 revision、分析 revision、fork section 和 snapshot digest；
2. 创建 `books(creation_mode='continuation')`；
3. 创建写作目录；
4. 写入 continuation binding 和 canon snapshot/records；
5. 可选绑定用户明确选择的已发布方法或完整方案；
6. 返回完整创建回执。

任一步失败都不能留下孤立书籍或半个快照。

### 9.3 组合 Story Memory

给现有 `UnifiedMemoryRetriever` 增加继承正史 provider：

- 对原创作品保持当前 Story Memory + semantic memory 行为；
- 对续写作品先召回继承正史，再召回目标书 Story Memory 和普通长期记忆；
- 目标书已确认状态可以在明确证据或用户批准下覆盖继承基线，但不能物理修改快照；
- 章节分析以组合基线做冲突判断，产生的 delta 仍只写目标 `book_id`。

## 10. 界面

### 10.1 全局写作方法库

书架的“创作资源”增加“写作方法库”入口，保留 `/writing-methods` 路由作为页面地址，但不在产品顶级首页单列，提供：

- 方法、方案两个页面；
- 引导创建和空白 Markdown 创建；
- 草稿自动保存、发布确认、版本历史；
- 内置内容只读和复制；
- 归档、删除保护和引用状态；
- 从原作分析生成的候选审核入口。

### 10.2 作品内写作方法

用“写作方法”面板替换旧“风格基调”面板，展示顶层方案/方法绑定、排序、解除、手动升级和按需推荐。

### 10.3 `/` 本轮选择

共享 Composer 只增加通用命令菜单扩展点；作品扩展负责：

- 监听当前光标附近的 `/`；
- 只列出服务器返回的当前书已绑定方法；
- 设置“本轮使用”或“本轮排除”；
- 用结构化状态随发送队列、重发和会话切换冻结；
- 不把选择编码进用户正文。

### 10.4 双书架与续写向导

书架页内部显示“原创作品”和“续写作品”两个明确分区/页签：

- 书架工具区提供小说来源库和写作方法库入口，顶级首页不重复展示；
- 书架、小说来源库、写作方法库和新建续写作品页复用统一的应用页头、页面留白、容器宽度、卡片与响应式视觉规则；
- 原创入口继续使用现有创建流程；
- 续写入口进入“选择/导入来源 → 预览章节 → 分析审核 → 选择章末分叉 → 预览正史 → 创建续写”；
- 续写卡片显示来源和分叉点摘要；
- 工作台显示只读的“继承正史”入口和独立的“写作方法”入口。

## 11. 旧风格基调退役清单

这是已经获得产品授权的破坏性迁移，但仍必须按顺序执行：先完成替代入口和调用清理，再 `DROP TABLE IF EXISTS book_style`。

删除或修改的当前准确接缝：

- Backend：
  - `backend/database/crud/book_style.py`
  - `backend/schemas/book_style.py`
  - `backend/routers/book_style.py`
  - `backend/infrastructure/writing/tools/handlers/book_style_tools.py`
  - `backend/skills/getBookStyle/SKILL.md`
  - Writing tool catalog、policy、cache、display name 和 handler 注册
  - `backend/main.py` 的 router 注册
  - `backend/routers/books.py` 的删除级联
  - `backend/database/schema.py` 的建表，替换为一次性幂等 drop
- Screenplay：
  - 删除 `readSourceStyle` schema、描述、显示名、工具集合、query 和来源回执映射；
  - 不自动改成读取作品绑定的写作方法。剧本原作风格应从实际来源文本分析；方法绑定是创作指导，不等于来源证据。
- Frontend：
  - `src/Workspace/DirectorNotebook/StyleForm.tsx`
  - `src/Workspace/utilityPanelTypes.ts` 的 style tab
  - `src/Workspace/WorkspaceUtilityPanel.tsx` 的 style 分支
  - 对应样式、types、services 和 tool-call labels
- Tests：
  - 删除只验证旧功能存在的用例；
  - 把数据完整性、工具目录和 Screenplay 断言改成验证旧路径完全不存在。

注意：Memory Center 中通用记忆 kind `style` 不是 `book_style`，不能因为名称相似而删除。

内部 `WritingSkillCatalog` 仍然准确描述 `backend/skills/*/SKILL.md` 工具声明加载器，且不出现在产品 UI。它的重命名不再作为本功能门禁；若团队仍想减少术语歧义，应在功能完成后做独立机械重构，不能与写作方法业务混在同一提交。

## 12. 实施阶段

### Phase 0：基线与保护

- [x] 记录 `git status --short` 和当前分支，标明本需求外的脏文件并保持不动。
- [x] 运行当前 Writing、Story Memory、书架、共享 Agent 组件和架构边界的基线测试。
- [x] 确认当前数据库备份/导入仍可用，记录破坏性 `book_style` 删除的验收样本。
- [x] 把本文中的文件名与代码再次核对；若已漂移，先更新计划再实现。

2026-08-27 实施审计记录：当前分支为 `feat/0.6`，开始实施时
`git status --short` 无输出。Writing/Story Memory/Composition focused tests
171 个、数据库导出导入与数据完整性 10 个、Agent/PurrA 边界 53 个、
Writing/Screenplay 工具与生命周期 143 个、共享 Composer/发送队列前端测试
94 个均通过，`npm run typecheck` 通过。数据库破坏性验收沿用
`test_data_integrity_routes.py` 中的旧 `book_style` 行样本，并以
`test_database_web_import.py` 覆盖导入后连接恢复。当前实现中
`RepositoryWritingContextSource` 位于 `backend/domains/writing/context_source.py`；
共享 Composer 已有渲染插槽但没有结构化命令菜单状态；这些是后续阶段的实际接缝。

门禁：基线失败必须区分“既有失败”和“本需求引入”，不能把既有失败当成本需求通过。

### Phase 1：写作方法持久化与 API

- [x] 新建独立 writing-method schema 初始化模块并从 `init_schema()` 调用。
- [x] 实现方法/方案 Repository、应用服务和 Pydantic wire contracts。
- [x] 实现草稿乐观并发、不可变发布、原子批量发布、复制、归档和删除保护。
- [x] 实现已发布方案的规范化有序成员和准确 revision 校验。
- [x] 实现作品顶层绑定、排序、解除和手动升级。
- [x] 使用确定性 ID `INSERT OR IGNORE` 初始化少量内置方法/方案；升级不能改写已经存在的版本。

实施门禁：`test_writing_methods.py` 覆盖幂等 schema、内置只读/复制、
草稿并发、不可变发布、方案成员冻结、批量发布回滚、引用保护和准确版本绑定；
连同生命周期、数据库导入导出与边界用例共 55 个通过，Writing/Application
边界 7 个通过，Python compileall 与 `git diff --check` 通过。首次门禁曾因新增
router 使生命周期固定计数从 21 变为 22 而失败，更新正式路由断言后重跑通过。

门禁：后端 focused tests 覆盖版本冻结、并发冲突、事务回滚、方案原子性、引用保护和幂等 schema。

### Phase 2：写作方法界面与旧风格退役

- [x] 增加全局 `/writing-methods` 页面、书架内入口和前端服务类型；顶级首页不单列。
- [x] 实现方法/方案草稿、Markdown 编辑、发布和版本查看。
- [x] 在作品工作台用方法绑定面板替换风格基调。
- [x] 完成第 11 节全部删除接缝；Screenplay 同时删除 `readSourceStyle`。
- [x] 最后执行幂等 `DROP TABLE IF EXISTS book_style`，明确验证旧行已不存在。

实施门禁：运行时代码反向搜索不再包含旧表/API/工具/界面名称；
破坏性迁移测试用旧表和旧行启动数据库，确认升级后表与数据均不存在且未迁移。
Writing/Screenplay/生命周期 focused 后端 149 个通过，Screenplay 完整相关组
291 个通过，`npm run typecheck` 通过，`npm run test:unit` 397 个通过，
`git diff --check` 通过。首次退役门禁发现工具数量和重规划用例仍有旧硬编码，
已改为 35 个现行 Writing 工具和故事背景证据后重跑通过；Memory Center 的通用
`style` 记忆类型未改动。

门禁：`rg` 不再发现运行时 `book_style`、`getBookStyle`、`saveBookStyle`、`readSourceStyle` 或“风格基调”；允许历史文档出现。前端 typecheck、相关单测和后端完整旧风格退役测试通过。

### Phase 3：方法运行时与不可变 Run 绑定

- [x] 在请求、队列、重发和 request digest 中加入本轮方法 overrides。
- [x] 在 Writing Profile 准备阶段由服务器解析作品方法栈。
- [x] 在 Writing ContextProvider 增加方法策略和正文上下文块、预算与冲突失败。
- [x] 增加 Profile 提供 binding attributes 的产品中立宿主钩子。
- [x] 从 Run binding 读取可用版本栈，从 durable Context Evidence Receipt 读取历史实际方法版本；不创建平行 receipt 表。

实施门禁：新增确定性方法栈解析、同一方法多版本冲突、未绑定 override、
强制方法预算不足、TaskSpec 技法选择、request digest、队列冻结、Run binding
版本摘要和 PurrA typed checkpoint receipt 还原测试。方法运行时 6 个、写作方法
领域/API 8 个、Writing/Application/Agent 边界 41 个、相关 Agent/上下文/请求组
131 个、共享 Composer 行为 18 个均通过；`npm run typecheck` 通过。方法正文
仅进入不可信 ContextBlock，Run binding 不保存正文，未新增 receipt 表，也未修改
通用 PurrA 业务依赖。

门禁：同一 binding snapshot + TaskSpec 得到相同 resolution digest；未绑定 revision、冲突版本和超限 primary 必须在模型调用前失败；每个实际注入 revision 都有 durable context receipt；发布新版不改变旧 Run 或旧作品绑定。

### Phase 4：`/` 选择器与按需推荐

- [x] 给共享 Composer 增加最小通用命令菜单扩展点。
- [x] 实现作品级 `/` 菜单、强制/排除状态和发送队列冻结。
- [x] 增加只读方法目录检索能力，仅在明确点击“推荐”或结构化 recommendation 请求时开放。
- [x] Agent 返回建议和理由；绑定仍由用户确认后的普通 API 命令完成。

实施门禁：共享 Composer 只接收产品中立 command item；Writing 扩展只从当前
作品顶层绑定展开准确 method revision，状态按“自动选择 → 本轮强制 → 本轮排除”
循环并在发送后清空，队列和重发继续使用冻结 envelope。只读
`searchWritingMethods` 仅在服务端识别到 `[写作方法推荐]` 结构化请求时进入本轮
enabled tool 集，返回目录元数据而不返回 Markdown，也没有任何绑定写工具。
相关后端 165 个、Composer/队列行为 19 个、override 纯逻辑 2 个通过，
`npm run typecheck` 通过。

门禁：未绑定方法绝不出现在 `/` 菜单或本轮请求中；普通写作不自动触发推荐；Agent 无写绑定工具。

### Phase 5：只读来源与冻结 revision

- [x] 新建 continuation/source schema 初始化模块及 Repository。
- [x] 实现 TXT/Markdown platform picker、导入限制、章节解析预览和确认。
- [x] 在确认页显示来源大小、数据发送边界和使用权提示；不默认创建后续 revision。
- [x] 实现从现有作品的事务内冻结。
- [x] 实现来源列表、revision/section 只读 API、FTS 搜索和 LIKE fallback。
- [x] 增加来源归档和删除保护；已有 continuation binding 引用的 revision 不可删除。

实施门禁：新增独立 continuation/source schema，历史书籍幂等回填
`creation_mode='original'`；确定性标题解析保留单节人工确认回退。预览只计算
体积、digest 和章节，不写数据库；确认导入和作品冻结才在 cancellation-linearizable
事务中写不可变 revision/sections。Electron 独立 picker 返回文件身份、扩展名、
字节数和 UTF-8 正文，未改变旧 `openAndReadTextFile`。来源相关及数据库/生命周期/
边界测试 63 个、Electron picker 2 个通过，`npm run typecheck` 和
`git diff --check` 通过；未加入 EPUB、Obsidian 或向量库。

门禁：重复导入幂等策略明确；来源 section 不可经任何 Writing API 修改；删除原始书不影响已冻结来源；未确认前不向模型发送来源正文。

### Phase 6：证据化来源分析

- [x] 注册只服务于分析的 `NovelAnalysisAgentProfile`。
- [x] 定义 Recipe、Unit、Artifact schema、来源只读工具和证据回执。
- [x] 实现逐章提取、跨章归一、聚合、证据存在性校验和覆盖率报告。
- [x] 实现审核/纠正工作流和正式分析 revision 原子发布。
- [x] 验证暂停、恢复、取消、重试和应用重启后的任务一致性。

实施门禁：独立 `novel_analysis` Profile 已注册到现有 Composition，仅暴露
空的 Core 工具目录；实际来源读取通过宿主绑定的只读 section reader 完成，
每次读取生成 revision/section/scope digest 回执，正文中的提示注入不会改变
Recipe 或范围。宿主按 section 编译 extract → normalize → aggregate → validate →
coverage → review Artifact DAG，沿用 `ai_agent_runs`、LongTask Unit 和正式
Artifact，未增加分析运行表。未完成 LongTask 的 Artifact 发布会失败，审核修订
生成新 Artifact；正式发布事务再次逐字校验 excerpt，并把 facts、craft cards 和
evidence 原子写成不可变 analysis revision。Phase 6/来源/Profile/数据库/生命周期/
架构边界 focused 后端 109 个通过，`npm run test:unit` 399 个、
`npm run check:agent-refactor-boundaries` 53 个、`npm run typecheck` 通过，
暂停、重启恢复、取消和失败重试均有持久化用例，`git diff --check` 通过。

门禁：未完成 Artifact 不能发布；每个正式 fact/craft card 至少有一个合法来源证据；越界 section 和提示注入测试 fail closed。

### Phase 7：正史快照、续写创建与双书架

- [x] 增加 `books.creation_mode` 和历史书籍幂等回填。
- [x] 实现分叉 section 选择、分叉范围过滤和正史预览。
- [x] 实现 cancellation-linearizable 原子创建续写。
- [x] 改造书架为原创/续写双入口，并增加向导和续写卡片摘要。
- [x] 更新书籍删除逻辑：删除续写要清理其绑定、快照和方法绑定；删除来源 origin book 不删除冻结 revision。

实施门禁：历史书在 continuation schema 初始化时幂等回填为 `original`；正史
预览只接受来源 revision 中完整 section 的章末，并过滤掉分叉点后的 fact、技法
类型和缺少分叉前证据的记录。创建命令在一个 cancellation-linearizable 事务内
重新计算 snapshot digest，再写不可变 snapshot/records、`creation_mode=continuation`
的独立书籍、写作目录、continuation binding 和可选准确方法版本绑定；方法校验失败
会连同 book、outline 和 snapshot 全部回滚。来源新增 revision 不会改变既有绑定。
书架已拆成“原创作品/续写作品”，向导串联来源版本、正式分析、章末分叉、正史预览
和原子创建，续写卡显示来源与分叉摘要。删除续写会清理 binding、snapshot records、
snapshot 和方法绑定，但保留来源 revision。Phase 7/来源/数据库/生命周期 focused
后端 37 个、`npm run test:unit` 399 个、边界 53 个通过；
`npm run typecheck` 与 `git diff --check` 通过。

门禁：只能章末分叉；任何分叉点后证据进入快照都必须失败；创建失败无孤立 `book`；历史原创书行为不变。

### Phase 8：续写 Writing 运行时与组合 Story Memory

- [x] Writing Profile 权威 hydrate continuation context，不新增续写 Profile。
- [x] 增加继承正史 provider、组合优先级、冲突诊断和证据回执。
- [x] 增加只在有效 continuation binding 下启用的受限来源读取工具。
- [x] 让章节 Story Memory 分析读取组合基线，但只写目标书 delta。
- [x] 在工作台显示继承正史只读视图和当前来源/分叉身份。

实施门禁：Writing Profile 先只读 `books.creation_mode`，原创路径经查询追踪证明不访问
continuation 表；续写路径再加载冻结 binding、canon records 和 digest，并把精确身份冻结到
Run binding。继承正史以独立宿主上下文块注入，带 Context Evidence Receipt、预算截断和
`inherited_canon_over_target_story_memory` 优先级/冲突诊断。只读来源工具只接收模型提供的
section id，book、准确 revision 和 fork ordinal 均由宿主绑定，分叉后章节 fail-closed。
Story Memory 分析提示组合继承正史与目标书当前状态，但 delta 仍沿既有 target book ledger
写入；所有 Writing 写工具继续由 host-bound bookId 限域。工作台仅对续写显示“继承正史”
入口、来源/分叉身份和只读事实。Phase 8 focused 后端 98 个、前端 unit 399 个、Agent/PurrA
边界 53 个与 `npm run typecheck` 通过；首次 focused 命令引用了不存在的旧测试文件名，
该命令未执行测试，已用实际 `test_story_memory_analysis.py` 重跑通过。

门禁：原创请求不查询 continuation 表；续写读不到分叉点后来源；所有写工具只能写目标书；正史快照永不被修改。

### Phase 9：来源技法到候选方法的桥接

- [x] 从已发布 craft cards 生成候选方法草稿和候选方案草稿。
- [x] 保留稳定 analysis/craft-card 引用，不复制原文证据到方法 Markdown。
- [x] 实现逐项编辑、删除和一次确认后的原子发布。
- [x] 发布完成后不自动绑定任何作品；续写创建或作品面板由用户明确绑定。

实施门禁：桥接服务只接受正式分析中的 `verified` craft cards，在一个事务中生成普通
可编辑方法草稿和候选方案草稿；method/scheme 的 source ref 同时冻结 analysis id/version/
digest、source revision 与 craft-card id/digest。技法卡的 evidence excerpt 不进入方法正文，
若正文误含同一逐字摘录则替换为档案引用提示；证据表仍是唯一原文证据档案。方法库支持
逐项编辑、删除、勾选保留项和一次确认，发布命令在 cancellation-linearizable 事务内先生成
全部方法 revision、再更新并发布完整方案；方案失败会回滚所有方法 revision。返回值明确
`bindingChanged=false`，不会自动绑定任何作品。分析后续更新不改写已发布 revision，删除候选
不删除分析证据。Phase 9 focused 后端 25 个、前端 unit 399 个、Agent/PurrA 边界 53 个、
`npm run typecheck` 和 `git diff --check` 通过。

门禁：方法删除不影响分析证据；分析更新不改写已发布方法；候选发布失败不产生部分方案。

### Phase 10：完整验收

- [x] 后端 focused tests。
- [x] 前端 unit tests 与 typecheck。
- [x] 后端完整 pytest。
- [x] Agent/PurrA 应用边界和持久化边界门禁。
- [ ] Web 实机：创建方法、发布方案、绑定、`/`、历史回放、来源导入、分析、快照、续写、重启恢复。（方法/方案/绑定/`/`/书架分区/重启已实机；外部模型分析后的链路因无配置模型与 Provider 凭据阻塞。）
- [x] Electron 实机：TXT/Markdown 选择与同一业务 API 闭环。
- [x] 导出/导入数据库后验证方法版本、来源 revision、快照和 Run binding 仍一致。
- [x] 停止所有测试启动的后端、Vite 和 Electron 进程，并确认相关端口无监听。

实施门禁：最终 focused 后端 219 个、前端 unit 399 个、Agent/PurrA 边界 53 个、
完整后端 1687 个、`npm run typecheck`、Purr Components 边界、`npm run check` 和
`git diff --check` 均通过。
Web 隔离实机完成方法草稿自动保存、方法 v1 发布、方案对准确方法 revision 的组合发布、
作品对准确方案 revision 的绑定，以及 `/` 仅展示本书已绑定方法；原创/续写书架分区也已验证。
Electron 原生选择 Markdown 后展示 256 字节、94 字符、3 节的导入确认页，要求权利与模型
发送边界双确认，最终通过同一 API 创建一个不可变来源 revision；TXT 与扩展名拒绝由 IPC
自动化测试覆盖。隔离数据库重启后，方法、方案、作品绑定和来源 revision 身份保持一致；
导出/导入自动化另验证方法 revision、来源 revision、canon snapshot 与 Run binding digest。
隔离环境没有配置分析模型和真实 Provider 凭据，因此 Web 的分析、快照、续写和历史运行
实机没有执行，未写成通过；对应持久化、暂停/恢复/取消/重复请求和分叉边界已有 focused
测试通过。测试启动的 18322、18323 与 IPv4 5174 均已关闭；用户原有 18321 和 IPv6 5174
监听保持未动。

## 13. 主要文件落点

计划中的新文件名允许按当前代码风格微调，但职责不能跨层漂移：

- Schema：
  - `backend/database/writing_method_schema.py`
  - `backend/database/novel_continuation_schema.py`
- Domain：
  - `backend/domains/writing/methods.py`
  - `backend/domains/writing/method_resolution.py`
  - `backend/domains/continuation/`
- Application：
  - `backend/application/writing_method_service.py`
  - `backend/application/novel_source_service.py`
  - `backend/application/novel_analysis_profile.py`
  - `backend/application/continuation_service.py`
  - 扩展现有 `writing_agent_profile.py`、`request_mapping.py`、`writing_memory_context` 组合链
- Persistence：
  - `backend/infrastructure/persistence/writing/sqlite_writing_method_repository.py`
  - `backend/infrastructure/persistence/continuation/`
- Routes/Schemas：
  - `backend/routers/writing_methods.py`
  - `backend/routers/novel_sources.py`
  - `backend/routers/continuations.py`
  - 对应 `backend/schemas/` wire contracts
- Frontend：
  - `src/WritingMethodsPage/`
  - `src/BookshelfPage/` 的双书架与续写向导子组件
  - `src/Workspace/` 的写作方法和继承正史面板
  - `src/components/AgentConversation/Composer/` 的通用命令菜单扩展点
  - `src/Workspace/AiPanel/` 的本轮方法选择和发送冻结

不要修改外部 PurrA 包来承载产品规则。只有当宿主缺少通用扩展钩子时，才增加最小的产品中立接口，并用 PurrA/应用边界测试证明没有业务反向依赖。

## 14. 每阶段验证命令

按风险选择 focused tests 后，至少运行：

```bash
npm run typecheck
npm run test:unit
npm run test:backend
npm run check:agent-refactor-boundaries
```

如阶段改动共享 Agent/PurrA 运行协议，再运行：

```bash
npm run check
```

不得把“命令执行过”当成通过；交付时分别报告 focused、前端、后端、边界、实机和进程清理结果。

## 15. 独立完成定义

### 15.1 写作方法完成

```text
创建/复制方法 → 编辑草稿 → 主动发布不可变版本
→ 组合并发布方案 → 作品绑定准确版本
→ Writing 宿主解析并注入 → / 控制本轮
→ Run binding 可还原允许版本栈，Context Evidence Receipt 可还原实际版本
→ 新版不静默改变旧作品或旧 Run
```

### 15.2 小说续写完成

```text
导入/冻结来源 → 发布证据化分析 → 选择章末分叉
→ 生成不可变正史快照 → 原子创建独立续写作品
→ Writing 按 TaskSpec 组合召回继承正史和续写 Story Memory
→ 受限读取原文 → 只修改目标书 → 重启后状态一致
```

### 15.3 集成完成

```text
已验证技法卡 → 候选方法/方案 → 用户审核发布
→ 用户明确绑定续写 → 准确版本进入 binding snapshot 和实际上下文回执
→ 原文证据仍只存在于来源分析档案
```

同时满足：旧 `book_style` 及 `readSourceStyle` 已完全退役；原创作品路径无行为回归；通用 PurrA 不包含续写或写作方法业务规则。

## 16. 新对话启动提示词

下面的提示词用于另开开发对话。执行者仍需以启动时的代码为准，不能把本文日期当成免审计依据。

```text
请在当前 PurrTypos 项目中实施“写作方法”和“小说续写”两个需求。工作目录是：
/Users/liuyubin/Lybrand_project/PurrTypos

这次是实施任务，不是重新泛化讨论。开始前请完整阅读：
1. docs/design/2026-08-27-writing-methods-and-novel-continuation-implementation-plan.md
2. docs/superpowers/specs/2026-08-12-writing-method-library-design.md（只作为已确认产品语义；实际拓扑以第 1 份当前计划为准）
3. docs/agent-architecture-final.md
4. docs/purra-baseline.md
5. 与当前 Writing Profile、Story Memory、Agent Run binding、共享 Composer 和数据库初始化直接相关的代码与测试

先执行 Phase 0：检查分支和 git status，记录本需求外的未提交改动并保持不动；核对计划中的文件接缝；运行相关基线测试。不要清理、覆盖或顺手重构用户现有改动。若当前代码已经再次漂移，先用证据更新计划中的具体接缝，再继续实现。

按当前计划 Phase 1 到 Phase 10 顺序推进。每阶段只做该阶段的最小纵向切片，完成 focused tests 和阶段门禁后再进入下一阶段，并同步更新计划 checkbox。遇到真实契约冲突时说明事实、影响和最小修订，不要静默发明兼容层。

必须坚持这些边界：
- 产品名称使用“写作方法库 / 写作方法 / 写作方案”，不把用户内容称为 Skill。
- 续写创作复用现有 WritingAgentProfile，以服务器校验的 creation_mode 和 continuation binding 增加宿主能力；首版不复制一个 Continuation Profile。
- 只有跨来源逐章蒸馏使用独立、可恢复的 NovelAnalysisAgentProfile。
- 通用 PurrA 不查询写作方法、来源或续写业务表；如果确实缺少扩展点，只增加最小、产品中立的宿主钩子。
- Run binding 冻结可用方法版本栈和本轮强制/排除；TaskSpec 后实际注入的方法用现有 Context Evidence Receipt 持久化。不要新增 writing_method_run_receipts 平行真相。
- 方案绑定保持原子；作品和历史 Run 都不自动跟随方法/方案新版。
- 来源 revision、分析 revision 和 canon snapshot 不可变；首版只允许章末分叉，分叉点后来源默认不可见。
- 来源事实不写入目标书普通 Story Memory；组合召回可以读取继承正史，但章节分析只向目标 book 写 delta。
- 来源正文、方法 Markdown 和分析文本均按不可信内容处理，不能获得工具权限或扩大作用域。
- 首版外部来源只实现 TXT/Markdown 和从现有作品冻结；不要加入 EPUB、Obsidian、在线市场、向量库或自动版本清理。
- 彻底删除旧 book_style、getBookStyle、saveBookStyle、readSourceStyle 及现有数据，不迁移；但不要误删 Memory Center 的通用 style 记忆类型。
- Screenplay 删除 readSourceStyle 后从实际来源文本分析风格，不自动读取作品绑定的写作方法。
- 用户明确导入/冻结时才创建来源 revision；显示存储体积和向外部模型发送所需片段的数据边界，未确认前不得发送原文。

验证时分别报告：focused tests、前端 typecheck/unit、后端完整 pytest、Agent/PurrA 边界、Web/Electron 实机、数据库重启/导入恢复，以及测试进程与端口清理。不要把缺失凭据的真实 Provider E2E 写成通过，也不要只通过增加超时来掩盖运行问题。

完成标准不是“建了表和页面”，而是两条独立闭环和集成闭环均真实可用：
1. 方法草稿→发布→方案→作品绑定→本轮 / 选择→实际版本可追溯且不自动升级；
2. 来源导入/冻结→证据化分析→章末正史快照→独立续写→受限召回→只更新目标书；
3. 已验证技法卡→候选方法/方案→用户审核发布→用户明确绑定，且原文证据不复制进方法正文。

现在从 Phase 0 开始，先给出当前代码证据和基线结果，然后继续实施；除非遇到需要用户决定的真实产品冲突或外部凭据阻塞，不要停在再次写计划。
```

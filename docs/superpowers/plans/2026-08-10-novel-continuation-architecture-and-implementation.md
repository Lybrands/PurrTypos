# 小说续写能力架构与实施计划

状态：已确认方向，进入实施  
日期：2026-08-10  
范围：原作导入、蒸馏分析、正史快照、续写创作、Continuation Agent 及 Story Memory 组合召回

## 目标

在不破坏现有原创写作体验的前提下，建立完整闭环：

1. 读取外部作品或冻结现有原创作品；
2. 将来源保存为不可变修订；
3. 按章节蒸馏人物、关系、世界事实、时间线、剧情线和写作技法；
4. 用户选择章末分叉点并确认截至该点的正史快照；
5. 创建独立续写作品；
6. Writing Agent 在受控来源范围内继承正史并继续创作；
7. 续写章节只更新续写项目，不反向修改原作。

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
- 原作硬事实与风格软约束分开管理。
- Obsidian 集成后置；当前仅保留稳定 ID、修订和投影能力。

## 架构边界

### Novel Source

拥有原作、来源修订、卷章结构、原文检索和只读访问。内部原创作品和外部文件都必须先冻结为相同的来源修订，续写不能实时依赖一个可变 `book`。

### Source Analysis

拥有蒸馏任务、事实、证据、技法卡、审核和纠错。模型输出先进入候选 Artifact，经过证据验证后才发布分析版本。

### Continuation

拥有目标作品与来源之间的绑定、分叉点、正史快照、来源升级比较和继承/续写合并规则。

### Writing

继续拥有章节、大纲、人物、世界设定、伏笔、长期记忆和续写阶段 Story Memory。Writing 不拥有原作来源和继承正史。

### Continuation Agent

使用独立 Agent profile。复用 Writing 基础操作，但增加宿主锁定的来源范围、只读来源工具、继承正史上下文和冲突校验。通用 PurrA 不查询续写业务表。

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

### 续写

- `continuation_bindings`
- `continuation_canon_snapshots`
- `continuation_canon_records`

## 原作分析管线

1. 确定性解析卷章并让用户预览；
2. 按章节提取事实和技法信号；
3. 跨章节归一人物与实体；
4. 聚合人物状态、关系、时间线、剧情线和伏笔；
5. 验证证据片段确实存在于绑定章节；
6. 生成待审核分析版本；
7. 用户整体确认并可纠正高风险事实；
8. 原子发布分析版本。

每个长任务单元绑定 `source_revision_id + section_id + analysis_schema_version`。重启中断返回 `paused/pending`，不消耗业务重试次数。

## 正史快照

正史快照仅从分叉点及以前的证据构建，包含：

- 人物身份和分叉点当前状态；
- 人物关系；
- 世界硬规则；
- 已发生事件；
- 未解决剧情线与伏笔；
- 人物截至分叉点的认知范围；
- 独立的风格/技法配方。

依赖分叉点以后证据的总结不得进入快照。

## Agent 上下文顺序

1. 宿主绑定：目标书、来源修订、分叉点、正史快照；
2. 继承正史硬约束；
3. 已接受的续写 Story Memory；
4. 当前章节、大纲和用户任务；
5. 创作配方；
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

### 续写

- `POST /api/continuations/preview-snapshot`
- `POST /api/continuations`
- `GET /api/continuations/by-book/{bookId}`
- `GET /api/continuations/{bookId}/canon`
- `POST /api/continuations/{bookId}/source-upgrade-preview`

## 实施阶段

### 阶段 1：设计契约和数据库骨架

- [x] 固化架构、边界和不变量。
- [ ] 增加领域契约和 Repository ports。
- [ ] 增加 SQLite 表和非破坏迁移。
- [ ] 历史书籍迁移为 `original`。
- [ ] 增加数据完整性测试。

### 阶段 2：原作导入与只读查询

- [ ] TXT/Markdown 导入。
- [ ] EPUB 导入。
- [ ] 章节解析预览与确认。
- [ ] 从现有原创作品冻结来源修订。
- [ ] 来源全文检索和范围校验。
- [ ] Electron/Web 文件入口。

### 阶段 3：蒸馏分析

- [ ] 定义分析 Artifact schema。
- [ ] 建立可恢复长任务 recipe。
- [ ] 逐章事实与技法提取。
- [ ] 人物/实体归一和聚合。
- [ ] 证据、覆盖范围和分叉泄漏验证。
- [ ] 审核、纠错和原子发布。

### 阶段 4：续写项目

- [ ] 原创/续写双书架。
- [ ] 原作库和新建续写向导。
- [ ] 分叉点选择。
- [ ] 正史快照预览。
- [ ] 原子创建目标 `book + binding + snapshot`。
- [ ] 工作台显示来源与分叉点。

### 阶段 5：Continuation Agent

- [ ] 独立 profile 和 DomainContext。
- [ ] 受控来源/正史只读工具。
- [ ] 复用 Writing 操作但只写目标书。
- [ ] 确定性继承正史与创作配方上下文。
- [ ] 来源读取收据。
- [ ] 防 Prompt Injection 和来源越界测试。

### 阶段 6：Story Memory 组合层

- [ ] 继承快照与目标 Story Memory 组合召回。
- [ ] 续写章节分析使用组合基线。
- [ ] 硬事实冲突提示。
- [ ] 显式改写正史流程。
- [ ] 来源更新比较和手动升级。

### 阶段 7：验证与收尾

- [ ] 数据迁移与删除生命周期。
- [ ] 中断恢复和幂等测试。
- [ ] 多续写分支隔离测试。
- [ ] Agent 架构门禁。
- [ ] 前端类型检查和单元测试。
- [ ] 后端完整测试。
- [ ] Web 与 Electron 实际流程验证。

## 完成定义

只有以下闭环真实可用，才视为完成：

```text
导入/冻结原作
→ 发布证据化分析
→ 选择章末分叉点
→ 创建不可变正史快照
→ 创建续写作品
→ Agent 按需读取继承正史并提出章节修改
→ 用户接受后只更新续写 Story Memory
→ 应用重启后任务、快照和创作状态仍然一致
```

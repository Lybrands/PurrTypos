# Long-Term Memory Redesign

> 状态：设计已确认，待实施计划  
> 日期：2026-06-20  
> 范围：PurrTypos 的长期记忆、自动沉淀、召回注入、记忆管理 UI 与 Agent 工具

## 背景

当前项目里的“记忆系统”主要由 `ai_memories` 与 `ai_foreshadowing` 构成，实际语义更接近“本书设定”和“伏笔”。它能被用户手动勾选并注入 AI，但不是完整的长期记忆系统。

现状的主要问题：

- AiPanel 和 Inline 改写走两套注入逻辑，注入位置、文案强度和 token 控制不一致。
- `ai_memories` 只覆盖本书设定，人物、世界设定、大纲、章节正文、对话结论没有统一沉淀入口。
- 伏笔没有完整的 query 检索与 Agent 更新/回收工具。
- 勾选记忆缺少统一 token 预算，条目多时容易挤占上下文。
- README 和部分注释仍残留 mem0 / embedding 时代描述，容易误导维护者。

本设计目标是做一个本地优先、可逐步增强的长期记忆系统：第一版不强制依赖 Ollama、云端 embedding 或外部服务，先用 SQLite FTS5 和规则打分把自动沉淀、召回、更新、去重、遗忘流程打通，并为未来接入 embedding 留出边界。

## 目标

1. 建立统一的长期记忆池，承载设定、剧情事实、人物状态、世界观、伏笔、风格约束、对话/章节总结。
2. 统一所有 AI 入口的记忆召回和注入，消除前端各自拼 prompt 的分叉。
3. 支持自动沉淀候选，但避免错误记忆直接污染长期上下文。
4. 支持状态管理：启用、待确认、归档、被覆盖。
5. 支持来源追踪、冲突/覆盖关系、固定优先级和 token 预算。
6. 兼容现有 `ai_memories` / `ai_foreshadowing` 与旧工具，避免一次性破坏已有功能。

## 非目标

- 第一版不实现强依赖 embedding 的向量检索。
- 第一版不做后台静默大规模总结和自动覆盖。
- 第一版不删除旧表和旧接口。
- 第一版不把业务原始数据全文复制进记忆池；记忆池只保存 AI 可用的事实、摘要和约束。

## 核心架构

新增后端统一中枢 `Memory Orchestrator`。它负责三类工作：

1. **沉淀**：从章节、人物、世界设定、大纲、伏笔、AI 对话中提炼长期记忆或候选记忆。
2. **召回**：根据用户当前 prompt、章节、人物、勾选项、模式和工具上下文检索相关记忆。
3. **注入**：按类型和优先级把记忆格式化为 system context，并在预算内裁剪。

前端只传结构化信息：

- 当前书籍、章节、会话、模式。
- 当前用户 prompt。
- 用户手动勾选的强制记忆 ID。
- 关联章节/大纲/人物等上下文 ID。

后端统一决定：

- 哪些记忆强制注入。
- 哪些记忆自动召回。
- 哪些条目只列出索引并交给工具按需读取。
- 不同类型记忆如何标注为“既成事实”“未来计划”“伏笔”“风格约束”等。

## 数据模型

### `memory_items`

通用长期记忆主表。

建议字段：

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `book_id TEXT NOT NULL`
- `kind TEXT NOT NULL`
- `scope_type TEXT NOT NULL DEFAULT 'book'`
- `scope_id TEXT DEFAULT NULL`
- `content TEXT NOT NULL DEFAULT ''`
- `summary TEXT NOT NULL DEFAULT ''`
- `keywords TEXT NOT NULL DEFAULT ''`
- `importance INTEGER NOT NULL DEFAULT 3`
- `confidence REAL NOT NULL DEFAULT 1.0`
- `status TEXT NOT NULL DEFAULT 'active'`
- `pinned INTEGER NOT NULL DEFAULT 0`
- `fingerprint TEXT NOT NULL DEFAULT ''`
- `source_type TEXT NOT NULL DEFAULT 'manual'`
- `source_id TEXT DEFAULT NULL`
- `create_time DATETIME DEFAULT CURRENT_TIMESTAMP`
- `update_time DATETIME DEFAULT CURRENT_TIMESTAMP`
- `last_used_at DATETIME DEFAULT NULL`

`kind` 第一版取值：

- `canon`：强设定、本书设定。
- `plot`：已发生剧情事实。
- `character`：人物状态、关系、动机、伤势、秘密等。
- `world`：世界观、地点、势力、规则、物品。
- `foreshadowing`：伏笔。
- `style`：风格与写作规则。
- `summary`：对话、章节或阶段性总结。

`scope_type` 第一版取值：

- `book`
- `chapter`
- `character`
- `outline`
- `session`

`status` 第一版取值：

- `pending`：AI 提炼的候选，未被用户确认。
- `active`：已启用，可被召回注入。
- `archived`：已归档，默认不召回。
- `superseded`：已被新记忆覆盖，默认不召回。

### `memory_links`

记录记忆之间的关系。

建议字段：

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `book_id TEXT NOT NULL`
- `from_memory_id INTEGER NOT NULL`
- `to_memory_id INTEGER NOT NULL`
- `relation TEXT NOT NULL`
- `note TEXT NOT NULL DEFAULT ''`
- `create_time DATETIME DEFAULT CURRENT_TIMESTAMP`

`relation` 第一版取值：

- `supersedes`：前者覆盖后者。
- `contradicts`：潜在冲突。
- `supports`：互相佐证。
- `relates_to`：弱关联。

### `memory_items_fts`

使用 SQLite FTS5 trigram 建全文索引：

- `content`
- `summary`
- `keywords`

第一版不建向量表。未来接 embedding 时通过 `MemoryRetriever` 增加可选 provider，不要求修改前端和调用方。

### 旧表兼容

`ai_memories` 与 `ai_foreshadowing` 第一版保留。

- 旧 `ai_memories` 镜像为 `memory_items(kind='canon')`。
- 旧 `ai_foreshadowing` 镜像为 `memory_items(kind='foreshadowing')`。
- 伏笔原表继续保存专有字段，如埋入章节、预计回收章节、状态、已回收章节。
- 删除书籍时必须继续清理新旧记忆表。

## 自动沉淀

第一版采用“显式触发 + 轻量自动”，避免后台静默改写长期记忆。

### 章节正文

保存章节后不立即全量总结。以下场景生成候选：

- 用户手动点击“提炼记忆”。
- 章节纯文本超过 3000 字，且距离上次章节记忆提炼新增超过 1500 字时，提示用户确认是否提炼。
- AI 生成内容被实际写入正文。

候选类型包括：

- 已发生剧情事实。
- 人物状态变化。
- 重要物品、地点、势力变化。
- 伏笔候选。
- 已回收伏笔候选。

结果默认进入 `pending`，由用户确认后变为 `active`。

### 去重与覆盖

创建记忆前先生成 `fingerprint`，第一版可用规则化文本实现：去空白、统一标点、截取核心内容并结合 `book_id + kind + scope_type + scope_id`。写入前按 `fingerprint` 查重。

查重结果处理：

- 完全重复：不新增，只更新 `update_time` 与来源引用。
- 内容高度相似但字段不同：生成 `pending` 合并候选。
- 新内容明确覆盖旧内容：创建 `memory_links(relation='supersedes')` 候选，用户确认后把旧记忆标为 `superseded`。
- 内容冲突但无法判断新旧：创建 `memory_links(relation='contradicts')` 候选，保留两条，等待用户处理。

### 人物 / 世界设定 / 故事背景

保存后生成结构化摘要，进入高可信记忆。

- 用户直接编辑保存的内容可进入 `active`。
- AI 推断出的补充进入 `pending`。
- 与旧记忆冲突时，不直接覆盖；先创建 `memory_links(contradicts)` 或覆盖候选。

### 大纲

大纲沉淀为计划类记忆。注入时必须明确标记为“计划/大纲，非既成事实”，避免 AI 把未来剧情误认为已经发生。

### AI 对话

每轮对话结束后只沉淀以下内容：

- 用户明确要求“记住”的内容。
- AI 结果被写入正文或设定后的内容。
- 用户确认的阶段性总结。

普通闲聊、未确认脑暴和被拒绝的建议不自动进入长期记忆。

### 伏笔

新增伏笔时同步生成长期记忆。章节内容疑似回收伏笔时，只生成“疑似已回收”候选，不自动改状态。

## 召回与注入

新增统一入口，例如：

```python
async def build_memory_context(tool_ctx: dict, user_prompt: str, mode: str) -> MemoryContextBlock:
    """Return formatted memory context plus recall diagnostics for the current AI request."""
```

`MemoryContextBlock` 至少包含：

- `text`：最终注入的上下文文本。
- `included_ids`：实际注入的记忆 ID。
- `deferred_ids`：相关但因预算未注入的记忆 ID。
- `token_estimate`：估算后的注入规模。
- `diagnostics`：按类型统计的召回、裁剪和强制注入数量。

AiPanel、Inline 改写、Agent 模式和未来导演笔记本都调用同一套后端逻辑。

### 召回层级

第一层：强制注入。

- 用户手动勾选的记忆。
- `pinned=true` 且 scope 命中当前上下文的记忆。
- 当前章节、当前人物直接相关的高优先设定。

第二层：相关召回。

组合 query：

- 用户 prompt。
- 当前章节标题。
- 当前大纲标题。
- 当前人物名。
- 最近对话摘要。

检索用 FTS5，之后规则重排。

排序特征：

- 当前章节相关优先。
- 当前人物相关优先。
- `importance` 高优先。
- `pinned` 高优先。
- 近期 `last_used_at` 适度加分。
- 未回收伏笔优先。
- `archived` / `superseded` 默认排除。
- `pending` 只在记忆中心候选队列展示，不进入自动召回。

第三层：按需工具。

超出预算或相关性较弱的条目不注入正文，只列出可检索提示，例如“另有 8 条相关伏笔可用 `searchMemories` 查询”。

### 注入格式

按类型分块：

- `必须遵循的设定`
- `已发生的剧情事实`
- `人物当前状态`
- `待铺垫/待回收伏笔`
- `大纲计划，非既成事实`
- `风格约束`

每个分块都应避免混淆事实与计划。

### Token 预算

记忆预算纳入现有 `chat_preflight` 总预算管理。建议第一版建立统一预算分配：

- 关联章节/大纲：40%
- 长期记忆：30%
- 伏笔：15%
- 风格/强约束：15%

强制注入也必须有硬上限。超限时优先保留：

1. 用户当前指令。
2. 基础 system prompt。
3. 当前章节和当前人物直接相关的 pinned/高重要性记忆。
4. 已发生剧情事实。
5. 未回收伏笔。
6. 大纲计划和低优先摘要。

## UI 设计

### 记忆中心

替换 `DirectorNotebook/NotebookToolbar.tsx` 里“记忆 / 伏笔”的占位弹窗。

功能：

- 按类型筛选：设定、剧情事实、人物、世界观、伏笔、风格、总结。
- 按状态筛选：待确认、已启用、已归档、被覆盖。
- 搜索：关键词、来源、章节、人物。
- 排序：重要性、更新时间、最近使用、来源。
- 操作：固定、取消固定、确认候选、编辑、归档、标记覆盖、标记冲突、删除。
- 候选确认队列：AI 自动提炼的 `pending` 记忆集中处理。

### AI 上下文选择器

保留 AiPanel 灯泡入口，但职责收窄为“本轮强制注入”。

UI 应展示：

- 手动强制注入的条目数量。
- 后端自动召回的条目数量。
- 被预算裁剪的条目数量。

用户不需要手动勾选所有记忆。默认由后端自动召回相关记忆，手动勾选只表示“这轮一定带上”。

### Inline 改写

Inline 改写不再在前端调用 `getSparkIdeasByIds` / `getForeshadowingByIds` 并拼接 `[参考资料]`。它应改为把结构化上下文交给后端，由统一的 `build_memory_context` 生成注入块。

## Agent 工具

新增统一记忆工具：

- `searchMemories`：按 query、kind、scope、status 搜索。
- `getMemoryDetail`：查看完整内容、来源和关系。
- `createMemory`：新增记忆。
- `updateMemory`：更新内容、重要性、状态、scope。
- `archiveMemory`：归档过时记忆。
- `linkMemories`：标记覆盖、冲突、支持、关联。

伏笔保留专用工具：

- `createForeshadowing`
- `updateForeshadowing`
- `resolveForeshadowing`

旧工具兼容：

- `searchSparkIdeas` 内部转调 `searchMemories(kind='canon')`。
- `addSparkIdea` 保留兼容层，同时写入新记忆表。
- `updateSparkIdea` / `deleteSparkIdea` 保留兼容层，避免现有 skills 立即失效。

## 迁移策略

采用非破坏式迁移。

第一阶段：

- 新增 `memory_items`、`memory_links`、`memory_items_fts`。
- 启动迁移时把旧 `ai_memories` 与 `ai_foreshadowing` 镜像到新表。
- 旧 API 继续可用。

第二阶段：

- 旧 API 双写新表。
- 新增统一 memory API。
- 后端注入逐步改为读新表。

第三阶段：

- 前端记忆中心和上下文选择器改用新 API。
- Inline 改写移除前端拼 prompt。

第四阶段：

- 旧工具转兼容层。
- 文档、README、工具描述统一命名。

第五阶段：

- 评估是否废弃旧表或只保留同步兼容。

## 测试计划

后端测试：

- `memory_service`：CRUD、FTS、scope 过滤、status 过滤、importance 排序、pinned 排序。
- `memory_orchestrator`：强制注入、自动召回、预算裁剪、类型分块、计划/事实区分。
- `chat_preflight`：AiPanel 和 Inline 使用同一后端注入结果。
- `memory_tools`：搜索、创建、更新、归档、链接、伏笔回收。
- 迁移测试：旧设定和伏笔能镜像为新记忆，删书时新旧表都被清理。

前端验证：

- 记忆中心能展示、搜索、筛选、确认候选。
- 灯泡入口能选择强制注入记忆。
- 自动召回数量和预算裁剪提示正确展示。
- Inline 改写不再出现与 AiPanel 不一致的记忆文案。

## 分阶段实施

### 阶段一：记忆底座

建表、迁移、统一 service、FTS 检索、基础 API。

### 阶段二：统一召回注入

新增 `Memory Orchestrator`，让 AiPanel 和 Inline 都走后端统一上下文。

### 阶段三：工具升级

新增统一记忆工具，补齐伏笔 update/resolve，旧工具转兼容。

### 阶段四：记忆中心 UI

替换导演笔记本占位，加入搜索、筛选、固定、归档、候选确认。

### 阶段五：自动沉淀

先做手动触发和对话明确指令，再扩展到章节、人物、大纲保存后的候选生成。

## 风险与约束

- 错误记忆比漏记更危险，因此自动沉淀默认进入 `pending`。
- 未来大纲不能与已发生事实混合注入。
- 强制注入也必须受预算约束，避免撑爆 context。
- 旧 API 与旧工具需要兼容过渡，不能一次性断开现有前端和 skills。
- 不要重新引入强制 Ollama / mem0 依赖；embedding 只能作为可选增强。

## 待实施前确认

实施计划应按阶段拆分，并优先完成阶段一和阶段二。阶段五的自动沉淀涉及模型调用和用户确认体验，适合在统一召回稳定后再做。

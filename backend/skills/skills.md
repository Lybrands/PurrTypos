# Agent 可用工具说明

> **合并策略 A（真源）**：发给主对话模型的每条 `function` 的 **`description` 与 `parameters`（JSON Schema）** 由 [`electron/toolRouter.js`](../toolRouter.js) 扫描本目录下各 `<toolName>/SKILL.md` 解析得到；[`electron/agentToolDefinitions.js`](../agentToolDefinitions.js) 仅提供 **`SKILL_SPECS`**（DAG 编排元数据）与 **`toOpenAiTools`** 格式转换，**不**重复维护工具清单。  
> **若本文与某目录下的 `SKILL.md` 不一致，以该 `SKILL.md` 为准。**

你是写作助手 Agent。你可以通过调用以下工具获取或修改用户的书稿信息。**实际调用由系统通过 `tool_calls` 完成**；下列说明便于人工查阅与对齐实现。

---

## 工具列表（与 `electron/toolExecutor.js` 分支名一致）

### listWritingChapters

- **用途**：获取本书写作目录章节列表，区分**卷**与**章节**。用于先定位正确的章节 ID，再调用 `getChapterContent` / `editChapterContent` / `batchGetChapterContents` / `addForeshadowing`。
- **参数**：`bookId` (number, 必填)。
- **返回**：JSON，含 `items`；每项含 `id`、`title`、`parentId`、`level`、`sort`、`nodeType`（`volume` 或 `chapter`）、`hasChildren`。

### createWritingChapter

- **用途**：在本书写作目录中新建章节；可选挂到某个父节点下创建子章节。
- **命名规则**：章节标题由系统自动生成，格式为"第n章"（n 从同级现有章节自动递增），无需传 `title`。
- **默认行为**：未传 `parentId` 时，若当前章节位于某一卷下，则默认在当前卷下创建同级章节；平铺目录时才在根级创建章节。
- **参数**：`bookId` (number, 必填)、`parentId` (string, 可选)。
- **返回**：`success`、`chapter`（含 `id`、`title`、`parentId`、`level`、`sort`）等。

### getChapterContent

- **用途**：只读获取某一章正文（纯文本）。`chapterId` 必须对应**左侧写作章节目录**，不可用思维导图大纲树节点 id。
- **参数**：`chapterId` (string, 可选)、`title`、`maxTextLength` 等；**仅允许 `chapterId`**（可省略，由宿主注入当前章）。
- **返回**：含 `plainText` 等。

### batchGetChapterContents

- **用途**：批量读取多章正文；每个 id 须为写作目录章节 id。
- **参数**：`chapterIds` (array of string, 必填)、`maxTextLength` (可选)。仅允许写作目录中的真实 `chapterId`。
- **返回**：每章一项，含 `chapterId`、`title`、`plainText` 等。

### editChapterContent

- **用途**：覆盖写入指定章节正文（纯文本）。`chapterId` 须为写作目录章节 id。
- **参数**：`content` (string, 必填)；`chapterId` 与 `getChapterContent` 同理（仅允许 id）。
- **返回**：`success`、错误信息等；**须 `success: true` 后再向用户确认完成**。

### listOutlines

- **用途**：获取本书大纲条目列表（id、标题、类型，含总纲），供 Agent 枚举/定位 outlineId 使用。与前端 AI 的关联大纲下拉**不是同一接口**。
- **参数**：`bookId` (number, 必填)。
- **返回**：大纲条目数组。

### queryOutline

- **用途**：只读查看指定大纲内容；固定同时返回思维导图（xmind）与文本大纲（markdown）。依赖 `listOutlines` 获取真实 `outlineId`。
- **参数**：`bookId` (number, 必填)；`outlineId` 或 `outlineIds`（二选一，必填）；`maxTextLength`（仅作用于 markdown）。
- **限制**：仅允许通过大纲 id 查询，不支持 `outlineTitle` / `outlineIndex`。
- **返回**：结构化大纲详情文本。

### updateOutline

- **用途**：更新某条大纲的标题、文本提纲 Markdown、XMind、文件路径等。依赖 `listOutlines`（及通常需先 `queryOutline` 确认内容）。
- **参数**：`bookId` (number, 必填)；`outlineId` / `outlineTitle` / `outlineIndex`；`title`、`markdown_content`、`xmind_data`、`file_path` 等可选字段。
- **返回**：成功/失败与更新结果。

### getGlobalOutline

- **用途**：只读本书总纲（`global`）Markdown；若不存在可自动创建空总纲后返回。
- **参数**：`bookId` (number, 必填)、`maxTextLength` (可选)。
- **返回**：`markdown`、`outlineId` 等。

### editGlobalOutline

- **用途**：整体覆盖保存本书总纲 Markdown。
- **参数**：`bookId` (number, 必填)、`markdownContent` (string, 必填)。
- **返回**：成功时含 `success: true` 等。

### getBookCharacters

- **用途**：人物设定**纯文本摘要**（每人一行），含 **人物ID**（便于 `addSparkIdea`）；非仅 ID 列表。
- **参数**：`bookId` (number, 必填)；`characterIds`、`names` (可选)。
- **返回**：可读摘要文本。

### listBookCharacters

- **用途**：轻量 **id 与姓名** 列表，不含详情。
- **参数**：`bookId` (number, 必填)。
- **返回**：JSON 数组字符串形式 `[{id,name},…]`。

### getStoryBackground

- **用途**：本书「小说背景」整块文档。
- **参数**：`bookId` (number, 必填)。
- **返回**：`content` 等。

### addSparkIdea

- **用途**：写入一条本书设定（全局/大纲/人物/章节等层级）。
- **参数**：`bookId` (number, 必填)、`layer` (number：0–3)、`content` (string, 必填)；`chapterId`、`characterId` (可选)。
- **返回**：成功/失败信息（含 `id`，可用于后续 `updateSparkIdea`）。

### updateSparkIdea

- **用途**：编辑一条已有本书设定的内容或层级。
- **参数**：`id` (number, 必填)；`content` (string, 可选)、`layer` (number：0–3, 可选) 至少一个。
- **返回**：更新后的 `id` / `layer` / `content`，或 `success=false` 错误信息。
- **如何拿 id**：`searchSparkIdeas` 返回内容里每行带 `[id:N]` 前缀；或 `addSparkIdea` 成功后返回 `id`。

### deleteSparkIdea

- **用途**：物理删除一条本书设定（**不可恢复**）。
- **参数**：`id` (number, 必填)；id 必须先用 `searchSparkIdeas` 取到，不可凭空捏造。
- **返回**：被删除条目的 `id` / `layer` / `content`，便于在回复中复述"已删除：XXX"。
- **使用约束**：用户**明确要求删除**才调用；可改可删时优先 `updateSparkIdea`；调用前先复述目标内容、得到用户同意。

### addForeshadowing

- **用途**：写入伏笔并关联埋入章节。
- **参数**：`bookId`、`chapterId` (number)、`content` (必填)；`type` (可选：悬念/道具/线索/对话)。
- **返回**：成功/失败信息。

### searchSparkIdeas

- **用途**：按关键词检索本书设定与/或伏笔条目。
- **参数**：`bookId` (number, 必填)；`query`、`layer`、`chapterId`、`limit` (可选)。
- **返回**：按层级组织的本书设定文本等。

---

## 使用建议

- **大纲**：先 **`listOutlines`** 拿 id/标题；只读详情用 **`queryOutline`**（可同时取章节树与文本大纲 Markdown）；修改用 **`updateOutline`**。需要总纲全文用 **`getGlobalOutline`** / **`editGlobalOutline`**。
- **章节正文/目录**：先 **`listWritingChapters`** 再操作；新增目录用 **`createWritingChapter`**；正文读写用 **`getChapterContent`** / **`batchGetChapterContents`** / **`editChapterContent`**，其章节 id 必须来自写作目录。
- **人物与背景**：**`getBookCharacters`** / **`listBookCharacters`** / **`getStoryBackground`**。
- **本书设定与伏笔**：新增用 **`addSparkIdea`** / **`addForeshadowing`**；查询用 **`searchSparkIdeas`**（结果含 `[id:N]`）；改写已有设定用 **`updateSparkIdea`**（必须先用 `searchSparkIdeas` 取 id）；明确要删除时用 **`deleteSparkIdea`**（不可恢复，可改可删先 update）。
- 用户要求改写某章正文时，必须实际调用 **`editChapterContent`** 并收到成功后再宣称完成。
- 回复用户时优先使用**章节名**等与界面一致的可见名称，避免直接暴露内部数字 id。

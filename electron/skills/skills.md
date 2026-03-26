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

### getChapterContent

- **用途**：只读获取某一章正文（纯文本）。`chapterId` 必须对应**左侧写作章节目录**，不可用总纲/其他大纲树节点 id。
- **参数**：`chapterId` (string, 可选)、`chapterTitle`、`chapterIndex` (number)、`title`、`maxTextLength` 等；**优先 `chapterTitle` 或 `chapterIndex`（附录 [序号]），勿手写 id**；可省略章节参数由宿主注入当前章。
- **返回**：含 `plainText` 等。

### batchGetChapterContents

- **用途**：批量读取多章正文；每个 id 须为写作目录章节 id。
- **参数**：`chapterIds` (array of number, 必填)、`maxTextLength` (可选)。
- **返回**：每章一项，含 `chapterId`、`title`、`plainText` 等。

### editChapterContent

- **用途**：覆盖写入指定章节正文（纯文本）。`chapterId` 须为写作目录章节 id。
- **参数**：`content` (string, 必填)；`chapterId` / `chapterTitle` / `chapterIndex` 与 `getChapterContent` 同理。
- **返回**：`success`、错误信息等；**须 `success: true` 后再向用户确认完成**。

### listOutlines

- **用途**：获取本书大纲条目列表（id、标题、类型，含总纲），扁平列表。Agent 侧「枚举可关联大纲」对应该工具（前端 UI 另有 `getAvailableOutlines` 等封装，**非**独立 IPC 工具名）。
- **参数**：`bookId` (number, 必填)。
- **返回**：大纲条目数组。

### queryOutline

- **用途**：只读查看指定大纲的**章节树文本**与/或 **「文本大纲」标签页 Markdown**（`includeChapters` / `includeText`）。依赖 `listOutlines` 在需要 `outlineId` 时。
- **参数**：`bookId` (number, 必填)；`outlineIds` / `outlineId` / `outlineTitle` / `outlineIndex`；`includeChapters`、`includeText`、`maxTextLength`。
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

- **用途**：人物设定**纯文本摘要**（每人一行），含 **人物ID**（便于 `addMemory`）；非仅 ID 列表。
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

### addMemory

- **用途**：写入一条长期记忆（全局/大纲/人物/章节等层级）。
- **参数**：`bookId` (number, 必填)、`layer` (number：0–3)、`content` (string, 必填)；`chapterId`、`characterId` (可选)。
- **返回**：成功/失败信息。

### addForeshadowing

- **用途**：写入伏笔并关联埋入章节。
- **参数**：`bookId`、`chapterId` (number)、`content` (必填)；`type` (可选：悬念/道具/线索/对话)。
- **返回**：成功/失败信息。

### searchMemories

- **用途**：按关键词检索本书长期记忆与/或伏笔条目。
- **参数**：`bookId` (number, 必填)；`query`、`layer`、`chapterId`、`limit` (可选)。
- **返回**：按层级组织的记忆文本等。

---

## 使用建议

- **大纲**：先 **`listOutlines`** 拿 id/标题；只读详情用 **`queryOutline`**（可同时取章节树与文本大纲 Markdown）；修改用 **`updateOutline`**。需要总纲全文用 **`getGlobalOutline`** / **`editGlobalOutline`**。
- **章节正文**：先 **`listWritingChapters`** 再读/写；**`getChapterContent`** / **`batchGetChapterContents`** / **`editChapterContent`** 的章节 id 必须来自写作目录。
- **人物与背景**：**`getBookCharacters`** / **`listBookCharacters`** / **`getStoryBackground`**。
- **记忆与伏笔**：**`addMemory`**、**`addForeshadowing`**、**`searchMemories`**。
- 用户要求改写某章正文时，必须实际调用 **`editChapterContent`** 并收到成功后再宣称完成。
- 回复用户时优先使用**书名、章节名**，避免直接暴露内部数字 id。

# Agent 可用工具说明

> **合并策略 A**：主对话模型收到的 `tools` **仅来自** `electron/agentToolDefinitions.js`。本目录各 `SKILL.md` 仅用于 **embed / Ollama 意图** 等路由层，见 `README.md`。

你是写作助手 Agent。你可以通过调用以下工具获取或修改用户的书稿信息。**实际调用由系统通过 tool_calls 完成**，以下说明描述各工具的用途与参数，便于你在适当时机选择调用。

---

## 工具列表

### listWritingChapters

- **用途**：获取本书写作目录章节列表，并显式区分**卷**与**章节**。用于先定位正确的章节 ID，再调用 `getChapterContent` / `editChapterContent` / `batchGetChapterContents`。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
- **返回**：JSON，含 `items` 数组；每项含 `id`、`title`、`parentId`、`level`、`sort`、`nodeType`（`volume` 或 `chapter`）、`hasChildren`。

---

### getChapterContent

- **用途**：获取某一章的正文内容（纯文本）。重要：`chapterId` 必须是**左侧写作章节目录**对应的章节 id，不得使用总纲/章节大纲/其他大纲树中的节点 id，否则会取不到正文。
- **参数**：
  - `chapterId` (number, 必填)：写作目录章节 ID。
  - `title` (string, 可选)：章节标题，用于展示。
  - `maxTextLength` (number, 可选)：纯文本最大长度，默认 12000。
- **返回**：含 chapterId、title、plainText 等；回答时用 plainText。

---

### batchGetChapterContents

- **用途**：批量获取多章正文（纯文本）。每个 id 均须为写作目录章节 id（与左侧写作章节目录一致）。
- **参数**：
  - `chapterIds` (array, 必填)：章节 ID 列表，如 [1,2,3]。
  - `maxTextLength` (number, 可选)：每章纯文本最大长度，默认 12000。
- **返回**：每章一项，含 chapterId、title、plainText 等。

---

### getBookCharacters

- **用途**：以**纯文本摘要**返回人物信息（每人一行）：含姓名、性别、年龄、职业、性格、外貌、背景等已有字段，并含 **人物ID**（便于 `addMemory(characterId)`）；**不是仅返回 ID 列表**。默认全部。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
  - `characterIds` (array, 可选)：只返回这些人物 ID（来自上次本工具返回中的「人物ID」）。
  - `names` (array, 可选)：按角色名模糊匹配（子串即可），如 `["林月"]`；与 `characterIds` 同时存在时优先按 ID 筛选。
- **返回**：可读文本摘要；每人一行，逗号连接多字段，含 `人物ID:` 及库中已填的人设项。

---

### listBookCharacters

- **用途**：轻量获取本书人物的 **id 与姓名**，**不含**性别、背景等详情。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
- **返回**：**JSON 数组**的字符串形式，每项为 `{"id": number, "name": string}`，顺序同库中创建顺序；无人物时为 `[]`。用于先拿到 ID，再调用 `getBookCharacters`（`characterIds`）或 `addMemory`（`characterId`）。

---

### getStoryBackground

- **用途**：获取本书小说背景的文本内容。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
- **返回**：含 content、update_time 等；回答时用 content。

---

### getGlobalOutline

- **用途**：只读获取本书总纲（`global`）Markdown 文本。若总纲不存在，会自动创建空总纲后返回，便于后续补写。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
  - `maxTextLength` (number, 可选)：总纲 Markdown 最大长度，默认 32000。
- **返回**：含 success、outlineId、title、type、markdown、hasMarkdown。

---

### editGlobalOutline

- **用途**：写入编辑本书总纲（`global`）Markdown，整体覆盖保存。若总纲不存在，会自动创建后写入。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
  - `markdownContent` (string, 必填)：新的总纲 Markdown 全文（覆盖写入）。
- **返回**：成功时含 success: true、outlineId、title、type、markdownLength；失败时含 success: false、error。

---

### getAvailableOutlines

- **用途**：获取本书「可关联」的大纲列表（总纲 + 章节大纲 + 其他大纲，扁平）。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
- **返回**：大纲数组，每项含 id、title、type 等。

---

### batchGetOutlineDetails

- **用途**：根据大纲 ID 列表获取每个大纲的详情（含子章节与层级文本）。需先有 availableOutlines（来自 getAvailableOutlines）。
- **参数**：
  - `outlineIds` (array, 必填)：大纲 ID 列表。
  - `bookId` (number, 必填)：当前书籍 ID，用于解析 allOutlines。
- **返回**：每项含 title、chaptersText 等。

---

### getTextOutline

- **用途**：获取左侧大纲各条目中 **「文本大纲」标签页**保存的 **Markdown 正文**（剧情提纲、结构说明等）。与 **batchGetOutlineDetails**（子章节树 `chaptersText`）互补，后者来自 XMind/目录树而非文本页。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
  - `outlineIds` (array, 可选)：只拉取这些大纲 ID；不传则返回本书所有已填写文本大纲。
  - `maxTextLength` (number, 可选)：合并后总长度上限，默认 32000。
- **返回**：纯文本，多块之间用分隔线拼接；每项含类型标签、标题与「大纲ID:」便于对照 `getAvailableOutlines`。

---

### editTextOutline

- **用途**：写入指定大纲条目的「文本大纲」Markdown（总纲 / 卷大纲 / 章节大纲 / 其他大纲 / 写作大纲均可），整体覆盖原内容。适用于补写总纲文档、重写某条文本提纲、统一提纲口径等场景。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID（用于归属校验）。
  - `outlineId` (number, 必填)：目标大纲 ID（建议来自 `getAvailableOutlines`）。
  - `markdownContent` (string, 必填)：新的 Markdown 全文，保存时覆盖原文本大纲。
- **返回**：成功时含 success: true、outlineId、title、type、markdownLength；失败时含 success: false、error。

---

### editChapterContent

- **用途**：编辑指定章节的正文内容。将传入的 content（纯文本，段落用换行符分隔）写入该章节并保存。`chapterId` 须为左侧写作章节目录对应的章节 id。适用于按用户要求改写某一章、替换整章正文等场景。
- **参数**：
  - `chapterId` (number, 必填)：写作目录章节 ID。
  - `content` (string, 必填)：章节新正文，纯文本，段落之间用换行符分隔。
- **返回**：成功时含 success: true、message、chapterId；失败时含 success: false、error。只有返回 success 后才可告知用户修改已完成。

---

### searchMemories

- **用途**：按需查找本书的**长期记忆**，用于补充上下文。记忆分五层：**全局**（书名、主题、风格、世界观）、**大纲**（结构、章节任务、剧情走向）、**人物**（人设、关系、目标、缺陷、成长）、**章节**（本章内容与细节）、**伏笔**（埋入的伏笔及回收状态）。当需要回忆设定、核对伏笔、保证前后一致时调用。
- **参数**：
  - `bookId` (number, 必填)：当前书籍 ID。
  - `query` (string, 可选)：检索关键词，与当前问题或要回忆的内容相关；不传则返回近期记忆。
  - `layer` (string, 可选)：限定层级：全局 / 大纲 / 人物 / 章节 / 伏笔；不传则检索所有层。
  - `chapterId` (number, 可选)：当前章节 ID，检索章节记忆时可优先本章。
  - `limit` (number, 可选)：最多返回条数，默认 15。
- **返回**：按层级组织的记忆文本（含伏笔时会有类型、状态等）。未找到时返回「未找到与当前检索相关的长期记忆」。

---

## 使用建议

- 需要 **文本层大纲 Markdown**（非目录树）时用 **getTextOutline**；需要 **章节树结构** 时用 **batchGetOutlineDetails**。
- 用户要求修改总纲/卷/章节/其他大纲的文本提纲时，用 **editTextOutline**（先用 `getAvailableOutlines` 确认 `outlineId`）。
- 需要某章或某几章正文时用 **getChapterContent** 或 **batchGetChapterContents**；chapterId 必须来自写作目录。
- 需要人物或小说背景时用 **getBookCharacters**（可按 ID 或按名子集）、**listBookCharacters**（只要名称与 ID 对照时）、**getStoryBackground**。
- 用户要求写入、修改、改写、重写或替换某章内容时，必须调用 **editChapterContent** 并收到 success 后再回复完成，不得仅回复「已完成」而未实际调用该工具。
- **需要回忆设定、核对伏笔、保证前后一致**时，调用 **searchMemories**：传入与当前问题相关的 `query`（或限定 `layer`），获取本书的长期记忆后再作答。
- **回复用户时请使用书籍名、章节名等名称**（如「已修改《第一章》」），**不要直接暴露或返回 ID**（如「已修改 chapterId 5」）。


# 小说创作 Agent 旧实现合同盘点（F1）

> 盘点日期：2026-09-12
> 状态：历史 characterization；旧 Profile/context/tool/skill 源码已于 2026-09-14 删除。
> 原旧实现入口：`backend/application/writing_agent_profile.py`；该路径已不存在。
> 新实现目标目录：`backend/agents/writing/`。
> 下文“当前”仅指 2026-09-12 盘点快照，所列工具名与路径可能已退役。

## 1. 结论摘要

旧小说创作 Agent 并非没有“读取故事背景”和“读取人物”的能力。当前 Catalog 中确实注册了：

- `getStoryBackground`：读取当前书籍故事背景；
- `listBookCharacters`：返回当前书籍人物的 `id`、`name` 和可选 `materialLink`；
- `getBookCharacters`：返回人物详情，可按 ID 或姓名过滤。

这三个工具均有合法 schema、handler、READ policy 和展示名。普通的、绑定 `bookId`、启用 Agent tools 且没有受限 Novel Knowledge scope 的 Run 可以获得它们。

用户实际收到“没有能力查看”的最可信代码原因不是工具缺失，而是动态授权过滤：`enabled_writing_tools()` 在存在 `knowledge_scope` 且 `purpose != "discussion"` 时，会同时移除上述三个工具。Novel Knowledge binding 的默认 purpose 是 `prose`，因此一本绑定了知识库但没有显式切到 author discussion scope 的书，恰好比没有绑定知识库的书少这些当前状态读取能力。恢复 Run 时还有 `guard_legacy_registration()` 再次执行同类阻断。

“本书共有多少个人物”没有专用 count 工具。当前最合适的调用是 `listBookCharacters`，其返回值是完整 JSON 数组，但没有 `total` 字段，也没有分页合同。模型可以根据数组长度作答，却没有宿主计算出的权威计数；现有测试也没有覆盖真实 Provider 对这个自然语言问题的工具选择和精确计数。

这些事实应成为新实现的回归输入；动态过滤造成的不可用、无权威 count、无分页等问题不得继承为新合同。

## 2. 当前事实合同

### 2.1 HTTP 入口与请求生命周期

| 入口 | 当前用途 | 关键合同 |
| --- | --- | --- |
| `PUT /api/ai/chat/requests/{requestId}` | 在启动模型前预留 durable Writing request | `requestId == streamId`；必须为 `chatAgentMode=agent`、有 `sessionId`、API key 和 model；校验会话历史 fence；同一 request digest 幂等 |
| `POST /api/ai/chat/stream` | 创建 Writing Root Run 并传输初始 SSE | Agent 模式下 renderer 先完成 request reservation；断开 SSE 只 detach，不等同于取消 Run |
| `POST /api/ai/chat/requests/{requestId}/cancel` | Run 尚未绑定时也可取消 | request 已绑定 Run 后转交规范 Run cancellation control plane |
| `POST /api/ai/agent-runs/{runId}/cancel` | 显式持久取消 Root Run | terminal Run 不再接受新的取消；返回 cancellation receipt 状态 |
| `POST /api/ai/tool-approvals/{approvalId}` | 解决 CONFIRM 工具的一次性审批 | 只接受尚未处理且未失效的 approval |
| `GET /api/ai/session-runs/latest` | 页面恢复时定位最新/指定 request 的 Run | 返回 request receipt、prompt 和 Run snapshot |
| `GET /api/ai/agent-runs/{runId}` | 按 cursor 读取 Run snapshot | 额外投影 Writing setting proposal product events |
| `GET /api/ai/agent-runs/{runId}/events` | 初始 SSE 中断后的 durable 事件续传 | 校验 `sessionId` 所有权；传输公共 canonical events，不混入 product proposal body |

主要请求 DTO 是 `schemas.ai.ChatStreamRequest`。标准 renderer Agent 路径中，工具真正可用至少要求：

1. renderer 发送 `chatAgentMode="agent"`；后端旧兼容请求本身并未把 mode 作为 `tools_enabled` 的必要条件；
2. `enableAgentTools=true`；
3. 存在非空 `bookId`；
4. 所选模型的协议能力允许 tool calling；
5. 动态 `enabled_writing_tools()` 没有移除目标工具；
6. Planner/模型在本轮确实选择该工具。

调用方提供的 `tools` 或 `tool_choice` 被明确拒绝，调用方不能向旧 Writing Agent 动态注入工具。

### 2.2 Run、会话和上下文绑定

`to_writing_agent_request()` 把 HTTP 请求映射为 `AgentRunRequest`，domain namespace 固定为 `purrtypos.writing`。初始域上下文包含：

- `book_id`、`chapter_id`、当前章节标题；
- 关联章节、大纲 ID；
- 用户选择的短期记忆、长期记忆和伏笔 ID；
- context window label；
- `writing_technique_input_id`；
- 会话、模式、locale 和 `streamId` metadata。

`WritingAgentProfile.prepare_request()` 在提交 Root Run 前由数据库补齐：

- 当前写作章节目录；
- 当前书籍可用大纲目录；
- Novel Knowledge immutable scope snapshot；
- Writing Technique frozen snapshot；
- 原创/续写模式、续写 binding 和继承 canon records。

Run binding 当前有两种：

- durable session request：`namespace=writing.chat.request`，`aggregateId=sessionId`，`commandId=streamId`；
- 没有 durable request 但有书籍上下文：`namespace=writing.context`，`aggregateId=bookId`，command 为 stream ID 或随机 UUID。

binding attributes 至少保存 `bookId`；可能保存 `writingTechniqueSnapshot`、`novelKnowledgeScope`、`creationMode=continuation` 和 `continuationBinding`。恢复时这些持久化 attributes 是知识范围校验的重要依据。

模型可见的工具 schema 会移除 `bookId`；handler 调用前宿主以 Root Run 的 `bookId` 覆盖模型参数。`getChapterContent` 和 `editChapterContent` 在模型省略/传空 `chapterId` 时，可以使用宿主绑定的当前章节；模型显式传入的其他章节仍要经过目录与书籍范围校验。

### 2.3 动态工具授权

旧 Catalog 静态登记 40 个工具，但每个 Run 的 allowlist 由 `enabled_writing_tools()` 动态生成：

- domain namespace 不匹配或没有 `bookId`：不启用任何 Writing 工具；
- 没有 Novel Knowledge scope：禁用 `searchNovelKnowledge`、`readNovelKnowledge`，其余书籍工具可用；
- 有 scope 且 `purpose != discussion`：禁用人物、背景、设定、全局大纲、记忆等“当前状态”读写工具；
- Writing Technique mode 不是 `auto`：禁用 `searchWritingTechniques`；
- 没有手选/candidate technique：禁用 `readWritingTechnique`；
- 不是有效续写 binding：禁用 `readContinuationSourceSection`。

当 Planner 把工具写入 `expectedTools` 时，执行阶段只暴露当前 step 对应的工具。部分工具还有显式依赖，例如：

- `getBookCharacters -> listBookCharacters`；
- `updateCharacter -> getBookCharacters`（context contract，比旧 planning map 更严格）；
- `editStoryBackground -> getStoryBackground`；
- `queryOutline -> listOutlines`；
- `updateOutline -> listOutlines + queryOutline`；
- 章节读取/编辑通常先取得 writing chapter catalog。

### 2.4 工具目录、schema 与副作用

下表的参数是冻结 `SKILL.md` 中的完整参数；`bookId` 在模型可见 schema 中会被宿主移除并自动绑定。`*` 表示 schema required。

#### 只读工具（READ，无用户审批）

| 工具 | 参数 | 当前结果/来源 |
| --- | --- | --- |
| `searchNovelKnowledge` | `query*` | 检索绑定的 Novel Knowledge；结果携带版本化 evidence receipts |
| `readNovelKnowledge` | `documentId*`, `revision*`, `chunkId` | 按版本 locator 读取知识文档 |
| `getChapterContent` | `chapterId`, `title`, `maxTextLength` | 当前书籍单章正文；只正式支持 chapter ID/current chapter |
| `listWritingChapters` | `bookId*` | 写作目录及可选续写来源目录 |
| `batchGetChapterContents` | `chapterIds*`, `maxTextLength` | 多章正文数组 |
| `getBookCharacters` | `bookId*`, `characterIds`, `names` | 人物详情 Markdown；不传筛选即全部 |
| `listBookCharacters` | `bookId*` | 裸 JSON 数组，每项 `id/name` 及可选 `materialLink` |
| `getStoryHealthDashboard` | `bookId*` | 章节/字数、伏笔和人物长期未出现统计 |
| `getWritingStatsDashboard` | `bookId*` | 写作字数、目标和近 30 日统计 |
| `searchSparkIdeas` | `bookId*`, `query`, `layer`, `chapterId`, `limit` | 搜索旧 spark ideas 与伏笔 |
| `searchMemories` | `query*` | 检索版本化长期记忆组件 |
| `queryOutline` | `bookId*`, `outlineIds`, `outlineId`, `maxTextLength` | 指定大纲内容；ID 是正式 locator |
| `getGlobalOutline` | `bookId*`, `maxTextLength` | 当前书籍总纲 |
| `listOutlines` | `bookId*` | `{total,outlines}` 目录 |
| `listSettingEntities` | `bookId*` | 世界设定实体目录 |
| `getSettingEntities` | `bookId*`, `entityIds`, `names`, `entityType` | 世界设定详情 Markdown |
| `getStoryBackground` | `bookId*` | 故事背景正文，可能包含只读继承基线与 `materialLink` |
| `searchWritingTechniques` | `query*` | 只在 auto mode 检索已授权写作技法 |
| `readWritingTechnique` | `selection*`, `technique`, `path` | 读取 frozen technique snapshot 内允许的文件 |
| `readContinuationSourceSection` | `bookId*`, `offset`, `limit`, `sectionId` | 只读查看续写分叉点之前的来源目录/章节 |

#### 提议工具（PROPOSE，工具本身不持久化正式内容）

| 工具 | 参数 | 当前副作用合同 |
| --- | --- | --- |
| `editChapterContent` | `chapterId`, `content*` | 产生 `writing.proposed_chapter_diff`；由编辑器 diff 流程决定是否写入 `articles` |
| `updateCharacter` | `bookId*`, `characterId*`, `name`, `tags`, `profileMd` | 产生 `writing.proposed_setting_diff(kind=character)`；SettingDiff commit/reject 独立处理 |
| `updateSettingEntity` | `bookId*`, `entityId*`, `name`, `tags`, `profileMd` | 产生 `writing.proposed_setting_diff(kind=entity)` |
| `editStoryBackground` | `bookId*`, `content*` | 产生 `writing.proposed_setting_diff(kind=background)` |

PROPOSE 不是 PurrA approval：模型调用工具后即可创建 proposal event，但正式业务数据仍需用户在产品 diff UI 中接受。Proposal 的 occurrence identity 由 `(runId, toolCallId, effectIndex)` 派生。

#### 确认工具（CONFIRM，PurrA 一次性审批后直接持久化）

| 工具 | 必需参数（另有可选字段） | 风险 |
| --- | --- | --- |
| `createWritingChapter` | `bookId`, `parentId` | WRITE |
| `createCharacter` | `bookId`, `name` | WRITE |
| `deleteCharacter` | `bookId`, `characterId` | DESTRUCTIVE |
| `addSparkIdea` | `bookId`, `layer`, `content` | WRITE |
| `updateSparkIdea` | `id` | WRITE |
| `deleteSparkIdea` | `id` | DESTRUCTIVE |
| `addForeshadowing` | `bookId`, `chapterId`, `content` | WRITE |
| `createMemory` | `bookId`, `kind`, `content` | WRITE |
| `updateMemory` | `id`, `version` | WRITE |
| `archiveMemory` | `id`, `version` | WRITE |
| `linkMemories` | `bookId`, from/to memory ID 与 version、`relation` | WRITE |
| `resolveForeshadowing` | `id`, `resolvedChapterId` | WRITE |
| `editGlobalOutline` | `bookId`, `markdownContent` | WRITE |
| `updateOutline` | `bookId`, `outlineId` | WRITE |
| `createSettingEntity` | `bookId`, `entityType`, `name` | WRITE |
| `deleteSettingEntity` | `bookId`, `entityId` | DESTRUCTIVE |

CONFIRM 工具的 pending approval 持久化在 `ai_agent_approvals`。审批通过后 handler 执行，工具 receipt 记录 effect state；审批拒绝、超时或应用重启都必须 fail closed。重启时旧实现把 pending approval 置为 unavailable，并取消仍为 running 的 Run，不恢复到等待审批状态。

### 2.5 三个重点只读能力

#### `getStoryBackground`

当前事实：

- handler 通过宿主 `bookId` 调 `get_story_background()`；
- 没有背景时返回 `（暂无小说背景）`，不是能力错误；
- 可以组合 material link、续写继承基线和本书后续发展；
- policy 是 READ，不需要审批；
- 读取成功属于 replanning evidence，后续 Planner 可重排未完成语义步骤；
- 在受限 knowledge scope 中会从 allowlist 被移除，恢复时也可能被 guard 拒绝为 `knowledge_historical_projection_unavailable`。

#### `listBookCharacters`

当前事实：

- 从当前书籍的 characters/material projection 读取全部人物；
- 返回完整 JSON 数组；姓名中的换行被压成空格；
- 空书返回 `[]`；
- policy 是 READ，不需要审批；
- context result projection 在被后续步骤消费后可压成 receipt。

缺少的合同：没有 `total`、分页 cursor、limit、稳定排序说明和输出 schema。人物很多时，数组会扩大模型输入；新实现不能继续依赖模型数数组项。

#### `getBookCharacters`

当前事实：

- 不传 `characterIds/names` 时返回全量人物档案；
- `characterIds` 优先，否则 `names` 模糊过滤；
- 输出是 Markdown 文本，不是结构化对象；
- policy 是 READ，不需要审批；
- planning 依赖先调用 `listBookCharacters`；
- 同样受 non-discussion knowledge scope 阻断。

#### “本书共有多少个人物”

旧实现能够以 `listBookCharacters` 的数组长度作为答案依据，所以“完全没有能力”不是准确描述。但它没有一个可验证的宿主 count 字段，模型仍可能：

- 没选中工具；
- 在受限 scope 下根本看不到工具；
- 选中 `getBookCharacters` 而不是轻量列表；
- 对较长/截断上下文计数错误；
- 把历史继承人物、当前书人物或设定实体混为同一统计口径。

新实现验收应要求工具返回类似 `{total, items, nextCursor}`，回答必须引用同一调用中的 `total`，并明确定义“当前书人物”是否包含只读继承材料投影。

### 2.6 事件、SSE 与前端投影

初始传输由 `/ai/chat/stream` 返回 SSE。每个公共 canonical output event 映射为：

- `eventId/outputStreamId/runId/rootRunId/parentRunId/agentId`；
- `turnId/invocationId/sequence`；
- `source/kind/channel/visibility/payload`；
- `occurredAt/emittedAt`。

终态另发 transport `runResult={runId,status,errorCode}`。公共 SSE 只发送 `visibility=public` 事件；私有 validated result 和内部证据不直接暴露。

Writing 产品 effect 类型为：

- `writing.proposed_chapter_diff`；
- `writing.proposed_setting_diff`；
- `writing.setting_updated`；
- `writing.chapter_created`；
- 未识别 progress payload 归为 `writing.progress`。

`GET /ai/agent-runs/{runId}/events` 用 durable cursor 续传 canonical public events，并移除 product event bodies。设定 proposal 由 `GET /ai/agent-runs/{runId}` 的 `SqliteWritingProposalReadModel` 从 run event + tool receipt 重新证明和投影。前端 `AiPanel`：

- `useChatSubmit` 创建 stream request、传递书籍/章节/选中上下文；
- `backendApi.aiChatStream` 先 reserve，跟踪 Root Run ID，断流后订阅 durable events；
- `bookChunkSideEffects` 把章节 proposal、setting updated、chapter created 分派到产品 UI；
- `bookProposalProjection` 和 `bookConversationHydration` 恢复 setting proposals；
- `SettingDiffContext`/`DiffContext` 负责正式接受或拒绝候选改动。

设置面板另外直接调用 `/books/{bookId}/characters` 和 `/story-background/{bookId}` 等 CRUD API；这证明 UI 本身能读取数据，但它不是 Agent 工具选择链的替代证据。

### 2.7 持久化与 Artifact

#### Agent/对话持久化

- `ai_writing_chat_requests`：pre-Run request reservation、binding、取消和 rejection；
- `ai_agent_runs`：Root Run、模型/runtime/binding/cancellation/usage/terminal result；
- `ai_agent_run_todos`：Planner steps；
- `ai_agent_run_events`、`ai_agent_output_streams`：canonical journal 与公共输出流；
- `ai_agent_tool_receipts`：工具参数 digest、结果、effects、错误和 effect state；
- `ai_agent_approvals`：CONFIRM 一次性审批；
- `ai_agent_run_cancellations`：规范取消审计；
- `ai_agent_run_model_attempts`、`ai_model_sdk_requests`：模型尝试/SDK 诊断；
- `ai_sessions`、`ai_conversations`、`ai_conversation_summaries`：会话、最终消息和长会话摘要；
- `ai_conversations.agent_process`：Setting proposal resolution 等产品覆盖层。

#### 业务数据

- 章节：`outlines`、`outline_chapters`、`articles`、`chapter_canvas`、`chapter_diff_history`；
- 人物：`characters`、`character_history`；
- 背景/设定：`story_background`、`story_background_history`、`setting_entities`、`setting_entity_history`；
- 大纲：`outlines`、`outline_history`；
- 旧 spark ideas/伏笔/统计：`ai_memories`、`ai_foreshadowing`、`book_word_stats`、`settings`；
- 长期记忆：PurrA memory component 的版本化 journal，宿主另有 source delivery/outbox 表；
- Novel Knowledge：`novel_knowledge_*`；
- 续写：`continuation_*`、`creation_material_*`；
- Writing Technique：`writing_technique_*`。

#### Artifact 结论

普通 Writing chat Root Run 当前不使用 `ai_agent_artifacts` 保存章节/设定候选。它依赖 canonical event、tool receipt、前端 diff 状态和 conversation product projection。名称为 `writing_technique_generation_tools.py` 的 Artifact 写入实际属于小说分析/技法生成流程，不应误认为 Writing chat Agent 的 Artifact 合同。

新实现是否要为 proposal 引入 attempt-aware Artifact 尚未决定；但不能在迁移中同时写旧 event-only proposal 和新 Artifact，除非其中一条是明确只读 shadow。

### 2.8 取消、断流、恢复和重启

当前事实：

- renderer abort 会先取消 durable request；Run 已绑定后请求规范 Root Run cancellation；
- SSE transport close 只 detach subscriber，后台 Core 继续持有 Run 到终态；
- `run_signal=asyncio.Event()` 在该路由中不会因断流置位，实际取消权属于持久 control plane；
- Run snapshot 和 cursor event stream 支持页面/传输恢复；
- session request receipt 用历史 conversation/run ID fence 防止编辑消息后把旧请求接到新历史；
- orphan recovery 对无 live owner 的普通 Root Run做统一 terminal settlement；Writing chat 本身没有 Durable Task/Unit resume；
- 应用启动会恢复未绑定 Writing request，并把遗留 pending approval 置 unavailable、取消相关 Run；
- proposal recovery 依赖 journal + receipt 证明，不能只信前端保存的 proposal body。

因此旧 Writing Agent 的“恢复”主要是恢复 Run 输出和 proposal 展示，而不是从最后一个模型/工具 step 无损继续执行。新实现若宣称可恢复，必须明确是 replay、重新执行，还是 operation checkpoint continuation。

## 3. 已知缺陷：新实现不得继承

### W-L1：能力存在但动态授权造成真实不可用

`enabled_writing_tools()` 在 Novel Knowledge scope 为 `prose` 或 `character` 时移除人物、背景等当前状态工具。`scope_snapshot()` 又默认把有效 binding 解释为 `prose`，除非用户已保存 discussion scope。这能直接解释某些书里模型回答“无法查看”，而相同问题在未绑定知识库的书里可能可用。

这不是单纯提示词问题，也不能靠“把三个工具重新注册一次”解决。新实现必须把“防止未来信息泄漏的历史视角”与“作者查询本书当前资料”拆成显式 capability/scope，不能用一个隐式 purpose 同时决定知识检索和产品数据库读取。

### W-L2：人物统计不是权威结构化合同

`listBookCharacters` 只返回裸数组，没有 `total`、分页和统计口径；“人物总数”由模型数数组。新实现必须由宿主返回总数，禁止模型凭对话上下文或人物详情文本猜测。

### W-L3：列表没有统一的有效分页合同

人物、设定列表无分页；章节目录和批量章节工具存在直接对 `json.dumps(...)[:24000]` 截字符串的实现，会产生非法 JSON。新实现必须先按对象/字符预算裁剪或分页，再完整序列化，并返回 `truncated/nextCursor/total` 等明确字段。

### W-L4：章节清空语义不明确

`editChapterContent` 的 SKILL schema 要求 `content`，但 handler 对缺少或非字符串的 content 默认成空串。标准 Core schema validation 通常会拦截“缺字段”，所以“正常调用必然清空”不成立；不过空字符串本身合法，绕过/旧调用也可能形成整章清空 proposal。新合同要么明确 `clear=true`，要么禁止空正文，不能用缺省值代表清空。

### W-L5：错误分类过粗

handler 只要返回 JSON object 且有 `error`，Catalog 一律映射成 `tool_execution_failed`。参数错误、scope 拒绝、乐观并发冲突、暂时存储错误和业务不存在没有稳定分类，不利于有界重试和用户说明。

### W-L6：启动失败可能重复结算且部分路径缺日志

`AgentRunService`、`_stream_composed_agent()` 和 `/ai/chat/stream` 外层均可能调用 `on_start_failed()`。未绑定 Run 的 receipt-only 分支还会直接返回公开结果而不记录原始 exception。新生命周期必须由单一层持久结算，其他层只投影/记录附属诊断。

### W-L7：本地取消 signal 是误导性空壳

旧路由创建 `run_signal`，但断流只设置 `transport_closed`；没有 cancellation reason 时 AgentRunService 也不会创建 signal watcher。当前系统实际依赖 durable control plane。新实现应删掉无效参数或注入 control-plane-backed signal，避免测试误以为本地 Event 能取消。

### W-L8：普通 Writing chat 没有 operation 级 proposal Artifact

候选主要依靠 event/receipt/conversation overlay。它能支持现有 UI 回放，但无法天然表达 attempt、effect uncertainty 和写后结算未知。新实现若采用 Artifact，必须先定义 owner identity、OPEN/finalized 状态和旧 proposal read adapter。

### W-L9：`allowWithoutTechniques` 是无效但进入 digest 的参数

续写创建把该参数写入 request digest，却没有对应业务分支。改变这个无效开关会造成同 operation ID 的 409。它属于相关创作入口合同，但不是 Writing chat Catalog 工具；新实现要么实现语义，要么从新版本 DTO 中删除。

### W-L10：此前归到 Writing Agent 的 display 闭包缺陷实际属于分析流程

`application/writing_technique_generation_tools.py` 中的晚绑定 `name` 确实存在，但这些 registrations 服务小说分析/写作技法生成 Artifact，不是上述 40 个 Writing chat 工具。重构归属应放到 Novel Analysis 或 shared technique generation，不能为修它而修改冻结 Writing chat catalog。

## 4. 待决策项

1. **作者讨论与角色视角如何分权**：是否给 Run 增加显式 `authorCurrentState` 与 `storyTemporalScope` 两个正交能力，而不是复用 Novel Knowledge `purpose`。
2. **人物总数口径**：只计当前书 `characters`，还是把续写继承材料中的人物投影计入；被删除/归档人物是否计入。
3. **人物列表返回合同**：建议 `{total,items,nextCursor,snapshotRevision}`；需决定稳定排序和默认 page size。
4. **故事背景来源合并**：当前本书背景、继承 baseline、Obsidian materialLink 是拼成文本；新合同应返回分字段还是渲染后 Markdown。
5. **只读工具是否允许在 ask mode 使用**：当前 renderer 只有 Agent mode 会开启 tools；产品是否需要一个只读问答模式要另行决定。
6. **Planner 是否是简单查询的必经步骤**：查看背景/人数可以直接 capability routing，还是仍让 Planner 生成 Todo；这会影响延迟、成本和工具选择稳定性。
7. **PROPOSE 的 durable owner**：继续使用 event+receipt，还是引入 operation-scoped proposal Artifact；不得双写两个权威来源。
8. **章节清空**：是否是允许能力；若允许必须显式操作和独立确认文案。
9. **审批重启语义**：沿用 fail closed/cancel，还是恢复 pending approval；后者需要 durable waiter/continuation，而不只是恢复 UI。
10. **长期记忆与 spark idea 的边界**：两个持久系统同时暴露给模型，需确定新工具是否合并命名、如何区分版本和 scope。
11. **历史 scope 读能力**：允许模型浏览旧 revision 但禁止其成为正式写入证据，还是完全不可见；需要 evidence admission 而不只是 tool allowlist。
12. **proposal 的修改与提交职责**：模型只提出候选，用户 diff UI 提交；还是 approval 后由 Agent commit。两者的幂等/effect state 不同。

## 5. 现有测试覆盖

### 5.1 已有确定性证据

- `test_writing_skill_catalog.py`：SKILL discovery、frontmatter/schema 装载和无效 skill 跳过；
- `test_writing_tool_infrastructure.py`、`test_writing_tool_runtime.py`：40 个 policy/schema/handler 覆盖、READ/PROPOSE/CONFIRM mode、实例隔离与错误适配；
- `test_host_owned_writing_arguments.py`：模型 schema 隐藏 `bookId`、宿主覆盖 book scope、current chapter default；
- `test_writing_domain_adapter.py`、`test_writing_domain_boundaries.py`：domain adapter 和 effect 转换；
- `test_writing_planning_policy.py`、`test_writing_planning_context.py`：工具依赖与 planning context；
- `test_agent_composition.py`：mock Provider 下 `getStoryBackground` 的计划、调用和 replanning；`listBookCharacters -> deleteCharacter` 审批链；
- `test_ai_composed_sse_wire_contract.py`：mock Provider 下人物工具缺失调用的显式失败、人物列表与恢复 wire contract；
- `test_novel_knowledge.py`：持久 scope、历史时间 gate；明确断言受限 scope 的 `getBookCharacters` 返回 `knowledge_historical_projection_unavailable`；
- `test_continuations.py`：续写 binding 下继承读取能力与 source boundary；
- `test_writing_chat_request_store.py`、`test_writing_chat_request_routes.py`：request reservation、history fence、binding、取消和恢复；
- `test_run_execution_control.py`、`test_purra_runtime_cancellation_race.py`、approval 相关测试：Run cancellation 与一次性审批；
- `test_conversations_routes.py`、`test_setting_diff_resolution_atomicity.py`、前端 proposal projection/hydration tests：setting proposal 的 journal ownership、提交/拒绝和回放；
- `test_writing_chapter_tool_boundaries.py`、`test_writing_outline_tool_safety.py`：章节、大纲范围与写入边界；
- `test_writing_context.py`、`test_writing_retrieval.py`、`test_writing_read_cache.py`：上下文、evidence 和缓存。

### 5.2 测试不能证明的事项

上述关键工具选择测试使用 monkeypatch Provider，并由测试代码预先写死 Planner payload 和 tool call，因此只能证明“模型已经选择工具以后，宿主接线能执行”，不能证明真实 Provider 会从自然语言问题稳定选中工具。

目前没有发现以下验收：

- 真实 Provider 对“查看这本书的故事背景”的稳定 tool selection；
- 真实 Provider 对“本书共有多少个人物”的 `listBookCharacters` 选择和精确计数；
- 有/无 Novel Knowledge binding、`prose/discussion/character` scope 的完整能力矩阵；
- 0、1、大量人物时的 total/pagination/模型输入预算；
- Electron 真实 Agent 开关、全局会话/章节会话 book binding；
- Electron 审批、断流恢复、应用重启和 proposal 回放的一体化验收；
- Provider 不支持 required tool choice、模型漏调工具、工具参数无效时的用户可理解诊断；
- 历史数据库升级后，同一自然语言问题与当前数据库值的一致性。

## 6. 新实现的最小 characterization 验收清单

在替换路由切流前，至少用隔离 fixture 和真实 Provider 各完成一次：

1. 无知识库 binding：读取背景、统计 0/1/N 个人物；
2. discussion binding：同样三项能力可用；
3. prose/character temporal scope：明确验证作者查询和角色视角查询各自的允许/拒绝行为，不能笼统回复“没有能力”；
4. `bookId` 由宿主绑定，模型无法读取另一书籍；
5. 人物总数由工具结构化 `total` 给出，items 分页不影响 total；
6. 空背景、空人物返回成功的空态，不伪装为工具不可用；
7. story/character query 的 tool call、receipt、canonical event 和最终答案能在断流恢复后重放；
8. 章节/人物/背景写入保持 propose/approve/commit 分离，拒绝不产生业务写入；
9. 显式取消跨重启可观察，网络断开不自动取消；
10. legacy Run 继续由 legacy reader 回放，新 Run 不写 legacy proposal owner key。

## 7. 盘点证据索引

- Profile 与请求准备：`backend/application/writing_agent_profile.py`
- Run 入口：`backend/application/writing_agent_service.py`
- HTTP/SSE：`backend/routers/ai.py`、`backend/application/sse_mapping.py`
- 请求映射：`backend/application/request_mapping.py`
- 域上下文：`backend/domains/writing/contracts.py`、`context.py`、`execution_state.py`
- 工具策略与授权：`backend/domains/writing/policies.py`、`tools/catalog.py`
- schema loader：`backend/infrastructure/writing/skill_catalog.py`、`backend/skills/*/SKILL.md`
- handler：`backend/infrastructure/writing/tools/handlers/`
- scope guard：`backend/infrastructure/writing/knowledge_retrieval.py`
- Proposal 证明/持久投影：`backend/application/writing_proposal_read_model.py`、`book_conversation_product_projection.py`
- request receipt：`backend/infrastructure/persistence/writing_chat_request_store.py`
- 取消/孤儿恢复：`backend/application/agent_cancellation_service.py`、`agent_orphan_recovery_service.py`
- renderer 调用：`src/Workspace/AiPanel/hooks/useChatSubmit.ts`、`src/services/backendApi.ts`

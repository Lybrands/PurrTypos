# 剧本 Agent：入口与领域实现设计

## 1. 产品判断

剧本入口应当同时支持两种创作起点：

1. **改编书架作品**：选定一本书作为只读素材源，Agent 按需引用人物、世界设定、大纲、章节与记忆。
2. **开启原创故事**：先建立独立剧本项目，通过对话逐步确认人物、冲突、主题与形式，不强制先在书架创建小说。

二者进入同一套剧本生产流程，但来源、权限与第一轮任务不同。入口不应直接展示空白聊天框，而应先收集一份最小创作简报。

## 2. 入口状态

```text
选择起点
  ├─ 改编书架作品 ─ 选择 source_book_id
  └─ 原创故事 ───── 不绑定书籍
          ↓
最小创作简报
  - project_title
  - screenplay_format
  - adaptation_mode / exploration_start
  - premise（可空）
          ↓
创建剧本项目
          ↓
进入剧本 Agent 工作台
          ↓
书架改编：原作范围分析（原创项目跳过）
```

当 `premise` 为空时，原创项目的第一轮任务是澄清；书架改编仍先完成原作范围
分析。两种情况都不应阻止用户进入工作台。

## 3. 剧本工作流

剧本 Agent 不以“一次生成完整剧本”为默认行为，而以可确认的中间产物推进：

| 阶段 | 主要产物 | 用户控制点 |
|---|---|---|
| Orientation | 原作范围分析 / 原创澄清问题 | 接受事实底座或确认创作目标 |
| Creative brief | 受众、形式、主题、改编原则 | 接受或修改简报 |
| Structure | 故事梗概、节拍表、分集结构 | 锁定结构版本 |
| Scene plan | 场景表、人物目标、冲突与转折 | 调整场景顺序 |
| Draft | 按场生成的剧本正文 | 逐场接受或重写 |
| Review | 连贯性、人物弧光、节奏检查 | 决定修订项 |

每个阶段的产物独立版本化。后续阶段只消费用户选定的上游版本。

## 4. 领域边界

现有 `agent_core` 不需要加入任何“剧本”判断。新增 `domains/screenplay`，通过现有端口注入：

```text
API / SSE
    ↓ agentProfile = "screenplay"
Screenplay Request Mapping
    ↓
Agent Core（保持不变）
    ↑
Screenplay Planning / Policy / Response
    ↑
Screenplay Tool Catalog
```

建议在现有 `/ai/chat/stream` 请求上增加以下字段：

```ts
interface ScreenplayAgentContext {
  agentProfile: "screenplay";
  screenplayProjectId: string;
  sourceBookId?: string | null;
  activeDocumentId?: string | null;
  activeStage: "orientation" | "brief" | "structure" | "scenes" | "draft" | "review";
}
```

默认 `agentProfile` 保持为 `writing`，避免影响现有工作台。

## 5. 数据模型

### screenplay_projects

| 字段 | 含义 |
|---|---|
| id | 剧本项目 ID |
| title | 项目名称 |
| source_kind | `book` / `original` |
| source_book_id | 书架来源，可空 |
| source_scope_json | 不可变的原作改编范围与稳定章节 ID 快照 |
| format | 短片、电影、单集剧、连续剧、竖屏短剧 |
| approach | 改编方式或原创探索起点 |
| premise | 用户的初始描述 |
| active_stage | 当前工作流阶段 |
| create_time / update_time | 时间 |

### screenplay_documents

| 字段 | 含义 |
|---|---|
| id | 文档 ID |
| project_id | 所属剧本项目 |
| kind | `source_analysis` / `creative_brief` / `beat_sheet` / `episode_outline` / `scene_list` / `scene_draft` / `review` |
| title | 文档标题 |
| content_json | 结构化内容 |
| content_text | 可编辑 / 可导出的文本表示 |
| version | 版本号 |
| status | `draft` / `accepted` / `superseded` |
| derived_from_ids | 上游文档版本 |

### screenplay_source_refs

记录某个剧本文档引用了哪些原作素材及其版本摘要：

| 字段 | 含义 |
|---|---|
| document_id | 剧本文档 |
| source_type | chapter / outline / character / setting / memory |
| source_id | 原作对象 ID |
| source_revision | 引用时的内容摘要 |
| excerpt | 可展示的短引用或说明 |

它用于回答“这段改编来自哪里”，也用于原作变化后的过期提示。

## 6. 工具设计

剧本 Agent 只注册当前阶段需要的工具。

### 只读素材工具

- `getSourceBookOverview`
- `getSourceCoveragePlan`
- `readSourceCoverageBatch`
- `searchSourceMaterial`
- `readSourcePassages`
- `getSourceCharacters`
- `getSourceWorldSettings`
- `getScreenplayProject`
- `getScreenplayDocument`

这些工具可以复用现有 Writing 数据加载器与 handler，但在剧本目录中只暴露只读子集，并强制 `source_book_id` 归属校验。

### 可审阅提案

- `proposeCreativeBrief`
- `proposeBeatSheet`
- `proposeEpisodeOutline`
- `proposeSceneList`
- `proposeSceneDraft`
- `proposeScreenplayRevision`

提案只生成 diff 或候选版本，不直接覆盖已接受文档。

### 需要确认的写入

- `createScreenplayProject`
- `acceptScreenplayDocument`
- `saveScreenplayDocumentVersion`
- `archiveScreenplayProject`

所有持久化写入沿用一次性人工确认与幂等边界。

## 7. 上下文策略

不要把整本书直接拼进提示词。推荐顺序：

1. 入口只传 `source_book_id` 和创作简报。
2. Planner 根据当前阶段先读取书籍概览。
3. Agent 用检索工具定位相关章节、人物与设定。
4. 只把命中的最小证据集放入当前轮上下文。
5. 生成文档时保存 `screenplay_source_refs`。

这样既能控制 token，也能让改编结果可追溯。

## 8. 必须保持的产品不变量

1. 来源书籍默认只读，剧本 Agent 不获得修改原作的工具。
2. 原创剧本项目不会隐式创建书籍；“同步到书架”应是单独且可确认的动作。
3. 一次 Run 只能绑定一个剧本项目和至多一个来源书籍。
4. 工具参数中的 `book_id` 不能覆盖宿主绑定的 `source_book_id`。
5. 关键中间产物必须版本化，已接受版本不能被模型静默覆盖。
6. 所有原作引用都应能追溯到对象 ID 与引用时版本。
7. 切换剧本阶段应缩小工具目录，而不是只依靠 Prompt 约束。
8. 限定章节或卷的项目不得读取范围外正文、大纲或无法证明归属范围的全局资料。

## 9. 推荐实施顺序

1. 入口原型与创作简报状态（已落地）。
2. `screenplay_projects` / `screenplay_documents` 的 CRUD 与 IPC（已落地）。
3. `agentProfile=screenplay` 的 request mapping 与 composition registry（已落地）。
4. 只读素材工具和来源追踪（已落地）。
5. Creative brief / Structure 两个阶段的提案与接受流程（已落地）。
6. 场景表、逐场正文与导出（已落地）。
7. 结构化审阅、完整修订与标准剧本 PDF（已落地）。
8. 文档预览编辑、版本差异、历史恢复与项目归档（已落地）。
9. 按整本、前 N 章、前 N 卷、指定章节或指定卷限定改编范围（已落地）。
10. 改编项目先生成带来源证据的原作范围分析，再进入创作简报（已落地）。
11. 长篇范围按正文规模生成稳定批次，区分全文精读与抽样覆盖（已落地）。
12. 改编简报把原作分析转为成片规模和可追溯的逐条改编决策（已落地）。
13. 节拍表和分集结构逐条落实改编决策，并建立稳定结构单元 ID（已落地）。
14. 场景表完整承接结构单元，贯通原作证据到具体场景的追踪链（已落地）。
15. 滚动正文累计记录每场对目标、冲突、转折与连续性的执行结果（已落地）。
16. 最终复审通过时原子固化权威版本链与内容摘要交付清单（已落地）。
17. 原创电影、限定章节改编和连续剧修订复审端到端验收套件（已落地）。

## 10. 范围化改编

书架改编项目在创建时固化 `sourceScope`，支持：

- `whole_book`
- `first_chapters`
- `first_volumes`
- `selected_chapters`
- `selected_volumes`

限定范围会解析为按阅读顺序排列的稳定章节 ID 快照。后续新增或重排原作章节
不会静默扩大项目范围。范围是项目级创作边界，创建后不可修改；如果需要改编
原作的另一段内容，应创建独立剧本项目，使对话历史、提案版本和来源凭据保持
一致。

限定范围下，书籍概览、素材搜索和段落读取只返回入选章节，以及绑定这些章节
或入选卷的章节/卷大纲。人物档案、世界设定和故事背景目前是整本书级数据，
没有章节归属信息，因此限定范围下不会向 Agent 开放，避免把后续剧情信息带入
前部章节改编。整本改编保持原有全局资料能力。

当前项目创建是一个原子操作：`screenplay_projects` 与首个
`creative_brief` v1 同时写入。剧本页会列出未归档项目，重新进入后可恢复
项目及其文档版本。删除来源书籍只会解除 `source_book_id`，不会级联删除剧本项目。

`screenplay` profile 已拥有独立的 `DomainContext`、上下文预算、项目上下文
Provider、Planning Policy、Execution State 与独立工具目录。组合根会按 profile
选择领域适配器，默认仍为 `writing`。客户端提供的 `sourceBookId` 只能用于
一致性校验；数据库中项目绑定的来源书籍和阶段始终是权威值。

剧本项目页可以选择已配置模型并流式运行剧本 Agent。普通回复用于澄清与
讨论；正式版本通过结构化提案卡展示，用户可分别执行“保存草稿”或
“接受并推进”。

当前剧本工具目录包含九个 READ 工具：

- `getScreenplayProject` / `getScreenplayDocument`
- `getSourceBookOverview`
- `getSourceCoveragePlan` / `readSourceCoverageBatch`
- `searchSourceMaterial` / `readSourcePassages`
- `getSourceCharacters` / `getSourceWorldSettings`

模型不可传入 `bookId` 或 `projectId`；每次工具执行都会从项目表重新解析
`source_book_id`，并在 SQL 查询中再次施加书籍归属条件。原创项目只启用
项目与文档读取工具，以及当前阶段适用的提案工具。

长篇范围不会要求模型自行猜测阅读顺序。`getSourceCoveragePlan` 根据锁定章节、
正文规模和章节数量生成最多 6 个连续批次，并返回绑定当前原作版本的 `planId`。
Agent 可以在一次只读工具批次中调用全部 `readSourceCoverageBatch`；每批输出受
字符预算约束，短章节返回全文，长章节返回首尾抽样并明确标记 `full` 或
`sampled`。计划生成后若原作正文发生变化，旧 `planId` 会失效，必须重新规划。
来源作品没有正文章节时不允许创建改编项目，避免进入无法完成范围分析的死路。
决定创作结论的抽样章节仍应使用 `readSourcePassages` 精读。

Orientation 阶段为书架改编项目启用 `proposeSourceAnalysis`。Agent 必须先读取
原作概览并精读范围内证据，提案结构化记录叙事摘要、人物、关键事件、核心冲突、
可改编资产、连续性风险、事实引用与章节阅读覆盖率。没有精读完整个选择范围时，
必须明确记录未覆盖内容和分析局限。保存文档时会绑定本次 Run 的来源凭据；
接受时后端再次校验证据中的 `sourceType + sourceId` 确实出现在该文档凭据中。
接受后项目从 `orientation` 进入 `brief`。在创作简报尚未接受前，分析仍可基于
新证据生成继承当前版本的修订；原创项目不启用这个步骤。

Creative brief / Structure 阶段包含三个后续 `PROPOSE` 工具：

- `proposeCreativeBrief`
- `proposeBeatSheet`
- `proposeEpisodeOutline`

书架改编项目的 `creative_brief` 不是泛化的方向说明，而是从事实底座到剧本
结构之间的决策契约：

- `sourceAnalysisId` 由服务端写入，必须指向当前已接受的原作范围分析；
- `formatPlan` 必须与项目形态一致。电影、短片和单集剧确定目标时长，连续剧和
  竖屏短剧确定集数与单集时长，同时明确范围压缩策略和本次叙事终点；
- `adaptationDecisions` 使用稳定 ID，逐条记录 `preserve`、`compress`、
  `merge`、`omit`、`reorder`、`transform` 或 `invent`，并说明对象、理由和
  银幕意图；
- 除 `invent` 外，每条决策的 `sourceAnchors` 必须出现在已接受分析的
  `evidence` 中，不能引用未进入事实底座的原作内容；
- 原作分析存在抽样或未覆盖局限时，简报必须通过
  `acknowledgedSourceLimitations` 完整承接。

工具提案阶段和用户接受阶段都会执行同一套语义校验，防止通过手工接口保存的
不完整文档绕过工作流。原创项目没有原作事实底座，因此仍只要求创作目标本身。

结构阶段继续继承这条追踪链。`beat_sheet` 的每个节拍包含稳定 `id` 和连续
`order`；`episode_outline` 的每集包含稳定 `id` 和连续 `number`，且书架改编
项目的集数必须与简报 `formatPlan` 一致。两种结构都保存 `creativeBriefId` 和
`decisionCoverage`：

- 简报中的每条 `adaptationDecision` 必须恰好出现一次；
- 非 `omit` 决策至少映射到一个真实存在的结构单元；
- `omit` 决策使用空 `structureUnitIds`，但仍需写明删减如何执行；
- 不允许未知、重复、遗漏的决策 ID，也不允许引用不存在的节拍或分集 ID；
- 原创项目没有原作改编决策时，`decisionCoverage` 为空数组。

因此接受后的结构版本可以回答“这条原作改编决定最终落在了哪里”，并为下一步
把结构单元继续映射到场景表提供稳定标识。

场景阶段通过 `scene.structureUnitIds` 继续继承结构。场景表保存当前
`structureId`，每个场景仍使用稳定 `id` 和连续 `order`，并满足：

- 每个场景至少映射一个当前结构版本中的真实单元；
- 每个节拍或分集至少被一个场景承接，不能在拆场时静默丢失；
- 电影、短片和单集剧允许一个场景同时服务多个节拍；
- 连续剧和竖屏短剧的每个场景必须且只能归属一个分集，`episodeNumber`
  必须与所映射分集的 `number` 一致；
- 缺少稳定单元 ID 的旧版结构必须先生成新版，不能以空映射绕过。

接受场景表后，可由
`sourceAnalysis.evidence -> adaptationDecisions -> decisionCoverage ->
structureUnitIds -> scenes`
反向或正向查询一条创作决定的完整去向。

正文阶段不再只用 `completedSceneIds` 表示“写过了”。每次
`proposeSceneDraft` 仍只追加场景表中的下一场，同时提交当前场的 `execution`：

- `objectiveResult`：场景目标在正文中如何达成或失败；
- `conflictResult`：阻力如何具体升级、转移或解决；
- `turnResult`：场景结尾发生了什么状态改变；
- `continuityState`：人物、信息、物件和行动目标如何进入下一场；
- `unresolvedNotes`：仍需后续场景或审阅处理的事项。

服务端将它标准化为累计 `sceneExecutions`，复制场景的 `structureUnitIds` 并绑定
当前 `sceneListId`。新滚动版本必须完整继承此前正文与执行记录，每次恰好新增
一个场景；不能改写上一场的执行结论，也不能一次跳写多场。进入修订阶段后，
执行记录不再只是原样继承：所有被审阅问题影响的场景都必须重新评估，并把
更新后的结论与问题解决证据一起写入新版本。

它们只发出 `screenplay.document_proposal` 领域事件，不写数据库。提案保存
后仍是草稿；只有用户点击接受，后端才会在同一事务中替代同类型旧接受版本，
书架改编的创作简报必须继承已接受的 `source_analysis`。接受创作简报后推进到
`structure`，接受节拍表或分集结构后推进到 `scenes`。
结构提案必须引用已接受的创作简报，且电影/短片/单集剧与连续剧/竖屏短剧
使用不同的结构工具。

Scene plan / Draft 阶段新增两个 `PROPOSE` 工具：

- `proposeSceneList`
- `proposeSceneDraft`

场景表必须继承已接受的节拍表或分集结构，并为每个场景提供稳定 id。接受
场景表后进入 `draft`。逐场正文采用“滚动整稿”：每个新版本的
`contentText` 都包含截至当前场的完整剧本，并继承已接受的场景表与上一版
正文；已完成场景不能从 `completedSceneIds` 中消失，也不能跳过场景表顺序。
只有全部场景完成且 `isComplete=true` 的版本被用户接受后，项目才进入
`review`。

项目页可将当前已接受整稿导出为 `.fountain`、`.pdf`、`.md` 或 `.txt`；导出使用
桌面端原生保存对话框，不会改变项目文档或来源记录。

Review 阶段新增两个 `PROPOSE` 工具：

- `proposeScreenplayReview`
- `proposeScreenplayRevision`

审阅报告把问题结构化为稳定 id、严重程度、类别、关联场景、关联的
`executionFields`、修改建议与 `acceptanceCriteria`。问题必须至少定位一个
已有执行记录的场景，不能只给出脱离正文位置的泛泛意见。

修订工具只有在用户接受审阅报告后才可使用，并必须同时继承当前完整稿和
已接受审阅报告。修订输入必须：

- 用 `issueResolutions` 逐项回应报告中的全部问题，声明 `resolved` 或
  `partially_resolved`，并提供可在新正文中核对的 `resolutionEvidence`；
- 用 `executionUpdates` 完整重评所有受影响场景的目标、冲突、转折、
  连续性和未解决事项；
- 提交修订后的完整 Fountain 正文，而不是局部补丁。

服务端将重评记录按原场景顺序合并为新的 `sceneExecutions`，同时写入
`reassessedSceneIds`、`resolvedIssueIds` 和 `partiallyResolvedIssueIds`。
用户接受修订稿后仍回到审阅阶段，由新一轮审阅验证验收标准是否兑现；旧审阅
报告随之失效，不能被下一版修订重复使用。

复审不是重新生成一份与历史无关的报告。当前修订稿通过 `reviewId` 和
`revisionOf` 追溯上一轮审阅，复审必须为上一轮每个问题提交
`verificationResults`：

- `verified`：验收标准已经在新正文和更新后的执行记录中兑现；
- `still_open`：修改存在，但仍未达到验收标准；
- `regressed`：修订引入了同一问题的新退化或副作用。

每项核验都必须提供 `verificationEvidence`。`still_open` 与 `regressed`
必须以原问题 id 继续出现在当前 `issues` 中，供下一轮修订处理；已经
`verified` 的问题不能继续伪装成待修订项。只有历史问题全部验证通过、当前也
没有发现新问题时，复审才能给出 `ready` 并将项目推进为完成。

项目进入 `completed` 不是单独修改一个阶段字段。接受最终 `ready` 复审的同一
数据库事务会生成 `delivery_manifest`，固化以下内容：

- 项目标题、形态、创作方式、故事前提与原作范围快照；
- 原作范围分析（改编项目）、创作简报、结构、场景表、最终剧本和最终复审的
  权威文档 ID 与版本；
- 每份文档的稳定内容摘要、来源凭据数量，以及整套交付的 `packageDigest`；
- 最终场景数、场景执行记录数、历史问题验证数量和零开放问题质量门。

交付清单由服务端根据已接受版本生成，不由 Agent 自报。若任一上游版本缺失、
版本链断裂、最终审阅不对应当前剧本或仍有未通过核验，事务会整体失败，项目
不会进入完成状态。完成后的项目读取接口直接返回这份快照；桌面端可单独导出
JSON 清单，同时继续导出 Fountain、PDF、Markdown 或纯文本最终剧本。

标准 PDF 导出由后端使用 ReportLab 生成 US Letter 剧本排版：独立标题页、
1.5 英寸左页边距、场景标题/人物提示/对白分区和正文页码。中文 PDF 会查找
系统可嵌入字体并写入文件，避免依赖阅读器的 CJK 字体映射；缺少可靠字体时
会明确报错，不生成缺字 PDF。桌面端仍只负责原生保存对话框。

原作读取结果会以 `screenplay_source_refs` 记录在 Agent Run 下，包括来源
类型、对象 ID、内容摘要和 revision digest。用户保存候选文档时，当前 Run
的来源记录会与新文档原子关联；剧本项目页会显示每个版本的来源数量。

项目文档列表现在是版本工作台：草稿可在预览窗中直接编辑，已接受和已替代
版本保持只读；同类型文档可与前一版本逐行比较。恢复历史版本不会覆盖原记录，
而是复制正文、结构化内容和来源凭据，创建一个以被恢复版本为直接上游的新
草稿。项目归档是可逆的只读状态：归档后仍可查看、比较和导出，但后端会拒绝
新增、修改、接受、删除及版本恢复；恢复项目后才重新开放这些写操作。

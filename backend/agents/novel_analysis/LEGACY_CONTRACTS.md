# 小说分析 Agent：Legacy 合同盘点

> 阶段：历史只读盘点
> 日期：2026-09-12
> 状态：仅作迁移证据；legacy 可执行实现与冻结机制均已退役

下文“当前”仅指 2026-09-12 盘点快照，所列入口与工具不得视为现行实现。

本文记录当前小说分析 Agent 的外部合同、持久化事实和调用方依赖。它不是
legacy 实现的设计背书。文中明确标为“已知缺陷”的行为不得被 characterization
test 固化为 replacement 的正确行为；标为“待决策”的项目在编码前必须确定产品
语义和兼容策略。

## 1. 边界与术语

当前实现的正式分析身份层级是：

```text
来源版本 sourceRevisionId
  └── 对话 sessionId
      └── Root Agent Run (binding namespace: novel_source_analysis)
          └── Durable Task (namespace: purrtypos.novel_analysis)
              └── Unit (taskId, unitId, attempt)
                  └── PurrA operationId = taskId:unitId:attempt
```

- Root Run 承担规划、公开阶段播报和最终回复。
- recipe Unit 不创建独立 Agent Run，而是在拥有该 Task 的 Root/continuation Run 内
  通过 PurrA Operation 执行。
- `RunBinding(namespace="novel_source_analysis.unit")` 仍由 Unit 请求构造，但
  `run_durable_operation()` 不会把它持久化为新的 Run binding；单元级权威身份是
  operationId，不是 synthetic child Run。
- legacy reader 仍兼容历史 Unit Run，因此查询和 SSE 中保留
  `novel_source_analysis.unit` 以及 `runHistory` 读取分支。

Replacement 不得 import legacy profile、service、executor、prompt、tool catalog 或
projector。旧数据兼容必须通过名称明确的只读 adapter。

## 2. HTTP 入口与响应

路由定义在 `backend/routers/novel_sources.py`，统一挂载 `/api`。

| 方法与路径 | 请求要点 | 当前响应/效果 |
| --- | --- | --- |
| `POST /novel-source-revisions/{revisionId}/analyses` | Header `Idempotency-Key`；`conversationId?`、`runtime`、`prompt` | 202；绑定 session command，异步启动正式分析，返回 accepted、revision、command、sectionCount、dispatchActive |
| `POST /novel-source-revisions/{revisionId}/analysis-follow-ups` | Header `Idempotency-Key`；`artifactId?`、`replaceRunId?`、conversation、runtime、prompt | 202；普通追问、显式继续，或 replace-turn 分支 |
| `GET /novel-source-revisions/{revisionId}/analysis-runs` | revision scope | Root/Task/Unit/plan/Artifact 的 UI read projection |
| `GET /novel-source-revisions/{revisionId}/analysis-events?after&limit` | `after >= 0`，`1 <= limit <= 500` | revision-scoped SSE，page kind 为 `analysis_events` |
| `POST /novel-analysis-tasks/{taskId}/pause` | `expectedTaskRevision?` | 同步控制 receipt |
| `POST /novel-analysis-tasks/{taskId}/resume` | Header `Idempotency-Key`；runtime、`retryFailed` | 202；异步 continuation acceptance receipt |
| `POST /novel-analysis-tasks/{taskId}/cancel` | 空 body | 同步控制 receipt；先取消 Task，再请求终止仍 running 的关联 Run |
| `GET /novel-analysis-artifacts/{artifactId}` | Artifact id | 仅返回 finalized Artifact |
| `POST /novel-analysis-artifacts/{artifactId}/review` | Header `Idempotency-Key`；facts、craftCards、techniqueResult? | 创建新的 review Artifact，不原地改 candidate |
| `POST /novel-analysis-artifacts/{artifactId}/publish` | 空 body | 校验后写入 immutable published analysis；同内容幂等 |
| `GET /novel-source-revisions/{revisionId}/analyses` | revision scope | 当前发布版本列表投影；现 UI 只突出最新版本 |
| `GET /novel-source-analyses/{analysisId}` | analysis id | 已发布分析详情 |
| `GET/POST/PATCH /novel-source-revisions/{revisionId}/conversations...` | 创建、列出、改名、关闭/重开 | 管理 revision-scoped 分析对话 |

请求 Schema 定义在 `backend/schemas/novel_sources.py`：

- 正式分析和追问 runtime 必须包含 `modelConfigId`；请求还携带 apiKey、Provider、
  model options、context window。
- prompt 长度为 1–20,000。
- review 的后端 Schema 当前只接受 `facts`、`craftCards` 和可选
  `techniqueResult`。
- Task 控制 receipt 的前端合同包括 conversation/workflow 双状态、pause kind、
  reason code、resumable、automatic recovery due time、Task revision 和 Unit 计数。

### 已知缺陷（不得继承）

- `src/services/backendApi.ts` 和 TypeScript 类型会发送 `storyOverview`，但
  `ReviewNovelAnalysisRequest` 没有该字段；Pydantic 会丢弃它。review service 因此
  只能回退到源 Artifact 的 overview，任何客户端都无法通过当前 review HTTP 合同替换
  overview。
- `_execute()` 与 `_execute_follow_up()` 对普通 `Exception` 直接返回，缺少明确日志和
  诊断关联。持久化 Run 可能已有失败事实，但编程错误和启动路径错误会被静默隐藏。
- 进程内 `_ACTIVE_ANALYSES` 以整个 source revision 为 key，会阻止同一来源版本不同
  session 的同时启动；数据库唯一索引却已按 session 限制 active Task。两者粒度不一致。

### 待决策

- Replacement API 是否保持当前路径和 envelope 原样，只在内部按 implementation
  路由；建议保持，减少前端迁移面。
- review 是否允许编辑 story overview。若允许，必须把它加入后端请求 Schema；若不
  允许，应从客户端请求和编辑 UI 中删除该假能力。
- “一份来源版本只允许一个进行中分析”还是“每个 session 一个进行中分析”必须统一，
  不能继续让进程锁和数据库锁表达不同规则。

## 3. 会话、Turn 与来源绑定

### 当前事实合同

- 对话由 `novel_analysis_sessions` 保存，必须属于一个 `revision_id`；关闭的 session
  不接受新 command。
- `novel_analysis_session_commands` 将全局唯一 `command_id` 绑定到 session 和
  revision。未指定 session 时使用 `legacy:{revisionId}`。
- Root Run binding：
  - namespace：`novel_source_analysis`
  - aggregateId：source revision id
  - commandId：请求的 idempotency key
  - attributes：正式分析至少带 `taskIdempotencyKey`；追问带
    `interactionKind=follow_up` 和 `analysisArtifactRef`。
- 正式分析在准备请求时读取 revision 的全部 section，冻结 section id 顺序，并按
  context window 计算 `inputTokenBudget` 和连续 segment 范围。
- `NovelAnalysisSourceReader` 对每次读取都校验 revision、允许的 section id、section
  ordinal 和 segment 边界；精确 evidence 会校验引文在绑定范围内唯一存在。
- 来源正文标记为 untrusted context，只能作为证据，不能获得指令权限。
- replace-turn 不是物理删除。目标 Run 及其后缀写入
  `novel_analysis_superseded_runs`，从活跃历史、模型历史和 SSE 投影排除；原 Run 和
  Artifact 仍保留用于诊断。
- 追问可以绑定当前 Artifact；service 会验证 Artifact 的 sourceRevisionId。无
  Artifact 时仍可通过只读来源工具回答。
- 只有完全匹配“继续/继续分析/继续执行/接着分析/恢复分析/continue/resume”的追问
  会尝试恢复当前 session 最新 paused/failed Task。

### 已知缺陷（不得继承）

- Root 和 follow-up 都传入一个永不由 service 置位的 `asyncio.Event()`。当前真实取消
  依赖持久化 Task/Run control plane；这个参数表达了不存在的第二套本地取消权威。
- 正式分析 `NovelAnalysisDomainContext.command_id` 使用原始 task idempotency key，
  continuation Root Run binding 使用新的 run command。两种 command 的用途需要在
  replacement 命名中显式区分，不能继续依赖字段上下文猜测。

### 待决策

- 定义两个独立字段：`taskCommandId`（Task 幂等身份）和 `turnCommandId`（本次 Root
  / continuation Turn 身份）。
- 明确断开 SSE 只 detach，绝不取消业务 Run；取消只由持久 control plane 生效。
- 明确 follow-up 读 Artifact 是“当前 Run 的产物”还是“当前 session 最近选中的产物”。
  目前由前端从 sessionRuns 中自行选第一份有 Artifact 的 Run。

## 4. Recipe、Unit 与 Attempt

### 当前事实合同

`compile_novel_analysis_recipe()` 生成 `kind=novel_source_analysis`、
`recipeVersion=7`、`analysisSchemaVersion=3` 的 host recipe。模型 Planner 只能提供公开
语义步骤；不能扩大来源范围、增加工具步骤或改变 host recipe。

Planner 必须返回：

- `PlanningKind.PLANNED`；
- 有 TaskSpec，`operation=analyze` 且 target 为空；
- steps 只能是 MODEL executor 的 analyze/review，无 capability names。

当前 recipe 图：

```text
extract_section × N
  -> normalize_entities（多 segment 时为二叉分层归并）
  -> validate_evidence
  -> distill_skill
  -> coverage_report
  -> build_review_artifact
```

- `maxParallelism = min(4, segmentCount || sectionCount)`。
- `extract_section`、`normalize_entities`、`distill_skill` 的 `maxAttempts=2`；其余为 1。
- 单个普通模型 Unit 最多 3 model rounds；distill 最多 6；Root Planner 最多 4 次尝试。
- PurrA Operation identity 为 `taskId:unitId:attempt`，并注入
  `operationBinding={taskId, unitId, unitAttempt}`。
- Unit 的工具/模型执行仍发生在 owning Root/continuation Run 中，Unit 表的 `run_id`
  不代表一个新建的独立 Run。
- Task metadata 保存冻结 source revision、section ids、segments、prompt、analysis plan、
  runtime binding、recipe digest、预算和恢复计数。
- 已完成 Unit 的 `output_ref`、artifact digest、validation receipt 在恢复时复用。

### `storyOverview` 核对结论

当前 recipeVersion 7 **不会生成故事概览**：

- recipe 中没有 `aggregate_story` Unit；
- extract Unit 不设置 `includeStoryOverview`；
- normalize 只接收 facts/craftCards projection；
- `build_review_artifact` 只在上游已有 `storyOverview` 时透传；
- 当前集成测试明确断言新完成的 Artifact 没有 `storyOverview`。

legacy executor、tool schema、follow-up projection、review/publish validator 和前端仍支持
可选 storyOverview；这是历史兼容/残留能力。缺失 overview 不会在 review 的 fallback
处触发 `KeyError`，因为 service 使用 `source.get()` 检查；因此“review 必然 500”不是
当前事实。但用户提交的 overview 被请求 Schema 丢弃是确定的合同错误。

### 已知缺陷（不得继承）

- executor 仍保留 `aggregate_story` 和 `includeStoryOverview` 分支，但当前 recipe 永远不
  创建这些输入，是不可达的 production 分支和误导性的测试表面。
- Planner steps 通过 phase 映射附着到 host Units。它们只用于公开展示，不是 recipe
  权威；replacement 不能从 UI Planner 步骤反推恢复依赖。
- Unit Artifact owner ref 为 `taskId:unitId`，没有 attempt。重试若产生不同 payload，
  `NovelAnalysisArtifactStore` 会报 content conflict，而不是保存独立 attempt 结果。

### 待决策

- 故事概览必须明确为：必需正式产物、可选产物、或彻底移除。若保留，建议设独立
  overview Unit，并定义它依赖验证后的 facts，而不是隐藏在 extract metadata 中。
- 新 recipeVersion 必须是 replacement 自己的版本，不能复用 legacy recipeVersion 7。
- attempt 产物建议以 durable operationId 为 owner identity；另设 Unit winner/finalized
  指针，不能覆盖或删除 effect-state 未知的旧 attempt。
- Planner 是用户可见阶段计划还是只负责 admission。无论选择哪一种，都不能改变 host
  recipe 的冻结范围和恢复合同。

## 5. 工具、输入投影与模型输出

### 当前工具集合

正式 Root 分析关闭 tools；Planner 只规划。每个模型 Unit 开启 tools，并要求至少一次
成功的提交工具调用。follow-up 使用 reactive 模式和只读工具。

| 交互/Unit | 实际 enabled 工具 |
| --- | --- |
| 正式 Root | 无 |
| 普通 extract / normalize Unit | 仅 `submitNovelAnalysisResult` |
| legacy aggregate Unit | 仅 `submitAnalysisOverview` |
| distill_skill Unit | `getTechniqueDraft`、`readTechniqueDraftFile`、`applyTechniqueDraftChanges`、`submitWritingTechnique`、`listAnalysisObservations`、`readAnalysisObservations`、`listAnalysisEvidence`、`readAnalysisEvidence`、`readTechniqueSource` |
| follow-up，无 Artifact | `listAnalysisSourceSections`、`readAnalysisSourceSection`、`listAnalysisSavedResults`、`readAnalysisSavedResult` |
| follow-up，有 Artifact | 上述工具加 `readAnalysisTechniqueFile` |

catalog 还注册了 `findAnalysisSourceEvidence`、`appendAnalysisFacts` 和
`appendAnalysisObservations`，但当前 enabled policy 不向普通 Unit 或 distill Unit 开放
它们；它们不能被视为可用能力。

普通分析提交 Schema：

- 必须包含 `facts[]`、`craftCards[]`；每条必须有至少一条 evidence；
- facts 包含 factKind、subjectKey、predicate、value、lifecycleStatus?、claimNature?；
- craftCards 包含 cardKind、title、bodyMarkdown、mergedObservationIds?；
- evidence 可使用短 `sourceSpanId`、`evidenceId`、逐字 excerpt，或允许的 chapter
  reference；
- storyOverview 在工具 Schema 中可选，但 recipeVersion 7 不要求或生成它；
- `SubmittedAnalysisResultValidator` 只有看到提交工具对应的已提交 Artifact receipt 才
  接受 Unit 的最终模型响应。

输入投影由 `project_unit_input()` 完成：

- source text 会转为带短 source span handle 的证据投影；
- observations/craftCards 会从直接上下文删除，只留下 `observationCount` 和一段提示，
  声称可通过 `listAnalysisObservations` / `readAnalysisObservations` 读取；
- evidence 可投影为短 evidence id，并声称可以 list/read；
-完整 durable payload 仍存在 operation context 中，宿主提交 handler 可用于恢复引用、
  保留未合并 observation 和校验证据范围。

### normalize observation access 核对结论

这是确定的能力合同断裂：

1. normalize 输入的 `sectionCandidates` 原本包含 craftCards；
2. `project_unit_input()` 删除全部 craftCards，只留下 observationCount 和工具访问说明；
3. `_NORMALIZE_INSTRUCTION` 要求模型归并等价 observation 并填写
   `mergedObservationIds`；
4. enabled policy 对普通 normalize Unit 只开放 `submitNovelAnalysisResult`，不开放任何
   observation list/read 工具；
5. 因此模型既看不到 observation 正文和 ID，也不能调用提示中声明的读取工具。

宿主在提交时会用完整 durable input 保留未合并 observation，因此不一定丢数据；但
模型无法执行语义归并，实际能力退化为“事实名称归一 + 原 observation 原样保留”。

### 其他已知缺陷（不得继承）

- 输入投影可能提示 `listAnalysisEvidence` / `readAnalysisEvidence`，但普通 Unit 同样没有
  这些授权。
- evidence 校验/修复反馈可能要求调用 `findAnalysisSourceEvidence`，而普通 extract Unit
  不具备该工具。sourceSpan 短编号虽已直接注入，但坏引用的修复提示和授权不一致。
- 截断重试提示要求“分批保存观察或文件，读取目录后补读”，普通 extract Unit 实际不
  能调用 append/list/read 工具。
- tool catalog 的“已注册”与“当前 request enabled”容易被混为一谈。replacement 的
  capability contract 必须在发起模型调用前联合校验 projected input、prompt-required
  capabilities、enabled schema、allowed effects 和 expected final output。

### 待决策

- normalize 应选择一种有界方案：直接注入可控数量的 observation id/body/evidence，或
  开放有严格分页和调用预算的 observation 读工具。不能同时删除正文又禁止读取。
- extract 是否需要 append/checkpoint 工具。如果要求一次原子提交，应清除所有分批保存
  提示；如果要支持大输出，则必须把分页 Artifact 纳入正式 effect/recovery 合同。
- 写作技法 distill 目前复用了 writing technique service 和 legacy generation tools。
  replacement 是继续通过新 adapter 使用既有技法存储，还是建立新的工具合同后兼容写入，
  必须在 A2/A3 前决定；新模块不能 import legacy tool catalog。

## 6. Artifact、Schema、Review 与 Publish

### 当前 Artifact 种类

namespace 均为 `purrtypos.novel_analysis`，引用格式为
`novel-analysis-artifact://{artifactId}`。

| kind | owner ref | 内容/用途 |
| --- | --- | --- |
| `novel_analysis_model_result` | `analysis_operation:{operationId}`，owner 为 owning Run | Unit 提交工具的模型结果；用于 operation 完成后读取 |
| `novel_source_analysis_unit` | `long_task_unit:{taskId}:{unitId}`，owner 为 source revision | host 校验/组合后的 Unit 输出 |
| `analysis_fact_page` | `observation_page:{operationId}:{toolCallId}` | legacy 分批 facts checkpoint |
| `analysis_observation_page` | 同上 | legacy 分批 observations checkpoint |
| `novel_source_analysis_candidate` | final review Unit 的 long_task_unit owner | 待用户审核的完整候选 |
| `novel_source_analysis_review` | `analysis_review_command:{commandId}` | 用户审核后的新 Artifact |

`NovelAnalysisArtifactStore` 是单 item、单 batch、立即 finalized 的封装：

- owner tuple `(namespace, ownerId, kind, ownerRefKind, ownerRefId)` 唯一；
- 同 owner 已有 batch 时只接受完全相同 digest；
- `require()` 只读取 finalized、且恰有一个 batch/一个 item 的 Artifact；
- Artifact metadata 保留 semanticKey、taskId、unitId 等；
- 通用 Artifact table 的 `schema_version` 仍默认为 1，业务 payload 另带
  `analysisSchemaVersion=3`。

最终 candidate/review payload 当前包括：

- `analysisSchemaVersion`、`sourceRevisionId`、`sectionIds`；
- `facts`、`craftCards`、可选 `storyOverview`；
- `techniqueResult`、`generationPrompt`；
- `coverage`、`conflicts`、`reviewStatus`。

Review：

- 只接受 candidate/review kind 和 schema version 3；
- 重新校验全部事实、cards、可选 overview 的 evidence；
- 验证 technique candidate 确实是同 source revision 所有的 sealed version；
- 以 review command id 创建新 Artifact，原 candidate 不变。

Publish：

- 有 taskId 的 Artifact 只在 Task completed 后可发布；
- 再次校验 source scope、证据和 technique version；
- 以 canonical payload digest 保证相同内容幂等；
- 在一个 cancellation-linearizable transaction 中写
  `novel_source_analyses`、facts、craft cards、evidence 和 technique association；
- 发布结果按 revision 递增 versionNo，summary_json 保存 Artifact id、section ids、
  technique、coverage、conflicts 和可选 overview。

### 已知缺陷（不得继承）

- host Unit Artifact owner 不含 attempt，无法表达一次 Unit 的多个实际执行结果。
- 阶段 Artifact 与 Unit checkpoint 不是同一事务；见下一节的结算顺序缺陷。
- storyOverview 在数据库没有独立规范表，只位于 summary_json；是否允许编辑与前后端
  Schema 又不一致。
- review/publish 的业务 schema version 与通用 Artifact schema_version 分离但没有统一
  contract registry，reader 必须靠 payload 字段判断。

### 待决策

- Replacement Artifact 必须持久化 `implementationId`、implementationVersion、
  artifactSchemaVersion、recipeVersion 和 operation identity。
- 明确 OPEN / WRITTEN / FINALIZED / SETTLEMENT_UNKNOWN 的恢复规则。不得为了重试简单
  删除旧 attempt Artifact。
- 选择 overview 的规范存储。如果它是正式事实产物，应有可验证的 version/schema，不能
  只依赖 summary_json 的松散字段。
- 明确 review 是“提交完整 replacement”还是 patch。当前表现为完整 facts/cards
  replacement、technique 可替换、overview 因 API 缺陷实际只读。

## 7. 阶段播报与 Artifact 结算顺序

### 当前事实顺序

模型 Unit 的成功路径是：

```text
模型调用并通过提交工具写 novel_analysis_model_result
  -> executor 读取并验证结果
  -> 写入并 finalize host novel_source_analysis_unit Artifact
  -> 构造 LongTaskUnitResult（尚未 checkpoint completed）
  -> Root 再调用真实 Provider 生成公开阶段播报
  -> report_operation_result 成功返回
  -> PurrA dispatcher 才能把 Unit checkpoint 为 completed
```

`OperationStageOutput` 在 Root 非 running 时抛 `CancelledError`；Provider 中断、空输出、
非 stop finish、工具调用或 delivery reconciliation 也可能抛异常。PurrA 对
`CancelledError` 有独立 interrupted/canceled 处理，不会简单走普通 BUSINESS_INVARIANT
分类。因此“阶段播报取消必然立即把 Unit 永久标记失败”不是准确事实。

但核心一致性问题确定存在：业务 Artifact 已 finalized，而 durable Unit 尚未 completed。
如果阶段播报失败或取消，后续恢复可能重新执行模型 Unit；因为 host Artifact owner 不含
attempt，新结果不同就会触发 `novel analysis Artifact content conflicts`。展示副作用因此
可以反向破坏已提交业务结果。

### 已知缺陷（不得继承）

- 公开展示是业务 Unit 成功的硬前置，违反“展示失败不能撤销已提交结果”。
- delivery state 以 `taskId:unitId` 为 key，不含 attempt；started/aborted 需要人工
  reconciliation，恢复策略没有与 Unit effect state 形成统一合同。
- `OperationStageOutput._locks` 按 run id 增长且不清理。
- 阶段播报再次调用真实 Provider，会增加成本、延迟和新的故障面；当前测试只验证模拟
  Provider 的串行公开输出和隐私过滤。

### Replacement 必须满足

- 业务 Unit 在 Artifact/effect 已确定提交后可独立 checkpoint completed。
- stage presentation 通过 outbox/可重放 projection 或非致命异步操作交付；失败只能影响
  展示状态，不能触发业务 Unit 再执行。
- delivery identity 和 attempt/effect identity 明确；同一成功业务结果不会重复展示。

## 8. 事件、SSE 与前端投影

### 当前事实合同

SSE page：

```json
{
  "kind": "analysis_events",
  "chunks": [{"cursor": 1, "runId": "...", "chunk": {}, "createdAt": "..."}],
  "runs": [],
  "publishedId": null,
  "projectionVersion": "...",
  "nextCursor": 1,
  "hasMore": false
}
```

- revision 存在性在打开 SSE 前校验，删除的来源返回 404，不进入无限重连。
- canonical events 来自 output journal；query 以 source revision 和 namespace 读取。
- cursor 必须跨过被过滤事件，避免订阅停在不可见事件上。
- projectionVersion 由 Run/Task/Unit/已发布分析状态计算；只有版本变化时重发 `runs` 和
  `publishedId`。
- legacy synthetic Unit、retry history、superseded Run 会从公开投影隐藏。
- Tree Child 只允许 PUBLIC + OPERATION 事件进入来源订阅；私有 body/reasoning 不公开。
- 前端将 Root 和 relatedRuns 的 canonical chunks 合并为一个 conversation turn；Root
  角色为 root，其他为 unit。
- `conversationStatus` 描述聊天输出是否 finalized；`workflowStatus` 描述 Task 是否
  queued/running/paused/completed/failed/canceled。前端不得用 Root done 推断 Task 完成。
- 前端通过 Root + related snapshots 补齐历史，再让 SSE 增量接管；页面首批 SSE 不能
  被当成完整历史。
- 公开 final response 来自 Root 最终 Provider 输出，不从 Unit metadata 拼接。

### 已知缺陷（不得继承）

- 当前 query 为历史 synthetic Unit 保留了复杂成员发现；replacement 若使用 Operation
  identity，应直接投影 Operation provenance，不再依赖不存在的 child Run membership。
- `relatedRuns` 在当前 operation-in-root 路径通常为空，但前端仍把它当 Unit 归属来源。
- SSE 产品可见性和历史兼容逻辑耦合在一个 query 中，增加删除 legacy executor 时的
  风险。

### 待决策

- 保持现有 SSE page envelope 以兼容前端，内部新增 implementation/version 和 operation
  projection；或者新建 v2 endpoint 后双 reader 迁移。禁止新旧双写 output journal。
- 明确 replacement stage 的 canonical channel、visibility、delivery state 和去重 key。
- Legacy 历史 reader 在 executor 删除后保留多久、由哪个 adapter 所有。

## 9. 取消、暂停与恢复

### 当前事实合同

- 用户 pause 调用 LongTask repository，并支持 expected Task revision 的乐观并发校验；
  reason code 为 `user_paused_novel_analysis`。
- cancel 先使 Task terminal/cancel requested，再枚举 Task 的 Root、continuation、legacy
  Unit Run 和 runHistory，对仍 running 的 Run 调用共享 cancellation service。
- User pause、system pause、budget pause 在 read projection 中有不同 pauseKind 和
  resumable/automatic recovery 语义。
- 可恢复 Provider/传输/活性超时可在两次 Unit attempt 后把 Task 暂停，已完成 Unit 和
  Artifact 保留。
- partial completion 是 Root 的真实阶段性最终回复，但不能冒充完整可审核/可发布成果。
- resume 不重新规划。它从原 Root 恢复 frozen execution plan，重新编译同版本 recipe，
  校验 Unit id 集合一致，复用 completed outputs，并只重置允许继续的 Unit。
- 恢复失败 Task 需要 `retryFailed=true`；恢复 paused Task 需要 false。
- budget resume 只按仍未完成模型 Unit 增加一轮受限 invocation budget，并留下授权
  metadata。
- automatic recovery 只扫描 system-paused 且已到期 Task；必须能用保存的
  `modelConfigId` 重新解析完全相同的 Provider/model/endpoint/context/capability identity。
  Task 不保存 API key。配置不存在、身份变化或 key 不匹配时保持暂停。
- 后端启动时先尝试 recover_due，再启动 2 秒轮询 monitor；关闭时停止并等待 monitor。

### 已知缺陷（不得继承）

- resume 通过直接 SQL 修改 Unit dependencies、attempt capacity 和 Task metadata，产品
  service 承担了部分框架状态机责任。
- 后台进程 registry 与持久 Task 状态是两套并发门禁，且 scope 不一致。
- stage-after-Artifact 顺序使取消与恢复可能撞到已 finalized 的无 attempt Artifact。
- application service 吞掉后台异常，调用方只能等待 read projection 变化，缺少明确的
  dispatch failure receipt/日志关联。

### 待决策

- Replacement 的恢复必须按 Run 上不可变 implementation identity 路由；旧 Task 永远
  回到 legacy reader/executor，不能被 replacement recipe 接管。
- 自动恢复是否仍由产品 monitor 承担，还是迁入 shared lifecycle；无论位置如何，模型
  身份校验和不保存 secret 的合同必须保留。
- 明确每类 failure category、effect state、attempt budget 和 Task terminal/pause 语义；
  不能只按异常类名决定重试。

## 10. 数据库与兼容读取范围

Replacement 需要兼容读取但不得直接复用 legacy 执行器的表：

- `ai_agent_runs`、`ai_agent_run_events`、canonical output journal；
- `ai_agent_long_tasks`、`ai_agent_long_task_runs`、`ai_agent_long_task_units`、
  `ai_agent_long_task_usage`、`ai_agent_long_task_events`；
- `ai_agent_artifacts`、`ai_agent_artifact_batches`、`ai_agent_artifact_claims`；
- `novel_analysis_sessions`、`novel_analysis_session_commands`、
  `novel_analysis_superseded_runs`；
- `novel_source_analyses`、`novel_source_analysis_facts`、
  `novel_source_analysis_evidence`、`novel_source_craft_cards`；
- source revision/section 表以及 sealed writing-technique 版本表。

新字段和表只能 additive migration。新旧实现不能占用同一个 Artifact owner tuple；旧
Run 字段缺失时必须显式投影为 legacy，不能默认成 replacement。

## 11. 当前调用方

### 后端

- `backend/routers/novel_sources.py`：全部 HTTP/SSE 入口。
- `backend/application/composition_factory.py`：注册 profile id `novel_analysis`。
- `backend/main.py`：安装 composition，启动 automatic recovery 和 reliability baseline
  monitor，挂载 router。
- 小说续写/知识链路读取 published analysis facts、evidence、story overview 和 technique
  association；删除来源时也会清理/阻止仍被引用的分析数据。
- 共享 Run 查询、取消、orphan recovery、output journal、Artifact lifecycle 和 Provider
  health repository 是运行依赖，不属于产品领域合同本身。

### 前端/Electron Web UI

- `src/services/backendApi.ts` 与 `src/services/index.ts`：API/SSE client。
- `src/NovelSourcesPage/index.tsx`：来源选择、正式分析、追问、replace-turn、暂停/恢复/
  取消、Artifact review+publish、证据定位和结果编辑。
- `src/NovelSourcesPage/analysisConversation.ts`：历史 snapshot hydration 和增量 event
  replay。
- `src/NovelSourcesPage/analysisTaskPlan.ts`：将 planner step 与 durable Units 聚合成 UI
  计划。
- `src/NovelSourcesPage/AnalysisMaterials.tsx`、`WritingSkillReview.tsx`：facts、cards、
  technique review。
- `src/types.ts`：API、Artifact、Run、workflow 和 SSE 类型。
- 通用 `AgentConversation` 与 canonical output replay 组件消费公开事件。

## 12. 现有测试证据

以下是 characterization 的主要现有覆盖，不等于 replacement 已验收：

- `backend/tests/test_novel_analysis.py`
  - 连续、有界 segment 和 source scope；
  - host recipe、Planner 映射、并行 fan-in 和预算；
  - evidence locator、范围恢复、发布幂等和 review 新 Artifact；
  - pause/resume/cancel/restart、partial checkpoint、Provider retry；
  - read projection 的双状态、Unit/plan/Artifact；
  - 当前 recipe 明确不含 aggregate_story/includeStoryOverview。
- `backend/tests/test_novel_analysis_conversation.py`
  - 模拟 Provider 下的 owner-Run operation 执行、工具提交、取消、系统暂停、公开阶段
    播报、最终回复和隐私过滤；
  - Unit scope 和 Artifact receipt；
  - distill 工具集合、overview 工具 Schema、follow-up 来源读取。
- `backend/tests/test_novel_analysis_progress.py`
  - 显式 resume intent、session-scoped saved results、continuation turn 分离。
- `backend/tests/test_novel_analysis_sessions.py`
  - session 隔离、legacy session、创建/改名/关闭/重开路由。
- `backend/tests/test_novel_analysis_turn_edit.py`
  - replace suffix、跨 session/运行中拒绝、历史保留、SSE cursor 前进。
- `backend/tests/test_agent_event_stream.py`
  - dynamic membership、Child body 隐藏、归档 retry history 隐藏、projection version。
- `backend/tests/test_operation_stage_output.py`
  - 模拟 Provider 下阶段输出串行、早到达、隐私、aborted/reconciliation 行为。
- 前端 `analysisConversation`、`analysisTaskPlan`、runtime selectors、timeline 和 composer
  tests 覆盖状态聚合、回放和交互约束。

### 现有测试没有证明的内容

- normalize 模型能够看到 observation 并实际完成 mergedObservationIds 归并；现有 fixture
  可以提交空 craftCards，再由宿主保留旧 observation，因此掩盖了能力退化。
- 用户编辑后的 storyOverview 会经过 HTTP Schema 到达 review service。
- stage Provider 在 Artifact 已提交后中断/取消，再恢复时不会重跑业务模型或发生 Artifact
  digest conflict。
- 多 session 同一 revision 的真实并发行为。
- 自动恢复在真实进程崩溃、Provider 限流和配置变更下的完整闭环。
- replacement implementation identity、recipe/tool/artifact version 路由；legacy 当前没有
  这些新字段。

## 13. 真实验收缺口

当前仓库存在历史真实 Provider 验收记录，但它早于当前 reliability、Operation 和
recipeVersion 7 接线，不能作为 replacement 或当前冻结基线的通过证据。最新可靠性计划
明确记录：真实 Provider 与 Electron 的 R7 尚未完成。

Replacement 切流前至少需要：

1. 使用隔离 source fixture 和新 Run，真实 Provider 完成 extract、normalize、evidence、
   distill、coverage、review Artifact 全链路。
2. 检查真实 tool choice：普通 Unit 只能选择合同内工具，normalize 能读取完整有界
   observations，错误 evidence 能在预算内修复。
3. 验证 overview 的最终产品决策及 HTTP review round-trip。
4. 在 stage 输出期间执行暂停、取消、断流和 Provider 中断，证明业务 Artifact 不重跑，
   且 UI 不把 partial 当完整成果。
5. 重启后端后恢复 paused Task，验证 implementation/recipe/tool/artifact 版本不漂移。
6. Electron 中验证会话历史、SSE cursor、计划状态、结果打开、证据定位、review/publish、
   replace-turn 和旧 Run 只读回放。
7. 验收启动的进程必须停止，并确认端口无监听；不得使用用户真实创作资料做破坏性写入。

## 14. Replacement 编码前的决策门

以下决定未落定前，不应开始 A1 recipe 实现：

1. storyOverview 是必需、可选还是删除；是否允许用户编辑。
2. normalize 采用直接有界输入还是分页只读工具。
3. extract 采用一次原子提交还是正式 checkpoint/append 协议。
4. 一个 revision 的 active Task 并发粒度是全局还是每 session。
5. stage presentation 是 persisted outbox、可重放 projection，还是非致命独立 Operation。
6. attempt Artifact winner 和 settlement-unknown 恢复规则。
7. replacement 与 sealed writing-technique storage 的兼容 adapter 边界。
8. SSE 保持当前 endpoint/envelope 还是提供 v2 reader；legacy 历史 reader 的保留期限。

无论选择如何，新合同都必须满足：一个 Run 永久绑定一种 implementation；新旧不双写；
工具能力在调用前可验证；展示失败不改变已提交业务结果；任何确定性测试都不能冒充真实
Provider 或 Electron 验收。

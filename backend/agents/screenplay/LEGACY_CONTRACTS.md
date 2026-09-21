# 剧本 Agent 旧实现合同盘点（已归档）

> 状态：历史迁移输入。旧执行源码已于 2026-09-14 删除，文中“当前”均指
> 2026-09-12 冻结快照，不代表现行实现，也不得作为可调用代码路径。
>
> 用途：解释 replacement 的来源和禁止继承的缺陷。现行权威实现只在
> `backend/agents/screenplay/`，产品路由在 `backend/routers/screenplay_*.py`；
> 下列已删除文件名仅作历史证据，不是兼容入口。

## 1. 边界和术语

- **Turn**：一条用户对话请求，产品权威行为记录在 `screenplay_agent_turns`。
- **Operation**：一个正式剧本业务动作，一条 Turn 至多一个，权威状态记录在 `screenplay_agent_operations`。普通问答没有 Operation。
- **Root Run**：PurrA Agent Run，binding namespace 为 `screenplay.conversation_turn`；负责理解意图、规划、LongTask 调度和最终回答。
- **LongTask**：PurrA 持久任务，保存 recipe、Unit DAG、attempt、重试、暂停与使用量。
- **Part / Unit**：Manifest 中的业务 Part 被编译为一个 LongTask Unit。当前执行身份是 `(taskId, unitId, attempt)`，其 PurrA `operationScopeId` 为 `taskId:unitId:attempt`。
- **Candidate Artifact**：正式 Revision 产生前的 Part 候选，存入通用 `ai_agent_artifacts` / batch / claim 表。
- **Revision**：产品域不可变交付物版本，最终由 Root terminal projector 组装并写入 `screenplay_revisions`。
- **Checkpoint**：业务里程碑处重新评估 Root plan 的持久化收据，不等同于每个 Part 完成事件。

权威代码入口：

- HTTP：`backend/routers/screenplay_conversations.py`
- wire schema：`backend/schemas/screenplay_agent.py`
- Turn/Root 编排：`backend/application/screenplay_agent_service.py`
- profile/admission/dispatcher/checkpoint observer：`backend/application/screenplay_agent_profile.py`
- recipe/Manifest：`backend/application/screenplay_manifest_compiler.py`
- Unit executor：`backend/application/screenplay_agent_task_executor.py`
- model/tool operation：`backend/application/screenplay_tool_calling.py`
- Root terminal projection：`backend/infrastructure/screenplay/agent_root_completion_projector.py`
- Candidate operation finalization：`backend/infrastructure/screenplay/candidate_completion_projector.py`
- 产品 Revision commit：`backend/infrastructure/persistence/sqlite_screenplay_operation_finalizer.py`

## 2. 当前事实合同

### 2.1 HTTP 入口和 wire contract

所有路径挂在 `/api/screenplay/v2`：

| 方法与路径 | 输入合同 | 主要输出/语义 |
| --- | --- | --- |
| `POST /projects/{projectId}/conversation/turns` | 必须有 `Idempotency-Key`；`sessionId >= 1`；非空 `content`（最多 20,000 字符）；runtime 必须有非空 API key 和 `options.model`；可带 typed `stageCommand` | `202`，先持久化 Turn，再进程内异步 dispatch |
| `GET /projects/{projectId}/conversation/snapshot?sessionId=` | session 必须属于项目且 scope 为 `screenplay` | Turn、Task、Operation 的完整恢复投影和 canonical cursor |
| `GET /projects/{projectId}/conversation/events?sessionId=&chunkAfter=&limit=` | `limit` 1–500 | canonical output journal 的 SSE 分页；返回 `agent_chunks` page |
| `POST /conversation/turns/{turnId}/cancel` | 必须有 `Idempotency-Key` | 幂等取消 receipt；不承诺请求返回时已经终态 |
| `POST /conversation/operations/{operationId}/resume` | 必须有 `Idempotency-Key`、`expectedOperationRevision` 和完整 runtime | `202`；仅 paused Operation 且来源 Root 已 canceled 时可恢复；CAS revision 防陈旧操作 |
| `DELETE /conversation/turns/{turnId}/and-after` | 无额外 body | 先 fence/cancel/等待相关执行，再删除该 Turn 及其后续对话运行态 |

`stageCommand` 是可选的强类型 UI 意图提示，只接受：

- action：`create | revise | review`
- targetRole：V2 deliverable role
- scope.kind：`current_stage | next_episodes | episodes | all_remaining`
- `count` 为 1–100；`episodeNumbers` 最多 100 项

Host 会用 `ScreenplayStageCommand.require_compatible(intent)` 校验模型 TaskSpec，stageCommand 不能替代模型意图，也不能改变产品事实。

公开 snapshot 的稳定形状由 `src/types.ts` 消费：

- Turn 状态：`queued | planning | running | paused | completed | failed | canceled`
- Operation 状态：`queued | running | paused | succeeded | failed | canceled`
- Task/Unit 包含总数、完成数、usage、result Revision、Unit status/attempt/outputRef/artifactDigest/validationReceipt/error。
- 普通回答 Turn 可以没有 Operation、Task 和 result Revision。

### 2.2 调用方

- `src/services/backendApi.ts` 是 Web/Electron 前端 API 适配入口。
- `src/ScreenplayAgentPage/conversationClient.ts` 封装 snapshot、submit、SSE、cancel、resume、truncate。
- `src/ScreenplayAgentPage/useScreenplayConversationController.ts`、`conversationState.ts` 和 `index.tsx` 消费恢复状态、流事件和 stage action。
- V2 workspace/revision/accept/review/finalize/PDF 接口仍在 `backend/routers/screenplay_v2.py`；Agent replacement 不得绕开这些产品版本生命周期。

### 2.3 Turn、Operation、Root Run 和 LongTask

1. `submit_turn` 以 `(projectId, Idempotency-Key)` 幂等创建 Turn，并保存用户文本、stageCommand 和非密钥 runtime profile。
2. 服务 claim Turn 后创建一个 Root Run；Root binding 为：
   - namespace：`screenplay.conversation_turn`
   - aggregate：projectId
   - command：初始 Turn commandId，或 continuation commandId
3. profile 从 Root 的持久 Turn 和 workspace 补齐 source book/scope；有 stageCommand 时强制 planned 模式。
4. 模型 TaskSpec 被解析为 `ScreenplayIntent`：
   - `answer` 走 inline，不创建 Operation/LongTask；
   - 正式 create/revise/review 经 resolver 冻结业务输入，创建 Operation，编译 Manifest/recipe，再创建 LongTask。
5. dispatcher 在同一数据库事务里 attach LongTask、Operation、Turn 和 Root runId。
6. Root Run 终态 projector 是 Operation/Turn/Revision 成功或失败的最终业务结算边界。

以下身份不可混淆：

| 层级 | 当前权威身份 | 是否有独立 PurrA Run |
| --- | --- | --- |
| 对话 | Turn id | 否；绑定到 Root Run |
| 正式动作 | Operation id | 否；投影到 Root Run 生命周期 |
| 持久工作 | LongTask id | 否；由 Root/continuation Run 绑定执行 |
| Part attempt | `taskId + unitId + attempt` / `operationScopeId` | **否** |
| continuation | 新 Root Run + 旧 LongTask run binding | 是，新建 continuation Root |

同一正式 Turn 的 Part 工具调用、模型调用和 canonical events 当前都发生在 owning Root Run 内。不能依赖 `runId` 单独区分 Part。

### 2.4 Manifest、recipe、Part 和并发

当前 `SCREENPLAY_RECIPE_VERSION = 10`。Manifest digest 基于 artifact kind、冻结 source revision refs、assembly strategy 和完整 Part 描述确定性计算。

Part kind 到 Unit kind：

| Part kind | Unit kind | 默认最大 attempts |
| --- | --- | --- |
| `evidence` | `collect_evidence` | 2 |
| `draft_scene` | `generate_draft_scene` | 4 |
| `episode_metadata` | `generate_episode_metadata` | 2 |
| `review_dimension` | `generate_review_dimension` | 4 |
| `document_section` | `generate_document_section` | 4 |
| `expansion` | `expand_structure_series_arc / episode_plan / character_arcs` | 2 |
| `host_projection` | `project_structure_hooks` | 2 |
| `validation` | `validate_manifest_part` | 2 |
| `final_response` | `compose_final_response` | 2 |

各目标角色的 DAG：

- `screenplayDraft`：每集 evidence → 逐场 scene（串行承接）→ episode metadata → episode validation；整体 max parallelism 1。
- `review`：每集 immutable draft evidence → 五个 review dimensions → episode validation；整体 max parallelism 5。
- `sourceAnalysis`：document evidence → 每授权叶章节 digest → 每 12 项一级 reduction → 五个公开 section → document validation；max parallelism 12。
- `creativeBrief` 等文档：evidence → bounded sections → document validation；通常最多 4 并发。
- `structure`：series arc index/expansion → episode plan index/expansion → character arcs index/expansion → host-projected hooks → validation；max parallelism 12。
- `sceneList`：按 episode section 生成，再验证。
- 每个正式 recipe 最后追加 private `compose-final-response`。

PurrA Root plan 与产品 DAG 不是一一对应。Host 只把每个 Part 映射到一个模型 authored plan step；不能让 planner 改写冻结业务依赖或凭空扩充范围。

### 2.5 Checkpoint

Checkpoint 仅在以下业务边界产生：

- draft：每集 `episode:{n}` validation 完成；
- 文档：`document:sections` validation 完成；
- review：所有分集 validation 完成后 `review:aggregate`。

`screenplay_checkpoint_plans` 以 `(operationId, checkpointKey)` 为主键，并对 `(taskId, checkpointKey)` 唯一。receipt 保存 rootRunId、input/plan digest、状态、结果、错误和 reservation lease/epoch。

Checkpoint planner 可以：

- 复用已完成业务事实；
- 对未来步骤修订 Root plan；
- 要求显式 continuation，令 LongTask/Operation/Turn 进入 paused；
- 在 continuation Root 上重新绑定 ready/paused receipt；
- 绝不能重写已经完成的 Part、冻结输入或 source revision scope。

### 2.6 工具目录和能力选择

工具 schema 与 handler 名称必须完全一致，否则 composition 构建失败。当前工具共 18 个：

- 项目读取：`inspectScreenplayProject`、`readScreenplayDeliverable`、`searchScreenplayDeliverables`
- 分集/场景：`getScreenplayEpisodeContext`、`getScreenplaySceneContext`
- 原作读取：`inspectSourceStructure`、`readSourceChapters`、`searchSourceText`、`listSourceCharacters`、`readSourceCharacters`、`listSourceWorldEntities`、`readSourceWorldEntities`、`readSourceBackground`、`querySourceStoryFacts`、`readSourceOutline`
- Part 依赖：`readScreenplayTaskDependencies`
- 候选：`writeScreenplayCandidatePart`、`inspectScreenplayCandidate`

工具选择规则：

- Root 只获得授权范围内的 read tools，不能写 Candidate。
- Unit 根据 `toolAccess` profile 精确授权；正式阶段不应同时获得超出 Part 合同的读取和写入能力。
- 没有 sourceBookId 时移除 source tools；受限章节授权时移除无法证明逐条来源范围的 whole-book catalog 工具。
- `writeScreenplayCandidatePart` 是 proposed write、cancellation-linearizable、host-managed durable、batch payload；其他工具是 read。
- Candidate 写入时 project/task/unit/role/part key/dependencies/revision scope/episode/source scope 等字段由 Host 绑定，模型只拥有 schema 中声明的内容字段。
- 长场景正文和 episode metadata 使用 hostCapture：模型输出由 Host 捕获，相关 Unit 不向模型开放 Candidate writer；其他候选 Part 通常必须调用 writer。
- 动态正文依赖必须通过成功且可追踪的读取工具获得，不能把完整依赖 body 直接注入 prompt。

### 2.7 Revision scope 和来源凭据

当前 scope 同时包含：

- sourceBookId/sourceScope：原作授权边界；
- sourceRevisionRefs：resolver 冻结的来源 Revision；
- deliverableRevisionScope：交付物 role → revisionId；
- dependencyPartKeys：当前 Unit 允许消费的直接上游 Part；
- boundEpisodeNumber/sceneId/sceneIds：分集和场景范围。

读取原作会写 `screenplay_source_receipts`，保存 runId、tool、source type/id/revision、coverage 和 excerpt；最终 Revision 可投影 source refs。

`draft_scene` 的 `readScreenplayDeliverable` 强制使用 Host 绑定 Revision。其他 tool profile 目前允许显式读取同项目、同 role 的任意合法历史 Revision；未显式传 revisionId 时优先使用绑定 scope，否则读取 accepted head。

这条“可浏览历史 Revision”的行为已有测试保护，因此是当前事实，不应误写成权限漏洞；但它是否能成为正式候选的可采纳证据仍是待决策项。

### 2.8 Candidate Artifact 与最终 Revision

Candidate 使用通用 Artifact 协议：

- namespace：`purrtypos.screenplay`
- kind：`candidate_part`
- owner ref kind：`screenplay_operation`
- owner identity 当前由 `{runId, taskId, unitId}` digest 得到
- 一个 Artifact 只接受一个规范化 batch/item；校验后 finalize
- normalizer 必须确定性；Candidate 类型、key、内容和 validation contract 必须与 Host scope 一致

当前真正的 Part 成功路径是：

1. `run_durable_operation` 在 owning Root Run 内调用 `run_operation`，生成 `operationScopeId=taskId:unitId:attempt`；
2. 工具或 hostCapture 写 Candidate；
3. `ScreenplayCandidateCompletionProjector.finalize_operation(...)` 在独立事务内校验 operation-scoped dependency reads、场景上下文和 Candidate，再 finalize Artifact；
4. Unit executor 把 finalized Candidate 投影为 Host Part Artifact/Unit output；
5. Root terminal projector 收集已完成 Part，Assembler 生成产品 Revision；
6. `SqliteScreenplayOperationFinalizer` 原子写 Revision、Operation succeeded、Turn completed 和 receipt。

必须明确区分旧路径：

- `ScreenplayCandidateCompletionProjector.project(run_id, commit)` 查询 binding namespace `screenplay.agent.task`，声称与 `run.completed` 同事务提交 Candidate。
- 当前 Part **没有独立 Run**，`AgentCoreRunOptions.binding=screenplay.agent.task` 不会覆盖 owning Root 的 binding，因此该 `project()` 对当前 Part operation 路径不可达。
- 当前有效路径是显式 `finalize_operation()`；它不是 Root `run.completed` projector，也不与最终产品 Revision commit 同一事务。
- 新实现不得把旧 `project()` 注释当作真实合同，也不得为了复活它而给每个 Part 人为创建 synthetic Run。

### 2.9 canonical 事件和 SSE

- 唯一传输来源是 PurrA canonical output journal / `ai_agent_run_events`，旧 screenplay 专属 output 表已退休且启动时删除。
- SSE 使用通用 `stream_agent_pages`；断开连接只解除订阅，不取消业务 Run。
- page 字段：`cursor`、`turnId`、`taskId`、`runId`、`runRole`、`userContent`、`model`、`turnCreatedAt`、`chunk`、`createdAt`；页级还有 `nextCursor`、`hasMore`、`projectionVersion`。
- 只向客户端投影绑定 Turn 的 public canonical output；游标必须越过无 Turn/不可见/不可映射事件，避免重复扫描。
- 工具历史显示名称由 canonical `operation.started` labelParams 加产品语义投影补充。
- `runRole` 当前按 Run binding namespace 推导：conversation Root 为 `root`；旧 `screenplay.agent.task` 为 `unit`；final response namespace 为 `final_response`；其余为 `related`。

### 2.10 usage

- LongTask 保存通用累计 usage；Operation 另有 `screenplay_agent_operation_usage(operation_id, run_id)`，按 runId 幂等记录后聚合到 Operation。
- 记录字段：invocationCount、inputTokens、generation/outputTokens、reasoningTokens；推理 token 有未报告 attempt 时为 unknown/null。
- 当前 Part operation 共用 Root runId，因此 usage 的持久幂等粒度是 Root/continuation Run，而不是单个 Part attempt。
- Root terminal projector 会在业务结算前投影 Operation usage；未能结算 usage 会阻止 Part/LongTask 成功提交。

### 2.11 取消、暂停、恢复和孤儿处理

- 显式取消先持久化幂等 cancel receipt 和 fence，再请求 PurrA Run cancel、取消本地 wrapper、标记 LongTask cancel；Operation/Turn 最终终态由 Root projector 原子结算。
- SSE disconnect 不等于取消。
- retryable/需重新规划错误可令 LongTask、Operation、Turn paused；resume 必须携带 expected Operation revision 和兼容的 runtime capability snapshot。
- continuation 只允许从 canceled source Root 恢复同一 LongTask；新 Root 保存 `continuationOf`、operation/lease/epoch/identity digest，并用 command reservation 保证单 winner。
- completed Unit/Artifact/checkpoint 必须复用，不能因 continuation 重写历史结果。
- truncate 是破坏性对话编辑：先对当前及之后 Turn 持久化 cancel fence，等待本地或外部 Root 停止并完成结算，超时则 409，不得先删业务状态。
- orphan recovery 通过 canonical Root terminal projector/取消 projector 对账；不能只依据进程内 `_ACTIVE_TASKS` 判断真实运行状态。

## 3. 已知缺陷：新实现不得继承

### 3.1 身份、投影和溯源

1. **伪 Part RunBinding**：`screenplay_tool_calling.py` 构造 `screenplay.agent.task` binding，但 `run_durable_operation` 在 owning Root 上执行 operation，binding 不会成为独立 Run 记录。旧 `candidate_completion_projector.project()`、历史 domain label enrichment 和 `runRole=unit` 依赖这份不存在的 Run binding。
2. **Part 溯源粒度丢失**：多个 Part 的 source run、usage 和 UI role 都指向同一 Root runId。新实现必须以 `operationScopeId` 或等价的 `(taskId, unitId, attempt)` 做 Part provenance；不能只增加另一个无法持久化的 RunBinding。
3. **注释与事务承诺失真**：旧 projector 写着 Candidate 与 `run.completed` 同事务，当前有效 `finalize_operation()` 并不满足该承诺。

### 3.2 Candidate 和重试

1. **漏写 Candidate 被永久失败**：tool operation 设置 `require_tool_call=False`；非 hostCapture Part 若模型忘调 `writeScreenplayCandidatePart`，最终 `candidate_commit_failed` 默认不可重试。模型行为偏差不能直接烧掉整个 Part。
2. **Artifact owner 不含 attempt**：写后、finalize 前中断时，重试复用 `{runId, taskId, unitId}`；若新输出不同会报 candidate conflict。不得简单删除 OPEN Artifact 掩盖 effect uncertainty。
3. **结构化输出错分类**：`structured_output_invalid` 在调用内两次解析后被标为 `retryable=False`，且未进入 MODEL_OUTPUT 分类，最终成为永久业务失败。
4. **阶段呈现可能反向影响已保存结果**：Part/Host Artifact 写入之后才调用 `report_operation_result`。展示或取消竞态不能让已经持久化的业务结果变成失败/重算输入。

### 3.3 Checkpoint 和 revision

1. **episode metadata 映射 typo**：checkpoint receipt 将 `compose_episode_metadata` 映射为 `episodeMetadata`，但真实 Unit kind 是 `generate_episode_metadata`；metadata 凭据被漏掉，checkpoint 事实判断失真。
2. **revision admissibility 未表达**：除 draft scene 外，工具允许读取同项目旧 Revision，但 Candidate finalize 只检查“发生过允许的读取/依赖读取”，没有统一证明正式输出只基于 frozen admissible revisions。
3. checkpoint observer 的进程内 lock map 没有清理策略；replacement 应使用有界/生命周期明确的 keyed lock。

### 3.4 异常和并发

1. Unit executor 的 `finally` 中 usage settlement error 会覆盖原始 model/tool 异常，丢失首要失败原因。
2. 查找当前 Unit 使用无默认值 `next(...)`；状态不一致时会泄漏 `StopIteration`/非领域错误，而不是稳定合同错误。
3. usage CAS 最多 16 次、没有 backoff/jitter；每次有异步数据库 I/O，严格说不是阻塞式 busy loop，但会放大争用且次数是魔数。
4. `structured_output_invalid`、candidate missing、工具输入、配置错误和业务不变量的分类分散，存在同一类错误在不同路径得到不同 durable disposition 的风险。

### 3.5 进程和呈现残留

1. `_ACTIVE_TASKS` 是 module-global、仅本进程可见，不能作为运行权威；与其他 Agent 的活跃表重复。
2. Root `signal=asyncio.Event()` 从不主动置位；实际取消依赖 run-control。保留空壳 signal 会误导实现者以为有第二条可靠取消通道。
3. `runRole=unit` 和基于 `screenplay.agent.task` binding 的历史标签重建在当前 operation 路径不会命中；新投影必须直接读取 operation identity/持久事件。

## 4. 待决策项

以下内容在 replacement 编码前需要形成显式 ADR 或计划勾选项：

1. **历史 Revision 浏览与正式证据**：
   - A：正式 Unit 只能读取 frozen `deliverableRevisionScope`；
   - B：允许浏览历史 Revision，但只有绑定 Revision 的 receipt 可成为 Candidate admissible evidence。
   推荐 B，兼顾分析能力与可重现性；finalize 必须验证 admissible receipt，而不是只验证 role/project。
2. **Candidate 写后中断恢复**：
   - 若 Candidate 已完整写入但 Unit 未完成，是直接验证并 finalize，还是新 attempt 重新生成？
   - 推荐先恢复/验证既有完整 Candidate；确需重生成时以 attempt 创建新 Artifact，并保留旧 Artifact 为诊断记录。
3. **Artifact owner**：是否采用 `(taskId, unitId, attempt)`，并把 owning Root/continuation Run 作为 metadata；不得继续只用 Root runId。
4. **非 hostCapture Candidate side effect**：是 Core 支持“必须发生指定 durable effect”，还是 Host 在 missing 时归类为有界 MODEL_OUTPUT_INVALID 重试？仅设置全局 `require_tool_call=True` 会破坏 hostCapture Part，不可采用。
5. **Part 是否需要独立 Agent Run**：默认答案是不需要。只有当产品要求 Part 独立审批、分享、取消或安全主体时才重新评估；不能为了兼容死 projector 而创建。
6. **Checkpoint planner 权限**：确定它只修订未完成的展示/执行计划，还是允许触发业务 scope 变更。当前合同只允许前者；后者必须成为新 Operation。
7. **usage 展示粒度**：公开 UI 是否需要 Part/attempt usage；无论 UI 是否展示，内部必须有 operation-scope provenance 以支持结算和诊断。
8. **旧 Run 回放期限**：删除旧 executor 后，是长期保留 lightweight legacy reader/projector，还是迁移所有历史可见事件。不能以新 Run 可执行作为删除 reader 的条件。
9. **普通问答能力范围**：Root 当前可读取授权的项目、原作人物、世界、背景和事实。需要明确“总人物数”等回答是目录事实、授权范围统计还是模型推断，并在 UI 标记来源范围。

## 5. 数据库兼容清单

replacement 必须读取或迁移以下现行数据，不能双写同一业务权威：

| 领域 | 表/存储 | 当前权威内容 |
| --- | --- | --- |
| 对话 | `screenplay_agent_turns` | Turn、stage command、Root、公开回答、状态/错误 |
| 正式操作 | `screenplay_agent_operations` | Operation 状态、revision、LongTask、result Revision、usage、receipt |
| 幂等命令 | `screenplay_agent_operation_commands`、`screenplay_agent_cancel_commands` | create/dispatch/pause/resume/finalize/cancel receipts 和 continuation reservation |
| 使用量 | `screenplay_agent_operation_usage` | `(operationId, runId)` 幂等 usage |
| PurrA Run | `ai_agent_runs`、`ai_agent_run_events` 及 canonical output journal | Root/continuation 状态、模型/工具事件、公开流 |
| PurrA LongTask | `ai_agent_long_tasks`、`ai_agent_long_task_units`、run bindings | recipe DAG、Unit attempt/status/output/失败、Root/continuation 绑定 |
| Artifact | `ai_agent_artifacts` 及 batches/claims | Candidate Part 与 Host Part 的 durable 内容 |
| checkpoint | `screenplay_checkpoint_plans` | 业务里程碑 plan revision receipt |
| 来源凭据 | `screenplay_source_receipts` | Run-bound source read receipts |
| 产品文档 | `screenplay_deliverables`、`screenplay_revisions`、`screenplay_revision_parts`、`screenplay_revision_inputs`、`screenplay_revision_source_refs` | 不可变交付版本、Part、输入和溯源 |
| 产品选择 | `screenplay_project_heads`、`screenplay_acceptance_events` | 用户接受的 head 与原子失效记录 |
| 编辑/审阅 | `screenplay_working_copies`、review decision/event、finalization event | 人工编辑、审阅裁决和最终完成 |
| 产品 outbox | `screenplay_command_receipts`、`screenplay_outbox_events` | V2 产品命令幂等与领域事件 |

旧的 `screenplay_agent_events/chunks`、`screenplay_conversation_*`、`screenplay_operations/operation_events` 不是兼容目标；schema 和测试明确要求其退休。

## 6. 现有测试资产

### 6.1 后端 deterministic/contract 测试

- `test_screenplay_agent_routes.py`：wire validation、submit/cancel/resume、canonical SSE、游标推进、断流不取消。
- `test_screenplay_agent_durable_service.py`：单 Root operation 执行、Root 原子结算、usage、取消竞态、truncate fencing、continuation single winner、孤儿恢复、checkpoint scope change。
- `test_screenplay_agent_rewrite.py`：TaskSpec/intent、Manifest v10、Part contract、结构扩展、checkpoint 状态机、source scope、host Part、流回放、失败分类。
- `test_screenplay_prompt_contracts.py`：Skill 可加载、示例满足 validator、正文依赖必须 read、提示词引用工具必须已授权。
- `test_screenplay_tool_catalog.py`：工具 profile、source 授权、revision read、Candidate Artifact、recorded reads、hostCapture、展示标签。
- `test_screenplay_read_cache.py`：跨 round/run cache identity、source edit invalidation、范围隔离、失败回滚和容量限制。
- `test_screenplay_v2_persistence_boundaries.py`：V2 不回写旧 CRUD、operation finalizer 是唯一产品成功提交边界。
- `test_screenplay_v2_schema.py`：旧库迁移、旧 runtime 表退休、Operation migration。
- `test_screenplay_v2_routes.py` / `test_screenplay_project_aggregate.py`：Revision、Working Copy、Accept、Review、Finalize、PDF 的产品生命周期。
- `test_long_task_run_binding.py`：Unit attempt binding 不可被 completion 改写。
- `test_screenplay_agent_runtime_cleanup.py`：只删除运行时归属数据，保留产品 Revision/Artifact/审阅状态，并保证事务回滚。

### 6.2 前端测试

- `src/ScreenplayAgentPage/conversationState.test.ts`：snapshot 恢复、SSE 增量、cancel/resume/truncate 状态。
- `screenplayConversationController.test.ts`：归档项目能力和 controller 边界。
- `stageAgentAction.test.ts`：UI stage action 到 typed stageCommand 的映射。

这些测试大多使用 scripted/fake gateway 和临时 SQLite，证明的是内部确定性合同，不等于真实 Provider 或 Electron 验收。

## 7. 必须新增的 characterization 与真实验收缺口

### 7.1 新实现切流前必须补齐的自动测试

1. 非 hostCapture Part 忘调 Candidate writer：应有界重试，不得永久失败。
2. Candidate 写成功、finalize/Unit commit 前中断：同内容恢复、不同内容新 attempt、取消三种路径均无冲突或双写。
3. `generate_episode_metadata` 必须进入 checkpoint receipt，并影响完成事实判断。
4. structured JSON 连续无效：调用内重试与 Unit attempt 总预算有明确上限和最终分类。
5. primary model/tool error 与 usage settlement 同时失败：保留 primary error，附带 settlement error。
6. 同一 Root 并行 Part 的 event、source receipt、usage、Artifact 都能按 operationScopeId 精确重建。
7. 历史 Revision 可以浏览但不能污染 frozen evidence（若采用推荐决策 B）。
8. public stage projection 失败/Root cancel 与 Candidate 成功提交竞态，不得破坏已确认业务 effect。
9. 多进程 submit/cancel/resume/truncate/orphan takeover，不依赖 `_ACTIVE_TASKS` 正确工作。
10. 旧 Root Run/snapshot/SSE 在 replacement 启用后仍可只读回放；同一 Run 永不跨 implementation version。

### 7.2 真实 Provider 验收

至少对每个目标角色执行一个全新 Run：

- 普通问答能主动选择 `readSourceBackground`、`listSourceCharacters` 等合适工具，并明确授权范围；
- create/revise/review 的 TaskSpec、工具选择和 Candidate side effect 符合合同；
- Provider 临时失败、无效 JSON、漏工具调用能够按预期恢复；
- 10 集 structure 和多场 screenplayDraft 在真实上下文窗口/输出预算下完成；
- usage 与 Provider 返回一致，且 Part attempt 可追踪。

### 7.3 Web/Electron 验收

- 新建 Turn 后自然文本进度、工具标签、最终回答不重复；
- 页面刷新/应用重启后 snapshot + SSE cursor 无丢失、无重放污染；
- SSE 断开不取消，显式取消立即展示并最终结算；
- paused Operation 更换兼容模型后可 resume，陈旧 revision 被拒绝；
- truncate 在本地/外部执行器运行时均先 fence，失败不删数据；
- Candidate Revision 出现在 workspace，但只有用户 accept 后成为 head；review 决策和 finalize 仍由 V2 产品接口控制；
- 历史旧 Run 仍能查看，replacement Run 显示 operation 级来源和 usage。

## 8. Replacement 的最低兼容验收线

只有同时满足以下条件，剧本旧 executor 才可以停止接收新 Run：

- HTTP、snapshot、SSE 和前端 types 没有未经版本化的破坏；
- answer 与 formal operation 两条路径均通过；
- Manifest/recipe/Part 合同有明确 replacement version，旧 LongTask 仍由旧 executor 恢复；
- Candidate/Revision 不双写，Root terminal commit 仍是唯一产品成功边界；
- cancel、pause、resume、truncate、orphan recovery 通过故障注入；
- operation-scope provenance/usage 可追踪；
- deterministic、真实 Provider、Web/Electron 三层验收分别记录，不能互相代替。

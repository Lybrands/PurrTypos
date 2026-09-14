# Novel Analysis 冻结实现退役边界

日期：2026-09-14

## 结论

> 2026-09-14 状态更新：下述前置迁移、启动职责接管和组合根断线均已完成；纯旧执行岛
> 已按本文边界删除。后续调用图复核又确认所谓“Writing 共享岛”没有任何 Writing 生产
> 调用者，因此没有复制到新公共层，而是只抽出仍在用的最小读取合同后直接退役。

预检时 Novel Analysis **不具备整包删除条件**。第二次真实库观察已证明 legacy
执行面没有活跃工作，但生产入口仍把四类现役职责接在冻结目录上：会话、混合版本历史
投影、legacy Artifact/正式资料读取，以及旧结果的继续、编辑、审核和发布。

下一批不删除冻结源码，而是先把上述职责拆开：replacement 保留唯一新建和执行路径；
legacy 只保留名称明确、无模型调用、无 Artifact 写入、无 Task 状态变更的历史只读
adapter。旧结果不得继续、编辑、审核或发布；需要这些能力时，用户应发起新的
replacement 分析。

## 观察 2：真实持久库事实

2026-09-14 18:17 CST，在应用进程已停止的前提下，对默认数据库
`~/Library/Application Support/purrtypos/purrtypos.db` 使用 SQLite
`mode=ro&immutable=1` 读取元数据。没有运行 schema 初始化、migration 或写事务，也
没有读取 prompt、来源正文、Artifact payload、分析正文或其他创作内容。

按持久 implementation identity、binding namespace 和 mode 联合识别：

| 对象 | 状态 | 数量 |
| --- | --- | ---: |
| legacy Run | done | 334 |
| legacy Run | failed | 38 |
| legacy Run | canceled | 53 |
| legacy Run | running / pending / paused / blocked | 0 |
| 关联 Durable Task | completed | 1 |
| 关联 Durable Task | failed | 10 |
| 关联 Durable Task | canceled | 12 |
| 关联 Durable Task | pending / running / paused | 0 |

总计仍为 425 个 legacy Run，与观察 1 完全一致。第二个时间点因此确认：不存在需要由
冻结 executor/profile/recovery 接管的进行中工作。

但数据库中仍有真实历史读取面：

| 历史对象 | 数量 |
| --- | ---: |
| finalized legacy Artifact | 1,111 |
| 其中最终候选 `novel_source_analysis_candidate` | 1 |
| 分析会话 | 9（1 open、8 closed） |
| 绑定到 legacy Run 的 session command | 95 |
| 已发布正式分析 | 0 |
| replacement Run / Artifact | 0 |

1,111 个 Artifact 由 336 个 fact page、240 个 observation page、281 个 model result、
254 个 unit result 和 1 个最终候选组成。这里的计数只用于判断兼容边界，不代表所有
中间 Artifact 都应暴露给 UI；公开历史 adapter 仍应只解析历史 Run 所引用的 finalized
最终 Artifact。

## 当前生产调用边界

冻结清单中的 Novel Analysis 文件不是一个封闭执行岛。

| 冻结职责 | 现行生产调用者 | 处置 |
| --- | --- | --- |
| `NovelAnalysisService.start/follow_up/replace_turn/pause/resume/cancel` | `agents.novel_analysis.product_service` | 移除 legacy 执行路由。create 已固定 replacement；legacy task 或 Artifact 的变更命令显式返回“旧版仅供查看”。 |
| `NovelAnalysisService.list_for_revision/get_artifact` | HTTP Run 列表、SSE、Artifact GET | 迁入 `agents.novel_analysis` 下的只读历史 adapter，保持当前 DTO 和 cursor 可读，不安装 legacy Profile。 |
| `review/publish/list_published/get_published` | Artifact/正式资料 HTTP | replacement review/publish 保持现行服务；正式资料查询抽成中立 query。legacy candidate 不再产生新的 review Artifact 或正式资料。 |
| `NovelAnalysisSessions` | 新建、追问、编辑及会话 CRUD；replacement Run projection | 这是现役产品存储，不是 legacy executor。原样迁入 `agents.novel_analysis`，所有 router/replacement 调用者切换后再删除旧文件。 |
| legacy Profile/Executor/Progress/Runtime/Recovery/Reliability | composition、lifespan 和冻结执行内部 | 观察 2 后可从组合根与 lifespan 断开；先移除生产调用，再删除纯 legacy 文件及专项测试。 |
| `NovelAnalysisSourceReader`、`NovelAnalysisArtifactStore`、legacy tool schema | 写作技法生成、分析证据/候选辅助、公开 facts | 属于跨 Agent 的遗留共享岛，不能随 Novel Analysis executor 一起删除。先提取中立 source/evidence/artifact schema；Writing 调用者迁移后再删旧名字。 |

`application.novel_analysis_stream` 已无生产调用者，只剩测试，可随纯 legacy 执行批次
删除。相反，`application.novel_analysis_source`、`novel_analysis_artifacts` 和
`novel_analysis_tools` 仍被 Writing 或分析资料共享模块直接导入，当前删除会破坏现役
能力。

## 必须关闭的伪兼容行为

当前 versioned product service 仍把以下请求回落到冻结实现：

- 找不到 replacement namespace 的 Artifact 时，追问回落 legacy；
- 编辑 legacy Run 时回落 legacy replace-turn；
- legacy Task 的 pause/resume/cancel 回落 legacy control；
- legacy candidate 的 review/publish 回落 legacy 写入链。

这些不是“历史只读”，而是在生产继续执行冻结逻辑。观察 2 后应全部移除。对于终态
legacy Task，pause/cancel 没有实际价值；10 个 failed Task 即使技术上可恢复，也不应
在退役后重新启动旧模型链。历史 Artifact 的普通追问也应要求重新分析，不能因为
Artifact namespace 未匹配就隐式执行旧实现。

## 分批迁移顺序

1. **会话边界迁移**：新增 replacement-owned session repository/service，迁移 router、
   Run projection 和 edit lifecycle 的调用；保持物理表和历史行不变。
2. **历史只读 adapter**：只实现 legacy Run DTO、关联 Unit 状态、最终 Artifact 读取和
   canonical event 可见性；禁止模型、Task control、Artifact mutation 和 publication。
3. **单路径产品控制**：`VersionedNovelAnalysisProductService` 去掉 legacy service 注入；
   start/follow-up/edit/resume/cancel 只走 replacement identity，legacy identity 统一返回
   明确的只读错误。Run 列表与 SSE 组合 replacement projection 和历史 adapter。
4. **正式资料查询抽离**：把 `list_published/get_published` 从 legacy service 迁到中立
   query；replacement review/publication 维持 immutable Artifact 流程。
5. **断开旧 runtime**：composition 不再安装 legacy Novel Analysis Profile；lifespan
   不再启动旧 recovery/reliability monitor。随后删除 profile、executor、progress、
   runtime、recovery、reliability、service、stream 及 legacy-only 测试。
6. **处理跨 Agent 共享岛**：在 Writing 迁移中抽取 source/evidence/artifact/schema
   合同并切换调用者；到时再删除旧 source/artifacts/tools 和
   `writing_technique_generation_tools`，不得留下 import shim。

## 数据库保留边界

本次退役不授权迁移或删除真实数据。以下表及历史行继续保留：

- `novel_analysis_sessions`、`novel_analysis_session_commands`、
  `novel_analysis_superseded_runs`；
- 通用 Run、Run Event、Output Journal、Long Task、Unit、Artifact 与 batch/claim 表；
- `novel_source_analyses`、facts、craft cards、evidence 和技法关联表；
- 来源 revision 与 section 表。

legacy implementation identity 和 binding namespace 也必须保留用于历史识别；保留身份
不等于保留 legacy 创建、恢复或写入能力。

## 删除门禁

纯 legacy 执行批次只有同时满足以下条件才可删除：

- production import scan 不再从 router、composition、lifespan 或 `backend/agents` 进入
  legacy Profile/Service/Executor/Recovery；
- replacement 新建、追问、编辑、pause/resume/cancel、review/publish 回归通过；
- 425 个 legacy Run 的列表、终态、公开事件和最终候选仍可只读回放；
- legacy 变更请求有稳定、明确的只读错误，不出现 fallback 或 500；
- session CRUD 和 replacement command binding 不依赖冻结模块；
- backend、frontend、Electron、package 内容、冻结清单和端口清理门禁通过。

## 下一开发项

**Novel Analysis Session Boundary Migration 已完成**：现役会话职责已迁入
`backend/agents/novel_analysis/sessions.py`，正式 router 的分析启动、追问与会话 CRUD
均已切换。物理表、历史行与 wire DTO 未改变；冻结 session 文件只剩 legacy service
内部调用。架构守卫禁止 router 重新导入该冻结文件。

**Novel Analysis Historical Read Adapter 已完成**：新增
`agents.novel_analysis.legacy_read_adapter.NovelAnalysisLegacyReadAdapter`，公开表面仅有
`list_for_revision` 与 `get_artifact`。正式 Run 列表、SSE 混合投影和 legacy Artifact
GET 已切换到该 adapter；等价性 fixture 要求同一历史 Run/Task/Artifact 的新旧 DTO
完全一致。该 adapter 不导入 frozen service/profile/executor，也不提供模型调用、Task
control、Artifact 写入或 publication 方法。

**Novel Analysis Product Control Single Path 已完成**：versioned product service 已删除
legacy service 注入；新建、追问、编辑、pause/resume/cancel 不再回退冻结执行。
legacy Task/Run/Artifact 的变更请求在写入前返回明确的“旧版仅供查看”。正式资料列表与
详情已迁入 `NovelAnalysisPublishedQuery`。正式 router 不再导入
`application.novel_analysis_service`。

现有“没有分析结果也只问原文”并非 legacy 专属语义。为避免删除 fallback 造成功能
倒退，replacement follow-up 现在允许无 Artifact 的 Reactive Run，只授权有界来源目录
与片段读取；有 replacement Artifact 时才增加结果读取工具和 owner Run identity。
无 Artifact 的追问与编辑均持久化为 replacement identity，不借用 legacy Profile。

下一项进入 **Novel Analysis Legacy Runtime Detach/Delete Preflight**：先重新计算冻结
profile/executor/service/recovery 的生产调用闭包和专项测试边界；确认 cross-Agent
source/artifact/tool 共享岛不在删除集合后，再断开 composition/lifespan 并删除纯 legacy
执行文件。本项不触碰 Writing 仍使用的共享岛。

## Detach/Delete Preflight 结果

2026-09-14 重新按 Python AST 计算冻结模块的直接生产 import，而不是按文件名推断归属。
预检结论是：**当前还不能直接执行原队列中的“断开并删除”**。会话、历史读取和产品
控制已经完成迁移，但启动期仍有两项现役职责没有 replacement owner，且 Writing 仍直接
复用部分 legacy 分析实现。

### 当前生产闭包

| 分组 | 文件 | 当前生产事实 | 决策 |
| --- | --- | --- | --- |
| 可在断线后删除的纯 legacy 岛 | `novel_analysis_agent_profile.py`、`novel_analysis_service.py`、`novel_analysis_runtime.py`、`novel_analysis_recovery.py`、`novel_analysis_sessions.py`、`novel_analysis_stream.py` | 除 `composition_factory.py`、`main.py` 和该岛内部调用外，已无正式产品调用；stream 已完全无生产调用 | 启动职责迁移和断线后删除 |
| 必须先迁移的现役启动职责 | `novel_analysis_reliability_baseline.py`；旧 recovery 所承担的 due-task 扫描 | reliability 是 namespace 级无内容指标，不属于 legacy 执行；replacement UI/DTO 已公开 `workflowAutoRecoveryEligible` 和 `workflowAutoResumeAtMs` | reliability 原语义迁入 `agents.novel_analysis`；新增 replacement-owned 自动恢复 coordinator 后才允许删除旧 recovery |
| Writing 共享岛，暂不删除 | `novel_analysis_executor.py`、`novel_analysis_tools.py`、`novel_analysis_source.py`、`novel_analysis_artifacts.py`、`writing_technique_generation_tools.py` | Writing 技法生成动态导入 executor 的 `_normalize_candidates`、`bind_model_candidate_scope`，并读取旧 tool schema、SourceReader 和 ArtifactStore | 留到 `Writing Shared Analysis Utility Extraction`；迁出真实共享 value object/helper 后一次性删旧名字，不保留 shim |
| 共享工具伴随读取 | `novel_analysis_progress.py` | 保留的 `novel_analysis_tools.py` 在 saved-result handler 中动态调用 `saved_results/read_saved_result` | 与 tools 一起迁移，不能在纯旧执行批次先删 |
| 共享岛的伴随模块 | `analysis_source_spans.py`、`analysis_candidate_input.py`、`analysis_observation_references.py`、`analysis_evidence_references.py`、`analysis_provenance.py` | 只被旧 tools/executor/service 或 Writing 技法生成调用 | 与 Writing 共享岛一起迁移，不能在 Novel Analysis 首批删除中顺带清理 |
| legacy-only 非冻结生产模块 | `domains/novel_analysis_public_facts.py` | 当前唯一生产调用者是旧 `NovelAnalysisService` | service 删除时一起删除；需要保留的正式资料读取已由 `NovelAnalysisPublishedQuery` 接管 |

这里修正了先前“executor 可随 Profile 一起删除”的过早判断。它虽然不再承担 replacement
Novel Analysis 执行，却仍是 Writing 的运行时依赖；按 Agent 名称删除会直接破坏技法生成。

### 两个硬前置条件

1. **replacement 自动恢复必须先接管。** 旧 `NovelAnalysisRecoveryService` 只按
   `namespace = purrtypos.novel_analysis`、paused 和 system reason 扫描，没有按 task kind
   或持久 implementation identity 过滤，因此它会把 replacement task 交给旧 service。
   replacement 的 `NovelAnalysisReplacementRecoveryService` 目前只构造人工 continuation，
   没有 `recover_due` 或 monitor。既然产品已经公开自动恢复资格和时间，不能直接删除
   monitor 造成静默功能倒退。新 coordinator 必须只接受
   `kind = novel_analysis.purra-native` 且 owner Run identity 为 replacement 的 task，使用保存
   的 runtime binding，并由 composition 跟踪后台 drain。
2. **可靠性 baseline 必须迁出冻结目录。** 当前采样查询按统一 namespace 统计 legacy 与
   replacement task，是仍然有效的可观测性，不是旧执行 fallback。迁移只改变代码 owner
   和启动 import，不改变表、bucket、窗口或 metrics schema。

### Composition 断线合同

- `composition_factory.py` 删除 `build_novel_analysis_agent_profile` 的 import 和 factory；
- `purrtypos.novel_analysis` 的默认 runtime profile 改为
  `novel_analysis.purra-native.v1`；
- legacy implementation identity 继续留在 implementation registry，作用仅是识别历史
  Run 并返回只读错误/历史 DTO。它不代表 legacy Profile 仍已安装；Screenplay 已采用同一
  tombstone 模式；
- `create_agent_composition()` 的非 versioned 测试组合将只保留尚未退役的 legacy Writing，
  不能为了旧测试继续安装 Novel Analysis Profile；需要 legacy executor 的专项测试改用
  `tests/support` 下的显式 frozen fixture composition，且该 fixture 不进入生产打包；
- 任何 owner 为 legacy Novel Analysis 的执行请求必须在进入 Core 前失败关闭，历史读取不
  经 request profile router。

### 测试删除与保留边界

不能按测试文件名整包删除。`test_novel_analysis.py` 和
`test_novel_analysis_conversation.py` 同时包含 source/evidence/technique 共享合同与旧
Profile/Service/Executor 行为，必须先拆分：

- 删除或迁入 frozen fixture：旧 start/follow-up、planner、dispatcher、pause/resume、
  retry、旧 profile capability 和旧 public facts 写入测试；
- 保留并改接 replacement/只读 adapter：混合版本 Run/SSE、历史 finalized Artifact、session
  CRUD、legacy 变更拒绝和 published query 测试；
- 保留到 Writing 共享抽取：segmentation、source span、evidence/provenance、candidate merge、
  writing technique generation 测试；
- `test_novel_analysis_legacy_read_adapter.py` 目前仍用旧 service 建 fixture。删除 service 前须
  改为最小 SQL/repository 历史 fixture，避免“测试等价性”反向保活生产旧 service；
- `test_main_lifespan.py` 与 composition 测试必须断言生产 profile 列表不再含
  `novel_analysis`，同时验证 replacement recovery/baseline monitor 被启动和干净停止。

### 修订后的执行批次

1. **Novel Analysis Replacement Startup Ownership**：实现 replacement-only due recovery；
   迁移 reliability baseline；补 identity/kind 隔离、幂等 dispatch、启动与 shutdown 测试。
2. **Novel Analysis Composition Runtime Detach**：组合根停止安装旧 Profile，默认 namespace
   指向 replacement；lifespan 只启动 replacement recovery 和迁移后的 baseline；加入禁止
   production import frozen runtime 的架构守卫。
3. **Novel Analysis Pure Legacy Source/Test Deletion**：删除已经闭合的 service/profile/
   runtime/recovery/session/stream 和 public facts；拆分 legacy-only 测试并更新冻结
   manifest。executor/tools/source/artifacts 不属于这一批。
4. **Writing Shared Analysis Utility Extraction**：最后迁移并删除共享岛；不复制旧 Agent
   调度、恢复或 Profile 语义。

因此下一项不是直接删文件，而是 **Novel Analysis Replacement Startup Ownership**。

## Replacement Startup Ownership 完成记录

2026-09-14 已完成启动职责迁移：

- 新增 `agents.novel_analysis.automatic_recovery`。due-task 查询同时要求 replacement
  namespace、`novel_analysis.purra-native` task kind，以及 owner Run 完整的
  `novel_analysis/purra-native/v1/tool v1/recipe v1/artifact v1` 持久 identity；legacy 或
  identity 不完整的 Run 不会进入 runtime resolution；
- 同一进程的 due 扫描用锁串行化，并用 task 级 dispatching 集合阻止尚在执行的同一任务
  被重复调度；最终 continuation lifecycle 仍会验证 paused 状态和 replacement owner；
- automatic continuation 显式以 `recoverySource=automatic` 恢复，人工 pause/retry 仍默认
  为 `user`；后台执行由 composition 跟踪，shutdown 会取消并等待；
- reliability baseline 已等价迁入 `agents.novel_analysis.reliability_baseline`，物理表、6 小时
  bucket、100-task window 和 metrics schema 未改变；
- lifespan 已不再导入或实例化冻结的 `application.novel_analysis_recovery` 与
  `application.novel_analysis_reliability_baseline`，启动扫描和两个 monitor 都使用新 owner。

下一项进入 **Novel Analysis Composition Runtime Detach**。此时可以停止安装旧 Profile，
但仍不能删除 Writing 共享岛。

## Composition Runtime Detach 完成记录

2026-09-14 已完成生产组合根断线：

- `composition_factory.py` 不再 import 或安装冻结的
  `build_novel_analysis_agent_profile`；普通 lifespan 的 profile 列表中不再存在
  `novel_analysis`，只保留 `novel_analysis.purra-native.v1`；
- `purrtypos.novel_analysis` 的 versioned namespace default 已固定为 replacement Profile；
- 即使测试或宿主传入不包含 Novel Analysis 的部分 rollout policy，versioned composition
  也会把 Novel Analysis 固定为 replacement create identity，不能重新开启已退役的新建
  路径；
- legacy implementation profile mapping 仍保留在 implementation registry，作为历史 Run
  identity tombstone。历史查询、SSE 和“旧版仅供查看”判定仍可解析该 identity，但生产
  Profile registry 中没有对应可执行 Profile；
- 仍需验证冻结 executor 行为的专项测试改用
  `tests/support/legacy_novel_analysis_composition.py`。该夹具显式安装单个冻结 Profile，
  不被 `backend/main.py`、router、`backend/agents` 或生产 composition 导入；
- 架构守卫禁止 production composition 重新导入旧 Profile，并固定 Analysis namespace 的
  replacement default。

下一项进入 **Novel Analysis Pure Legacy Source/Test Deletion**：删除已经无生产职责的
Profile/Service/Runtime/Recovery/Session/Stream/PublicFacts，并把仍混在旧大测试文件
中的共享 source/evidence/technique 合同拆出。Writing 共享岛仍不在删除范围。

## Pure Legacy Source/Test Deletion 完成记录

2026-09-14 已完成纯旧执行岛删除：

- 删除旧 Profile、Service、Runtime binding、Recovery、Reliability 原文件、Session 原文件、
  Stream 和 PublicFacts；replacement-owned recovery、reliability、session、publication query、
  历史 adapter 与 versioned SSE 保持唯一现役入口；
- 删除只服务旧 Profile/Service 的测试夹具、host recipe 测试、旧 unsaved publication 测试，
  并从混合测试中移除旧 planner/dispatch/follow-up/retry/review/publish 行为；保留 source、
  evidence、provenance、candidate merge、Writing 技法生成及 legacy 只读回放合同；
- 历史 adapter fixture 已改为直接验证稳定 DTO，不再通过被删除的 Service 做自我对照；
  versioned SSE 只公开有 Durable Task membership 的 legacy Unit，孤立 Unit 不进入历史；
- 删除两个仍直接启动旧分析服务的过期验收脚本。现行 replacement 的完整分析、控制、
  publication 与 Electron 验收脚本位于 `scripts/acceptance/`；
- 冻结清单从 131 个文件收敛为 124 个。`novel_analysis_progress.py` 因保留工具目录仍有
  动态调用而明确延期，不伪装成纯旧死代码。

下一项是 **Writing Shared Analysis Utility Extraction**：只迁移 Writing 真实使用的
candidate/source/evidence/artifact/schema 与 saved-result 合同，不重建旧 Novel Analysis
Profile、调度、恢复或执行兼容层。

## Shared-Island Reachability Correction 与退役记录

2026-09-14 对 application、agents、domains、infrastructure 和 routers 重新生成 Python AST
反向 import 图后，否定了本文预检阶段的一个前提：
`writing_technique_generation_tools.py` 虽然名称含 Writing，但生产调用者只有已经退役的
`novel_analysis_tools.py`、`novel_analysis_executor.py` 及该闭环内部模块；Writing legacy
Profile、Writing replacement、Writing router 和 Writing application service 均不导入它。
因此不存在需要迁入 Writing 的运行时逻辑。

本批按真实可达性完成以下收口：

- 没有新建“共享分析工具层”，避免把不可达的旧 Analysis Unit 协议改名保活；
- 将 continuation 仍使用的稳定 JSON hash 提取为中立的
  `application.canonical_json.canonical_json_digest`；
- 将历史 reader/router 仍需识别的旧 namespace 与 Artifact ref prefix 收敛到
  `agents.novel_analysis.legacy_contracts`，该模块只含持久标识，不含执行行为；
- 删除旧 executor/tools/source/artifacts/progress、技法生成 tools、五个 analysis helper、
  旧 domain recipe/prompt、对应 legacy-only 测试及旧技法模型验收脚本；
- 历史 adapter 测试改用最小持久 fixture，不再调用任何旧 validator、executor 或
  Artifact writer；架构守卫禁止上述退役模块重新进入生产 import 图；
- 冻结清单从 124 个文件收敛为 118 个，只剩 Writing legacy 范围。

验证结果：2,019 项 backend 测试通过，完整测试收集、compileall、冻结校验与 diff check
通过。全量回归同时发现并修正一个早已失真的通用测试前提：当前只有 Novel Analysis
replacement 使用 Core model planner；Writing replacement 是 reactive，Screenplay
replacement 使用 host recipe planner，不能恢复旧 Profile ID 或强行统一 Planner 类型。

这同时完成了原队列中的 **Writing Shared Analysis Utility Extraction**（结论为无需迁移）
和 **Novel Analysis Shared-Island Retirement**。下一项应直接进入
**Writing Legacy Runtime Detach/Delete Preflight**。

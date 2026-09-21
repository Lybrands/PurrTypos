# 1.0.0 写作技法旧结构退役清单

日期：2026-09-07

状态：退役实现已接入，隔离库回归通过；未删除正式用户数据。本文第 2–9 节保留 P0 的实施前源码盘点，其中“当前”均指该盘点时点；被删除的旧文件按历史路径标注，不作为现行实现入口。

关联：[改造计划](2026-09-07-writing-techniques-1.0.0-plan.md)。本文约束旧结构的退出范围；新结构的身份、授权、版本和运行语义以 P0 新契约为准。

## 1. 退役边界与两种删除

本次升级不迁移旧写作方法、旧写作方案及其绑定，不提供把旧正文继续作为可执行技法的兼容读取。旧技法数据退出活动库、候选入库、方案选择、小说绑定、目录搜索和模型上下文路径。

必须区分以下两种操作：

| 操作 | 本轮确定的规则 |
| --- | --- |
| 一次性的旧格式退役 | 只处理本清单确认的旧技法表、旧绑定及旧执行接点。旧正文不转换成新文件；旧分析与 Run 中的历史结果保留只读，不因旧技法 ID 失效而删除整个记录。 |
| 新结构中的业务删除 | 归档并从活动库、搜索及新选择中隐藏；保留方案、小说、Run 或其他正式引用仍需要的版本，不改写已有固定内容。归档终止对应执行授权，活跃和恢复任务按 P0 契约停止受影响的后续调用；保留历史不等于继续允许执行。 |
| 新结构中的物理回收 | 只可能回收经引用检查确认无引用的文件。首版不自动回收历史版本；不以“当前版本不是它”“已经归档”作为可以删除文件的依据。未完成写入的临时文件与历史可用版本分开处理。 |

删除小说、删除原始来源和清空历史运行都不属于本次技法退役。不得调用现有“删除来源作品”接口来间接清理技法，不得清空整个数据库或整个应用数据目录。

本清单依据源码，不包含实际用户库的行数、孤立记录数量或活动任务数量。实施前必须在待处理数据库上只读盘点；不能把测试库或源码定义当成真实数据盘点结果。

## 2. 数据表与持久化载荷

### 2.1 退役的六张旧表

定义见 `backend/database/writing_method_schema.py`（已移除）。以下都是旧结构，不得仅保留旧表并继续读写正文。

| 旧表 | 当前承载的数据 | 退役规则 |
| --- | --- | --- |
| `writing_methods` | `method_type` 的 `primary/technique` 分类、`draft_markdown`、`draft_metadata_json`、来源引用及当前发布版本指针 | 旧记录不转成新技法。移除该表的旧创建、种入、更新、复制和搜索路径；新文件存储不维护第二份可编辑正文。 |
| `writing_method_revisions` | `markdown_body`、正文摘要、旧 `version_no`、`method_type` 和元信息 | 旧版本不作为新结构可读取版本；保留历史 Run 中原有 ID 与凭据，不伪造新版本映射。 |
| `writing_schemes` | `draft_members_json`、候选方案、`candidateMethodIds` 来源引用、发布指针 | 不转换旧组合或候选容器；新方案由用户在新结构中创建。 |
| `writing_scheme_revisions` | 旧方案名称、用途、版本号、成员摘要 | 不将旧方案版本自动映射到新方案；历史 ID 只作为历史记录。 |
| `writing_scheme_revision_members` | `scheme_revision_id`、`method_revision_id`、`ordinal` | 随旧方案退役，不能保留为活动依赖边；新依赖重新建立。 |
| `book_writing_method_bindings` | 作品的旧方法／方案版本选择、顺序与来源 | 清除旧绑定，不能把它当成新自动选用授权或本轮手动指定。保留被绑定的小说。 |

旧索引随相应旧表退出：`idx_writing_method_revisions_method`、`idx_writing_scheme_members_method`、`idx_book_method_binding_unique`、`idx_book_scheme_binding_unique`、`idx_book_writing_method_bindings_order`。

实施清理顺序以旧关系为依据：绑定 → 方案成员 → 方案版本／方案 → 方法版本／方法。不得使用仅按名称前缀匹配的广泛删除。如果新结构复用任一表名，必须先确认旧列结构并完成带版本标记的旧结构退役，再创建新表；不能用“表存在”作为每次启动清空该表的理由。

### 2.2 保留的来源、正史、小说与 Memory

以下保留边界由现有 schema 和关联直接支持：

| 数据 | 当前证据 | 保留规则 |
| --- | --- | --- |
| `books`、`outline_chapters`、`articles` 及小说其他业务内容 | [schema.py](../../backend/database/schema.py)、[正文表](../../backend/database/schema.py) | 不删除小说、章节、正文或相关创作数据。旧方法绑定删除不意味着删除作品。 |
| `novel_source_works`、`novel_source_revisions`、`novel_source_sections`、`novel_source_sections_fts` | [continuation_schema.py](../../backend/database/continuation_schema.py) | 保留原始来源、分节、不可变版本及检索能力。 |
| `novel_source_analyses`、`novel_source_analysis_facts`、`novel_source_analysis_evidence`、`novel_source_craft_cards` | [分析表定义](../../backend/database/continuation_schema.py) | 保留历史分析、事实、证据及观察。旧技法在分析记录中保留为历史内容，但不能重新通过旧候选路径入库或执行。 |
| `continuation_canon_snapshots`、`continuation_canon_records`、`continuation_bindings` | [正史与续写表](../../backend/database/continuation_schema.py) | 保留分支点正史、原作引用及小说续写关系。技法绑定与续写绑定不是同一对象。 |
| `story_memory_*`、`memory_source_heads`、`memory_source_deliveries`、`memory_book_deletions`、其他现有 Memory 数据及 `memory-component-v1/` | [Memory schema](../../backend/database/schema.py)、[备份实现](../../backend/application/project_backup.py) | 本轮不清理、不重建、不改写 Memory。 |
| `settings`、知识库绑定和其他无关表／目录 | [settings](../../backend/database/schema.py)、[初始化](../../backend/database/schema.py) | 不使用全库重置解决旧技法退役；现有恢复流程的凭据及知识库重新授权规则仍由原业务负责。 |

### 2.3 保留历史载荷，封闭旧执行入口

当前 `NovelAnalysisService._publish_transaction` 把 `distillation` 放进 `novel_source_analyses.summary_json`；`_analysis_mapping` 又把该字段返回给界面，见 [发布](../../backend/application/novel_analysis_service.py)和[历史映射](../../backend/application/novel_analysis_service.py)。旧技法并不只存在六张表内。

保留这些原始分析载荷及其摘要，不为删除旧技法而重算历史 `content_digest`。历史展示应明确是旧格式分析结果；关闭旧结果的候选创建、发布、试写重试、技法绑定等活动操作。只读展示历史字段不等于保留旧格式执行兼容。

`ai_agent_runs.binding_attributes_json` 中的 `writingMethodBindingSnapshot`、`writingMethodRecommendationRequested` 以及 `WritingDomainContext` 内的旧快照／覆盖参数，需要按历史协议识别；不能回灌为新授权。证据见 [WritingAgentProfile](../../backend/application/writing_agent_profile.py)、[领域上下文序列化](../../backend/domains/writing/contracts.py)。

保留 `ai_agent_runs`、事件、产物、任务、对话与已有上下文凭据；不能因为引用的旧版本已退役就删整条 Run，或把“版本不存在”变成重新生成并替换历史正文。新请求与恢复入口必须检查协议：旧活动／暂停任务不能静默套用新提示词、新版本或恢复旧执行器。涉及旧协议的恢复应返回明确不可继续状态，保留历史，用户可从原始来源发起新的分析或写作。实际活动任务的处理清单必须在发布切换前盘点，使用现有生命周期收尾，不直接删除运行记录。

## 3. 启动、种子与一次性清理

当前 `init_schema` 每次调用 `init_writing_method_schema`，后者末尾每次调用 `_seed_builtins`，使用 `INSERT OR IGNORE` 填回旧数据。见 [启动调用](../../backend/database/schema.py)、`backend/database/writing_method_schema.py`（已移除）。只删除旧数据而保留这条链路，重启后会重新出现旧内置技法。

需要退役的种子身份为：

- `builtin-method-clear-narrative`／`builtin-method-clear-narrative-v1`。
- `builtin-method-scene-tension`／`builtin-method-scene-tension-v1`。
- `builtin-scheme-balanced-narrative`／`builtin-scheme-balanced-narrative-v1`。

是否提供新结构的示例技法由新内容契约决定；不能通过旧种子代码或自动复制旧 Markdown 完成所谓新内置内容。

一次性退役采用已有 `app_schema_migrations` 机制，证据见 [迁移登记表](../../backend/database/schema.py)和[已存在的一次性迁移模式](../../backend/database/schema.py)。退役实现必须满足：

1. 独立、稳定的迁移 ID；该 ID 仅代表旧技法结构退役，不复用既有 Agent 或剧本迁移 ID。
2. 只读检查实际表、列及旧记录；新结构存在时不能被旧清理分支误判。
3. 进入写入维护边界，先停止旧写入与旧运行准入；只处理已列出的旧表及引用。
4. 数据库清理与完成标记采用事务；失败不能留下“已完成”标记。若涉及文件或恢复目录，则有独立可恢复的安装过程，不能声称文件系统与 SQLite 共享事务。
5. 重启、重复初始化和重复清理都为无操作；新生成的技法、方案、授权和文件不被删除。
6. 从旧备份恢复时，对恢复候选重新检查协议及旧结构，不能只相信被恢复数据库中的完成标记；完成退役后才向运行端开放。

已有 `book_style` 表的退役位于 [schema.py](../../backend/database/schema.py)。本轮保持其退出状态，不重新引入旧风格注入，也不把它扩展成清理小说其他表的理由。

## 4. 后端读写与候选接点

| 接点 | 已核对的旧行为 | 替换／保留规则 |
| --- | --- | --- |
| `backend/infrastructure/persistence/writing/sqlite_writing_method_repository.py`（已移除） | 直接把正文草稿、正文版本、方案成员写进六张表；搜索当前 active 发布版本 | 改为新文件存储及业务关联接口；不保留旧正文 fallback。数据库检索改为可重建索引，并使用新授权范围。 |
| `backend/infrastructure/persistence/writing/sqlite_writing_method_repository.py`（已移除）、`backend/infrastructure/persistence/writing/sqlite_writing_method_repository.py`（已移除） | 旧删除只检查方案／作品引用，未查询 Run，未引用时会物理删除版本行 | 不能照搬为新文件删除规则。业务删除统一归档隐藏；被引用版本保留；首版不自动回收历史版本。 |
| `backend/application/writing_method_service.py`（已移除）、`backend/domains/writing/methods.py`（已移除）、`backend/schemas/writing_methods.py`（已移除） | 单正文、`primary/technique`、单篇摘要、旧成员 ID；批量发布在 SQLite 事务内完成 | 用新技法文件、入口元信息、整体快照、方案引用契约替换；正文不再受旧类型驱动隐式常驻。不能把原数据库原子发布直接等同于新多文件保存。 |
| `backend/application/writing_method_candidates.py`（已移除） | 验证迁移 assessment，`render_skill` 后写入 primary 方法；用 `analysis_candidate` 的方案保存 `candidateMethodIds` | 退役候选方案作为单技法容器的机制；生成直接提交新技法草稿。旧分析保留，但旧候选创建、候选批量发布均关闭。 |
| [NovelAnalysis 配方](../../backend/domains/novel_analysis.py)、`backend/domains/writing_distillation.py`（已移除）、[共享提示词](../../backend/domains/novel_analysis_prompts.py) | 固定 `skill:draft/trial/revise/retrial/assess`、正文固定字段、试写与评估门槛 | 新生成规范、文件产物和正常复核替代旧强制链路；来源观察与证据检查保留；旧分析历史不重新执行配方。 |
| [分析 tools](../../backend/application/novel_analysis_tools.py)、[executor](../../backend/application/novel_analysis_executor.py)、[service](../../backend/application/novel_analysis_service.py)、[public_facts](../../backend/domains/novel_analysis_public_facts.py)、[artifacts](../../backend/application/novel_analysis_artifacts.py)、[profile](../../backend/application/novel_analysis_agent_profile.py) | 工具、执行、保存与显示共同认识旧 distillation 和 assessment，当前分析 schema 为 2 | 同步替换新写入与执行契约，保留只读历史投影；不能只改最后提示词或把所有 schema 2 分析整体删除。 |

现有 HTTP 面由 `backend/routers/writing_methods.py`（已移除） 注册，前缀／英文新命名由 P0 契约决定；以下旧请求体或旧操作语义必须退出：

- `/writing-methods` 及 `/{method_id}` 的单正文 CRUD、`draft`、`publish`、`copy`、`status`、`publish-batch`。
- `/writing-schemes` 及 `/{scheme_id}` 的旧成员 CRUD、`draft`、`publish`、`copy`、`status`。
- `/novel-analyses/{analysis_id}/writing-method-candidates`、`/writing-method-candidate-batches/{scheme_id}`、候选 `/publish`。
- `/books/{book_id}/writing-method-bindings` 及 `reorder`、`/{binding_id}/upgrade`、解绑。

路由可由新契约替换，但不能为了兼容旧客户端继续接受旧正文、旧版本 ID 或旧候选入库请求。历史分析 GET 与证据查看继续保留；[novel_sources.py](../../backend/routers/novel_sources.py) 的 review／publish／resume 等写操作需要识别活动协议，不能借历史查看重新开放旧技法生产。

## 5. 小说、来源删除与运行上下文

| 接点 | 当前行为与影响 | 退役要求 |
| --- | --- | --- |
| [SqliteNovelSourceRepository.delete_work](../../backend/infrastructure/persistence/sqlite_novel_source_repository.py) | 使用 `writing_methods.source_ref_json`、`draft_metadata_json` 和 `writing_schemes.source_ref_json` 阻止删除仍有证据引用的来源；之后会删除来源分析、任务与产物 | 先替换为新技法版本的来源引用检查，避免删表后来源操作报 SQL 错。绝不能把该函数作为技法清理器调用；其来源／分析删除范围远大于本任务。 |
| [delete_revision](../../backend/infrastructure/persistence/sqlite_novel_source_repository.py) | 检查 `continuation_bindings` 与 `novel_source_analyses` | 保留原作版本与续写保护，不因技法退役删除这些引用。 |
| [books.py](../../backend/routers/books.py) | 删除作品时直接清理 `book_writing_method_bindings` | 替换为新业务关系清理；本次退役不主动调用删除作品。 |
| [ContinuationService](../../backend/application/continuation_service.py)、[continuations router](../../backend/routers/continuations.py) | 创建续写时接收旧 `writing_method_bindings` 并写入旧仓储 | 仅替换技法选择／授权接点。保留续写创建、正史快照、小说和原作绑定的原子性及独立生命周期。 |
| [WritingAgentProfile](../../backend/application/writing_agent_profile.py)、[request_mapping](../../backend/application/request_mapping.py)、[ai schema](../../backend/schemas/ai.py)、[WritingDomainContext](../../backend/domains/writing/contracts.py) | `forceRevisionIds`／`excludeRevisionIds`、recommendation 标记和旧快照进入请求／Run | 换成新模式、手动选择、授权、固定版本；旧本轮覆盖不自动解释为新授权。队列、重试、恢复同样使用新请求快照。 |
| `backend/domains/writing/method_resolution.py`（已移除）、[context](../../backend/domains/writing/context.py) | primary 自动常驻、force 项必选、technique 关键词选择，并注入选中方法整篇正文 | 退役隐式常驻和整篇单正文契约；从入口按需访问文件。保留预算与真实上下文凭据能力，不由目录列表伪造“已使用”。 |
| [WritingMethodRetriever](../../backend/infrastructure/writing/retrieval.py) | 以 `writingMethodRecommendationRequested` 准入，再搜索全局 active 旧发布方法；目录不包含方案授权筛选 | 替换为本轮模式与已授权技法／方案范围。手动模式在后端禁止发现其他项目，不能只隐藏按钮。 |
| [工具目录](../../backend/domains/writing/tools/catalog.py)、[tool adapter](../../backend/infrastructure/writing/tools/tool_catalog.py)、[policy](../../backend/domains/writing/policies.py)、[显示名](../../backend/domains/writing/tools/display_names.py)、`backend/skills/searchWritingMethods/SKILL.md`（已移除） | 共同提供旧推荐入口与措辞 | 随新检索／文件读取能力统一调整，保留只读权限边界；工具名和请求契约不能一半新、一半旧。 |

旧 `writingMethodBindingSnapshot` 的保存和历史读取不是删除 Run 的理由。恢复准入与退役标记应由业务宿主处理，通用 Agent Core 不应新增对旧技法表的查询。

## 6. 前端与请求调用者

| 接点 | 需要替换的旧假设 | 保留要求 |
| --- | --- | --- |
| [WritingMethodsPage](../../src/WritingMethodsPage/index.tsx) | 单正文编辑、`primary/technique` 分类、候选方案批量发布、版本只展示 `markdown_body`、旧物理删除按钮 | 改为入口与辅助文件编辑、整体版本和归档语义；证据入口与草稿保存能力继续提供。 |
| [WritingMethodBindingsPanel](../../src/Workspace/WritingMethodBindingsPanel.tsx) | 旧绑定、排序、升级和版本选择 | 对齐新授权／选择契约；不能把旧绑定列表自动变成自动选用白名单。 |
| [BookConversationExtensions](../../src/Workspace/AiPanel/BookConversationExtensions.tsx)、`src/Workspace/AiPanel/writingMethodOverrides.ts`（已移除）、[AiPanel](../../src/Workspace/AiPanel/index.tsx) | 推荐动作、主方法标签、强制／排除循环 | 换为自动／手动模式及始终可用的手动添加；更新实际使用来源展示。 |
| [chatQueue](../../src/Workspace/AiPanel/hooks/chatQueue.ts)、[useChatSubmit](../../src/Workspace/AiPanel/hooks/useChatSubmit.ts) | 入队、提交和重试复制旧 overrides | 队列快照包含新模式、明确版本和手动上传身份；不得提交时才按最新 UI 状态重解释已排队请求。 |
| [NovelSourcesPage](../../src/NovelSourcesPage/index.tsx) | 默认提示要求迁移效果；`createMethodCandidates` 检查 assessment；旧结果带保存候选操作 | 新分析用新生成链路；旧分析只读，不再候选入库；保留原始来源与分析证据查看、规范弹窗高度。 |
| [types.ts](../../src/types.ts)、[backendApi](../../src/services/backendApi.ts)、[services](../../src/services/index.ts) | `WritingMethod*`、`WritingScheme*`、旧绑定、旧候选、单正文字段和 API | 与新 schema 同步替换；不得只改界面名称而继续发送旧载荷。 |
| [App](../../src/App.tsx)、[WorkspaceUtilityPanel](../../src/Workspace/WorkspaceUtilityPanel.tsx)、[utilityPanelTypes](../../src/Workspace/utilityPanelTypes.ts) | 技法库及作品内入口注册 | 保留产品入口，指向同一新能力；不删除整个小说工作区或其他工具页。 |

## 7. 备份恢复与禁止复用的清理工具

当前完整备份格式是 `purrtypos.full-backup/v1`，只收录 `purrtypos.db` 与 `memory-component-v1/`；`_safe_relative_name` 还会拒绝其他目录。见 [build_project_backup](../../backend/application/project_backup.py)和[路径白名单](../../backend/application/project_backup.py)。新技法文件不自动包含在现有备份内。

当前恢复由 [files.import_database](../../backend/routers/files.py)、[_replace_project_data](../../backend/routers/files.py)、[DatabaseConnection.import_from_buffer](../../backend/database/connection.py) 完成。后者替换活动数据库并重开连接，没有执行 `init_schema`。因此，不能只依赖“重启时清理一次”：恢复后到重启前也不能让旧候选、旧搜索、旧正文读取重新变得可用。

恢复规则：

- 旧完整备份可以承接其小说、来源、正史、Memory 与历史记录；在候选恢复区执行旧技法退役，不把旧正文或绑定导入新结构。
- 新备份覆盖数据库业务状态、技法／方案文件、引用所需版本及按新契约保留的手动上传文件；缓存可以重建，授权和绑定不能当缓存丢弃。
- 备份格式和目录清单需要体现新的内容范围；明确校验、安装、失败回滚和重启边界，不接受“数据库成功、文件缺失”的恢复结果。
- 保留当前凭据脱敏及本机凭据合并、Memory 完整性检查、路径与摘要校验。技法改造不重新授权知识库或恢复云端凭据。
- 恢复完成后旧 schema 不可执行，新数据不被再次退役；索引重建不能让已归档／已删除对象重新出现在活动列表。

**不得复用 `scripts/clear-legacy-novel-analysis.py`（已移除） 作为本次清理器。** 它按旧分析 schema 选择 `novel_source_analysis` 与 unit Run，扩展子 Run，再删除任务、产物、分析、证据及运行关联；这与本次“保留分析历史和整个 Run”的范围冲突。它还使用旧绑定表不存在的 `revision_id` 列，当前表实际为 `method_revision_id`／`scheme_revision_id`。本轮不运行、不顺手修复或扩展该脚本；旧分析清理工具在新业务入口与文档操作指引中退出。

## 8. 现有测试调用者与新验收

这些测试是旧行为证据，不能全部原样保留成新产品要求；也不能为通过测试而恢复旧表和旧执行接口。

| 已核对测试 | 旧断言／需要调整的范围 | 必须继续验证的行为 |
| --- | --- | --- |
| `backend/tests/test_writing_methods.py`（已移除） | 内置种子、单正文、类型、旧 HTTP、旧绑定、无引用时删除版本 | 新初始化幂等、草稿并发、整体不可变版本、归档和引用保护；不复活 book_style。 |
| `backend/tests/test_writing_method_candidates.py`（已移除）、`backend/tests/test_writing_distillation.py`（已移除） | 自动候选方案、assessment 门槛、试写覆盖每步、primary 无关键词注入 | 新生成不自动绑定、不强制方案／迁移试写，单／多文件完整保存；删除技法不删除来源证据。 |
| `backend/tests/test_writing_method_runtime.py`（已移除）、[test_writing_retrieval.py](../../backend/tests/test_writing_retrieval.py) | 强制／排除、主方法常驻、旧推荐标记、整篇预算、旧全局目录 | 自动／手动准入、授权搜索、入口先读、按需文件、固定版本、实际输入凭据和预算。 |
| `test_writing_domain_adapter.py`、`test_writing_tool_infrastructure.py`、`test_writing_tool_runtime.py` | 旧检索工具注册、显示与领域合同 | 新工具与宿主权限一致，不把历史内容当授权。 |
| [test_novel_analysis.py](../../backend/tests/test_novel_analysis.py)、[test_novel_analysis_conversation.py](../../backend/tests/test_novel_analysis_conversation.py) | 旧配方、schema、固定模型调用次数、旧最终产物 | 持久任务、取消／恢复、来源证据及新文件产物；不同协议的历史只读与恢复拒绝。 |
| [test_novel_sources.py](../../backend/tests/test_novel_sources.py)、[test_continuations.py](../../backend/tests/test_continuations.py) | 来源引用依赖旧技法 SQL；续写创建旧方法绑定 | 新来源关联保护、正史冻结、续写不被技法清理删除；实际删除来源的旧测试不能当作技法退役模板。 |
| [test_project_backup.py](../../backend/tests/test_project_backup.py)、[test_database_web_import.py](../../backend/tests/test_database_web_import.py) | 旧数据库包含方法正文／版本；完整备份仅有数据库和 Memory | 新文件＋业务状态恢复一致；旧备份不复活执行路径，恢复前后无技法之外的数据损失。 |
| `backend/tests/test_legacy_novel_analysis_cleanup.py`（已移除） | 明确断言删除旧 analysis Run | 不作为本次清理验收；改由新范围测试验证历史 Run、分析、原文和正史保持不变。 |
| `src/Workspace/AiPanel/writingMethodOverrides.test.ts`（已移除）、[useChatSubmit.behavior.test.mjs](../../src/Workspace/AiPanel/hooks/useChatSubmit.behavior.test.mjs) | 三态 overrides、队列／请求传递 | 新模式与手动选择的请求快照、排队和重试语义。 |

退役必须另有以下行为验收，并且在临时数据库／目录中执行：

- [ ] 旧六表混合用户、内置和来源候选数据，只清理旧技法及绑定，不转成新正文。
- [ ] 清理前后小说正文、来源原文、正史、Memory、历史分析、Run 及事件／产物的保留范围一致；旧分析仍可只读查看。
- [ ] 已带完成标记后重启、重复初始化不再清理，新建技法和业务状态不受影响。
- [ ] 模拟中断后可恢复；未完成的清理不能留下错误完成标记。
- [ ] 恢复旧备份后、以及恢复到重启之间，均不能通过旧路由、候选服务、旧 Run 或工具执行旧技法。
- [ ] 新归档隐藏对象；被方案、小说、Run 引用的版本文件继续存在；首版不自动回收历史版本。
- [ ] 新完整备份恢复后文件、版本和引用一致；Memory 与凭据保护保持有效。
- [ ] 对活动协议执行路径检索旧表、旧类型、旧候选和迁移试写要求，剩余命中只能是明确的退役器、历史只读展示或历史测试资料。

## 9. 实施前仍需用真实环境确认的事项

1. 实际本地数据库是否存在本清单之外的历史表变体、手工改动或备份格式；本轮只核对了当前仓库定义，不证明所有旧安装的 schema 相同。
2. 发布切换时活动、暂停和可恢复 Run 的数量、持有的旧协议以及收尾状态；不能从源码推断当前运行情况。
3. 当前未被正式保存的草稿、上传文件和其存储位置；新临时文件生命周期按 P0 契约落定，不对未知目录做递归删除。
4. 新 schema 与备份格式的准确版本、最终表名和退役迁移 ID；本文只固定旧范围与幂等要求，不虚构已经实现的接口或命名。

以上未知项需要在实施接近相应边界时核实；清理范围限定为已列出的旧技法数据。本轮的完成条件是形成可追溯清单，不代表退役或备份升级已经完成。

## 10. 实施状态

新入口是 `backend/database/writing_technique_retirement.py` 与 `writing_technique_schema.py`。退役只处理核对列结构后的六张旧表，并持久化完成标记；新备份格式为 `purrtypos.full-backup/v2`，包含 writing-library，旧备份在隔离候选库完成退役后才能启用。数据库与目录恢复失败均回滚，保留小说、来源、Memory、历史分析和 Run。

已只读盘点正式库：旧方法/版本各 2 条、方案/版本各 1 条、成员 2 条、绑定 0 条；当时无活动 Run。该盘点不授权清空全库，也不代表已执行清理。确定性测试与剩余发布阻塞见[实施验收记录](../validation/2026-09-07-writing-techniques-implementation.md)。

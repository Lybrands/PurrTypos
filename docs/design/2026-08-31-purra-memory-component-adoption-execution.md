# PurrA 记忆组件接入执行记录

日期：2026-08-31，最终更新：2026-09-01。对应 [实施计划](./2026-08-31-purra-memory-component-adoption-plan.md)。

## 当前结论

**源码适配与离线集成验证已经完成。** PurrTypos 的管理 API、Agent 工具、自动沉淀、Retriever、主动上下文和内联编辑已经切到本地 PurrA 0.5.0 的唯一记忆组件链路；Embedding/RAG 属于本次适配范围，受管检索、整条上下文组装、证据持久化与发送前复验均已接入。旧 `memory_items` 及其 FTS、旧通用 service/repository、旧镜像与旧 ID 分支已从当前生产代码删除，没有运行时兼容层或旧实现 fallback。

这不等于真实环境已经切换完成。尚未执行真实旧数据盘点/一次性转存、收费 LLM/Embedding 中文质量验证、运行中用户服务重启和浏览器/Electron 完整应用 E2E；Planner 协议流仍按用户要求暂缓。现有用户服务和真实数据均未触碰。

用户已授权按计划完成源码工作。真实数据转存、付费调用、运行中服务切换和发布保持原计划的独立边界；原工作树的其他改动保留。

## 基线

- 两仓源码及未提交变更、逐文件 SHA256、git status 保存在 `/tmp/purrtypos-memory-adoption-20260831-212410`；PurrTypos HEAD 为 `b23a97f`，PurrA HEAD 为 `06f712a`。本地开发契约为 0.5.0，未发布。
- 修改前：PurrTypos 定向测试 96 项通过；Python 组件测试 107 项通过；TypeScript 组件 check 通过。
- 修改前全量 `npm run check:agent-refactor`：后端 1762 passed / 26 failed。完整日志为上述目录的 `purrtypos-gate-before.log`，失败名称见下文。
- 26 项的直接现象包括 Planner 等待/模型调用时序、接口地址未配置、测试模型输出耗尽、工具调用不符合规划输出、SSE 提前结束以及依赖流程未完成。**并未逐项证明所有失败只有同一根因**，后续必须按名称和堆栈比较，不能概括为“都是 Planner”。

## 已实现的契约

| 计划项 | 当前行为 | 不承诺的内容 |
| --- | --- | --- |
| G1 metadata | 最多 32 个 JSON 标量字段；拒绝保留字段、嵌套结构、非有限数和不安全整数；返回只读数据 | metadata 不能授予作品权限、覆盖 source/epoch/evidence |
| G1 管理列表 | `MemoryPage(items,next,epoch)`；状态、来源 ID、字面文本和 metadata 筛选；限制扫描数量 | 不用向量搜索冒充枚举；空页有 next 时不能显示“已全部加载”；多页不是数据库快照 |
| G2 写入与状态 | add 直接进入 active/pending/disabled；extract 固定 pending；真实创建/更新时间、reason；annotate 替换 metadata；状态/metadata 修改不重算向量 | 组件不决定何种故事事实应当生效；来源权限和用户批准仍由业务判定 |
| G3 选择/组装 | select 读取当前有效版本；公共 assembler 为 Agent 与非 Run 调用共享整条预算/证据逻辑，返回 included/deferred/missing | 没有虚构 Run；不把截断记录标为完整，也不替代发送前复验 |
| G4 显式关联 | link 使用双方所见版本，复用已有操作账本；links 显式分页，提供 valid；不修改正文/可见性 | 普通关联不是 resolve；不自动作相似度判断，不新增图数据库 |

幂等仍使用既有持久 key/fingerprint，控制事务保留 expected version、namespace epoch、未知写入者隔离和来源撤回校验。没有新建 memory_ops 或兼容层。set_state/annotate/resolve/link 不调用 LLM/Embedding，回执明确为零调用；SDK 正文操作在非受管模式下仍为未知用量。

SDK 元数据保持最近一次已验证正文写入的快照，journal 保存当前控制视图。读取必须经过组件，不能回去按 SDK 的旧状态字段筛选。Python 的 JSON 校验额外阻止 `true` 被 `1` 冒充，以及必需字段被删除；TypeScript 用标准库深比较检查相同约束。

**关联的明确行为变化：** 关联绑定具体版本；任何版本变化（包括置顶等 metadata 编辑）都会使旧关联 `valid=false`。历史关联仍可审计，但使用前需针对新版本明确确认。当前 UI 显示关联有效性，不把失效关系当作当前关系。SDK history 不包含 journal 控制操作；当前状态/原因和持久操作回执分别展示，不能虚构 SDK 历史。

`list` 只支持新分页返回，没有保留数组返回别名。当前组件尚未发布；本批没有为旧实验 journal 添加启动迁移。真实数据处理仍按 P6 在副本上演练。

## G5：保留实际领域职责

已检查 `backend/application/memory_reranking.py`、`domains/writing/memory_reranking.py` 与语义/Story Memory 上下文调用者。其判断是“遗漏哪条事实会破坏人物、关系、时间线或剧情连续性”，输入包括 storyKinds、entityRefs、chapterIds；调用已使用真实 Run 的 AgentModelTaskRunner。

因此不把这段判断整体下放给通用组件，也不另写一套通用排序引擎。宿主已保留领域 prompt/选择策略，使用组件 select 验证一般记忆的当前记录和版本，并用公共 assembler 注入；Story Memory 权威规则保留。已有测试核实只选择宿主候选、拒绝未知 ID；这不代表真实中文召回质量已通过。未受管 SDK reranker 继续关闭。

## G6：Core 公共发送前证据校验已闭环

PurrA Python/TypeScript 已增加公共 `ModelInputEvidenceValidator`，并把 Retriever 工具产生的外部上下文回执带入 canonical 工具结果、Run 证据账本和检查点。实际模型发送边界会合并消息回执与当前 Run 的持久回执，在每次 Provider attempt 前调用验证器；模型任务、上下文压缩、委派和恢复均使用同一接点。组件 owner 仍由宿主通过公共接口装配，Core 不解析 Mem0 私有 ID。

原缺口复现仍保留为修改前证据。修改后的真实组件探针使用本地 `Mem0Memory`、真实 journal/检查点恢复和替代 Provider：先保存回执、撤回来源，再恢复账本并调用已配置验证器的 Core，结果为：

```json
{"configuredCoreRejectsRevokedCheckpoint":true,"providerCalls":0,"restoredReceiptCount":1,"externalProviderCalls":0}
```

Python 新用例覆盖 Retriever 后续调用、检查点恢复和压缩中的嵌套模型任务；TypeScript 覆盖 Retriever、ModelTaskRunner、压缩继承与恢复。Planner 协议流未修改。该证据证明的是确定性发送门禁与真实本地组件存储，不等于真实 Provider 或 PurrTypos 完整 Agent E2E；宿主 scope/owner 装配现已通过确定性集成测试。

## 验证证据

日志均保存在上述基线目录；下面保留前置批次实际做过的检查，最新结果见后文“修改后验证”。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Python 组件完整确定性测试 | 119 passed | `purra-python-final.log` |
| TypeScript build / 单测 / 消费者类型 | 117 passed，构建和类型通过 | `purra-ts-final.log` |
| 共享契约 | JSON fixture 覆盖中文字段、AND/OR、null/缺字段、布尔/数字区别 | `integrations/mem0/fixtures/memory.json` 的 management 部分 |
| Python 真实 SDK + 本地 Qdrant/SQLite | 通过；替代 Provider | `python-sdk-final.log` |
| 安装后的 Python 双包 + 真实 SDK | 通过；从临时安装目录加载 | `python-package-install.log`、`python-installed-sdk.log` |
| 打包后的 TS 组件 + 真实 SDK/SQLite | 通过；从临时 node_modules 加载 | `ts-package-build.log`、`ts-installed-sdk.log` |
| PurrTypos 定向回归（原 96 项加领域重排 2 项） | 98 passed | `purrtypos-focused-after.log` |
| Core 发送前旧证据复现 | 修改前失败已复现；修改后真实组件恢复探针拒绝且 Provider 0 调用 | `recovery-gap.log` 及本轮 G6 探针输出 |
| PurrA Python 完整门禁 | 通过 | Core、公共导出、工具/检查点/压缩证据测试通过 |
| PurrA TypeScript 完整门禁 | 239 passed，build/type tests 通过 | Core、ModelTaskRunner、委派及恢复证据测试通过 |
| Python/TypeScript Mem0 组件 | 119 passed / 117 passed | 固定 0.5.0 本地源码 |

SDK 检查包含真实存储、重启、幂等、待审、metadata 控制操作、来源撤回、更正、预算和评审；每种语言另跑 9 个替代 Provider 的评估流程用例。它们验证链路，**不验证真实模型语义质量**。Python SDK 提示未装可选 spaCy/fastembed，BM25 未启用；没有为消除提示而增加无关依赖。预算拒绝用例的错误输出是预期行为。

## P2：本地依赖与宿主资源接入

PurrTypos 现在分别声明同级 `purra` 与 `purra-mem0[managed]` 0.5.0。分发脚本逐项解析本地源码、构建普通 wheel，并连同 `requirements-runtime.txt` 安装实际运行依赖；已移除 `--no-deps`。运行时约束把 Core/Mem0 共用的 OpenAI SDK 限定在已支持的 2.x 主版本，避免单独解析 Mem0 时漂移到未验证的 3.x。

新增 lifespan 所有的 `MemoryComponentResource`：在导入 Mem0 SDK 前固定 telemetry 和项目数据目录，一个 SDK store 通过异步上下文管理器借出不可变 user/project/agent scope。每个 scope 必须显式传 `MemoryBudget` 和受管 completion；Embedding 只使用设置页明确保存的 OpenAI 兼容 endpoint、模型、维度和凭据，关闭隐藏重试并记录 Provider 返回的真实 token 用量。关闭顺序为 Agent Core、scope drain/journal、Mem0 history/Qdrant、Embedding 客户端、宿主数据库。

维度写入 store manifest；配置变化不会静默混写旧集合，而以 `memory_embedding_dimensions_changed` 拒绝。未配置时不构造 SDK；配置/存储错误以脱敏 code 进入 `/health.memory`，关闭后拒绝新 scope。设置页另要求显式选择提炼/评审模型，不回退到第一个聊天模型。

确定性验证包括 19 项资源/配置/包边界测试和 TypeScript typecheck。真实 `mem0ai==2.0.19` + 本地 Qdrant/SQLite 在临时目录完成写入、关闭、重启、读取和检索。分发目录使用 `python -S` 且 cwd 位于 `/tmp` 验证，Core、purra-mem0、Mem0、LangChain、OpenAI、FastAPI、aiosqlite 均从 `build-resources/backend` 加载；没有借用开发 venv 的 site-packages。未调用外部 Provider。

未执行收费 Provider、浏览器/Electron 完整应用验收、真实数据迁移或运行中服务切换。

## P3–P5：宿主写入、读取与证据闭环

- `MemoryApplicationService` 是通用记忆的唯一应用边界。管理路由、Agent 写工具、来源提炼/交付、组件检索和上下文组装都调用它；工具使用 Core 提供的可信 `ToolCall.id` 生成幂等操作，不让模型选择 key、book、source 或 version 权威。
- 来源保存与 `memory_source_heads`/`memory_source_deliveries` 在同一业务事务记录，组件写入在事务后交付；章节、人物、设定、背景、大纲、会话、历史恢复、章节删除和整书删除都有稳定撤回路径。未配置组件时业务正文仍提交，交付明确失败并可恢复，不回退旧记忆实现。
- `WritingMemoryRetriever` 使用 PurrA `RetrieverTool` 和组件向量检索，Run ID 来自 Core 的 `AgentModelTaskRunner.run_id`。自动上下文与内联编辑共用组件整条选择/组装，并共用显式灵感/伏笔的预算函数。
- MemoryCenter、MemoryModal、选择状态、前端 service/type 和后端 DTO 使用组件字符串 ID、真实 version/state/metadata。历史、关联、置顶、审核/决议、禁用/归档、删除和稳定错误码已接通；旧 deduped/伪使用投影已移除。
- Story Memory 仅保留人物/关系/时间线/连续性领域状态账本及原子事务；没有建立第二份向量镜像。领域重排失败时返回空选择并报告失败，不采用确定性候选 fallback。
- Core 公共 `ModelInputEvidenceValidator` 覆盖 Retriever 工具结果、首次和后续 Provider 发送、检查点恢复、压缩和模型任务；外层预算截断同时清除语义和 Story Memory 回执，撤回/过期/版本或 epoch 变化会在发送前拒绝。

## P6：完整备份、删旧与分发

- `/database/export` 和 `/database/import` 统一使用标准 ZIP `.purrbackup`，包含脱敏后的宿主数据库、组件 manifest/history/vector store，以及已产生的 journal。manifest 记录文件大小、SHA256 和 Embedding 维度；导入拒绝路径穿越、重复/额外/缺失文件、超限归档、坏 SQLite、哈希或维度不一致。
- 备份不导出聊天模型或 Embedding API key；恢复只按相同模型身份重新绑定本设备现有凭据。数据库与组件先在暂存目录整体校验，再成套切换并留下离线回退副本；旧 lifespan 资源进入 restart-required 状态，不能继续混用恢复前 store。
- Electron 不再停服务后直接复制单个 `.db`，而是把同一备份包提交给后端校验，成功后按返回值重启。浏览器走同一接口，并明确提示恢复后需要重启后端。
- schema 已删除 `memory_items`、`memory_links`、对应 FTS 与启动回填；`long_term_memory_service.py`、`memory_intelligence_service.py`、重复的 `memory_service.py`、两个 SQLite 通用记忆仓储及对应测试已删除。灵感/伏笔路由与 Agent 工具共用唯一的、按 book 约束的 `SqliteWritingSourceRepository`；`ai_memories_fts` 只服务于显式业务来源搜索，不是通用长期记忆实现。生产扫描只剩当前组件 Builder/links API 和明确保留的 Story Memory 仓储。
- `RepositoryWritingContextSource` 已移到应用层，领域目录不再反向依赖 application。Story Memory 仓储加入外层业务事务时使用嵌套 savepoint，独立调用时仍拥有线性化提交边界。

## 修改后验证

| 检查 | 结果 |
| --- | --- |
| PurrA Python Core 全量 | 通过，退出码 0 |
| PurrA TypeScript Core 全量 | 239 passed；build、类型和示例通过 |
| purra-mem0 Python / TypeScript | 119 passed / 117 passed；构建和类型通过 |
| PurrTypos 记忆/来源/Story Memory/备份定向矩阵 | 相关定向测试全部通过；完整备份/恢复与坏包检查 13 项通过 |
| PurrTypos 类型/前端 | TypeScript 类型通过；430 项前端单测通过；Electron 备份 IPC 2 项通过 |
| PurrTypos 全量后端 | 1744 passed / 26 failed；记忆接入新增失败清零，失败名称与修改前 Planner/会话流程基线完全相同 |
| Web/后端分发 | `npm run build:web` 通过；cwd=/tmp、`python -S` 验证 purra 与 purra-mem0 0.5.0 从 `build-resources/backend` 导入 |
| 静态检查 | 两仓 `git diff --check` 通过；当前生产代码无旧通用记忆 service/repository/table 引用 |

这些是确定性和本地 SDK 证据。真实 Provider、真实用户数据转换、运行中服务加载和完整 UI 操作仍是未验证项，不能从上述测试推导为已经完成。

## 修改前全量失败名称（26 项）

- `backend/tests/test_agent_composition.py::test_composed_route_uses_complete_purra`
- `backend/tests/test_agent_composition.py::test_writing_read_evidence_replans_only_unfinished_semantic_steps`
- `backend/tests/test_agent_composition.py::test_composed_approval_is_resolved_through_existing_http_contract`
- `backend/tests/test_agent_planning.py::test_every_product_planner_exposes_waiting_before_provider_returns[writing]`
- `backend/tests/test_agent_planning.py::test_every_product_planner_exposes_waiting_before_provider_returns[novel_analysis]`
- `backend/tests/test_agent_planning.py::test_every_product_planner_exposes_waiting_before_provider_returns[screenplay]`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_root_plan_replays_without_private_recipe_progress`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_run_streams_same_run_delegation_lifecycle`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_planning_invalid_falls_back_to_model_only_sse_snapshot`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_unfinished_planned_tool_has_blocked_asgi_sse_snapshot`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_read_continuation_keeps_writing_evidence_policy`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_complete_selected_outline_repairs_redundant_read_plan`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_reject_uses_real_http_endpoint_and_replay_fails`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_disconnect_detaches_without_canceling_pending_approval`
- `backend/tests/test_ai_composed_sse_wire_contract.py::test_composed_send_side_disconnect_detaches_pending_run`
- `backend/tests/test_novel_analysis_conversation.py::test_durable_analysis_binds_unit_runs_and_recovers_public_process[False]`
- `backend/tests/test_novel_analysis_conversation.py::test_durable_analysis_binds_unit_runs_and_recovers_public_process[True]`
- `backend/tests/test_screenplay_agent_durable_service.py::test_service_cancel_settles_root_task_operation_and_turn`
- `backend/tests/test_screenplay_agent_durable_service.py::test_screenplay_answer_turn_does_not_create_a_durable_task`
- `backend/tests/test_screenplay_agent_durable_service.py::test_screenplay_formal_turn_records_tool_child_run_under_root`
- `backend/tests/test_screenplay_agent_durable_service.py::test_checkpoint_scope_change_pauses_then_explicit_continuation_replans`
- `backend/tests/test_screenplay_agent_durable_service.py::test_formal_root_retries_the_complete_business_projection_transaction`
- `backend/tests/test_screenplay_agent_durable_service.py::test_screenplay_formal_root_settles_non_success_terminal_states[<lambda>-paused-paused-canceled]`
- `backend/tests/test_screenplay_agent_durable_service.py::test_screenplay_formal_root_settles_non_success_terminal_states[<lambda>-failed-failed-failed]`
- `backend/tests/test_writing_chat_request_routes.py::test_replan_repairs_completed_step_rewrites_in_one_root`
- `backend/tests/test_writing_chat_request_routes.py::test_agent_edit_persists_candidate_receipt_without_applying_article`

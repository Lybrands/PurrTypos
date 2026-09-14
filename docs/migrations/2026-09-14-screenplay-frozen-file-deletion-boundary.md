# Screenplay 冻结文件删除边界

日期：2026-09-14

## 已确认事实

- 冻结清单中有 63 个路径属于旧 Screenplay 实现。
- 生产组合根、Screenplay HTTP 新建/回放入口均已不再安装旧 runtime。
- 启动流程现只恢复 `ScreenplayReplacementRecoveryService`；旧
  `SqliteScreenplayAgentRepository.recover_after_restart()` 已停止调用。
- replacement 对 `screenplay_agent_turns.planner_run_id` 的使用仅限 SQL
  存储边界；所有读取结果立即别名为 `root_run_id`，应用映射不再传播旧名。
- 历史终态 Turn 由 replacement query 读取，不依赖旧 profile、executor 或
  projector。
- replacement 与公开请求 schema 已改用
  `agents.screenplay.contracts.ScreenplayStageCommand`；现行 action、scope 和
  command 的 wire 映射及 Host 校验均不再导入冻结 domain。旧 Planner Intent
  语义没有复制到 replacement。
- 公开项目 API 已改用 `agents.screenplay.project_service`；项目、会话、Working
  Copy、Revision、审核、定稿与 PDF 导出不再依赖冻结
  `application.screenplay_v2_service`。
- 开发工具诊断已改用
  `agents.screenplay.historical_tool_presentation`；它只为 13 种冻结 read tool
  重建 `displayName`，不再导入旧 tool catalog，也不包含执行或写入能力。

## 仍需迁移的生产依赖

| 冻结模块 | 当前生产调用者 | 结论 |
| --- | --- | --- |
| `domains.screenplay_agent` | 旧 runtime、旧 persistence 与专项测试 | 现行 StageCommand 依赖已解除；整个 package 仍需与旧 runtime/repository/finalizer 同批删除，不能因本次抽取直接删目录。 |
| `application.screenplay_v2_service` | 旧 profile 与 legacy 专项测试 | 17 个仍在公开 API 使用的方法已原样迁入 `agents.screenplay.project_service`，生产路由已切换；旧文件随 legacy runtime 测试批次删除。 |
| `application.screenplay_tool_presentation` | 冻结旧 SSE 与 legacy 专项测试 | 开发诊断已迁入最小只读 adapter；旧模块随冻结 stream/runtime 同批删除。 |
| `domains.screenplay_agent.operation`、`application.screenplay_part_artifacts` | 旧 operation repository/finalizer | 已无生产 runtime 根调用；与旧 repository/finalizer 及其专属测试作为同一删除批次处理。 |

`infrastructure.persistence.sqlite_screenplay_agent_repository`、
`sqlite_screenplay_operation_repository` 和
`sqlite_screenplay_operation_finalizer` 不在原冻结清单中，但其剩余职责属于旧
runtime。启动恢复调用移除后，它们只被旧实现或旧专项测试引用，不应作为兼容层长期保留。

## 删除顺序

1. ~~抽出 `ScreenplayStageCommand` 现行合同并迁移 replacement 与请求 schema。~~
   已完成，并有架构守卫禁止 replacement/schema 重新导入冻结 package。
2. ~~迁移项目/Revision 服务和历史工具标签只读 adapter。~~ 已完成；HTTP、事务
   与历史诊断标签合同保持不变，生产路由不再依赖这两个冻结 application 模块。
3. 删除旧 profile、service、executor、planner/checkpoint、tool calling、旧
   projector、旧 operation repository/finalizer、旧 skill/tool catalog，以及测试专用
   legacy composition 和专属旧测试。
4. 更新冻结清单，只保留仍承担其他 Agent 历史边界的文件；执行 backend、frontend、
   Electron、历史 fixture、最终包与端口清理门禁。

不得为了删除文件保留 import shim、旧新双写或“新实现失败后回退旧实现”。

## 删除批次前最终调用图

以冻结清单中的 63 个 Screenplay 路径为目标重新做 Python import 调用图后，
未发现从现行生产入口进入冻结 runtime 的路径。冻结集合之外仅剩三个生产源码文件
导入冻结类型：

- `sqlite_screenplay_agent_repository.py`；
- `sqlite_screenplay_operation_repository.py`；
- `sqlite_screenplay_operation_finalizer.py`。

这三个文件没有现行 router、composition、lifespan 或 `agents.screenplay` 调用者；
调用者只来自冻结 profile/projector/runtime 和 legacy 专项测试。它们不是需要迁移的
当前 persistence，而是冻结执行岛漏出清单的三块组成部分。因此首个源码删除批次应当
同时删除 **63 个冻结路径 + 3 个旧 repository/finalizer**。若只删冻结清单，反而会
留下无法导入的残片。

## 数据库与历史回放保留边界

源码删除不授权数据库迁移。下列物理表仍由 replacement 或公开项目能力直接使用，
必须保留其 schema、migration、清理事务与现有历史行：

- `screenplay_agent_turns`、`screenplay_agent_operations`；
- `screenplay_projects`、会话、Working Copy、Revision、Part、Source Reference 与 Head；
- `screenplay_operation_access_receipts`、`screenplay_operation_usage_receipts`；
- 通用 Run、Run Event、Output Journal、Long Task 与 Artifact 表。

`screenplay_agent_turns.planner_run_id` 仍是 replacement 使用的物理存储列；当前查询在
SQL 边界把它别名为 `root_run_id`。本删除批次不重命名或删除该列。历史终态 Turn 的
可读性由 `agents.screenplay.conversation_query` 和通用 journal 保证，不依赖旧
profile、executor、repository 或 projector；现有 fixture 已覆盖
`legacy-frozen-2026-09-12` Turn 的 completed 状态和旧 Assistant 文本。

同时保留对 legacy implementation identity 的识别。它用于区分历史数据所有权，
不等于保留 legacy 创建、恢复或执行能力。

## 测试处置清单

删除批次必须与测试处置同一提交完成，避免用失效测试迫使旧实现继续存在：

- 直接删除 legacy-only 大套件：`test_screenplay_agent_durable_service.py`、
  `test_screenplay_agent_rewrite.py`、`test_screenplay_tool_catalog.py`、
  `test_screenplay_prompt_contracts.py`、`test_screenplay_read_cache.py`，以及
  `tests/support/legacy_screenplay_composition.py`；
- 从混合测试中移除或改写仅验证旧 Screenplay 实现的用例：
  `test_agent_event_stream.py`、`test_planning_mode_mapping.py`、
  `test_application_planning_constraints.py`、`test_agent_conversation_input.py`、
  `test_shared_agent_context.py`、`test_purra_incident_replay.py`；
- `test_screenplay_v2_routes.py` 不再通过冻结 `ScreenplayToolQuery` 准备数据，改用
  当前 project/revision persistence fixture；
- 保留 replacement、公开 API、历史终态回放、implementation routing、schema/
  migration、truncate/recovery 和诊断历史标签测试。

## 预检结论

首批删除边界已经闭合：它是一个不可被现行生产根到达的旧执行岛，删除时不需要新增
兼容层。下一开发项可以执行上述源码与测试删除，并以静态 import 扫描、backend
replacement/API/schema 回归、frontend/Electron/package 检查和端口清理作为验收。
本预检本身没有删除源码、历史数据或数据库结构。

## Batch 1 执行结果

2026-09-14 已按上述边界完成首批删除：

- 删除 63 个冻结 Screenplay 路径和 3 个遗留 repository/finalizer；
- 删除 7 个 legacy-only 测试/支持路径，拆分其余混合测试；源码与测试合计
  删除 73 个路径；
- 冻结清单从 194 个文件收敛为 131 个，仅继续保护 Writing 与 Novel
  Analysis 旧实现；
- `test:screenplay-acceptance` 已只引用 replacement/API/schema 测试，不再引用
  已删除的旧大套件；
- 现行源码、`build-resources/backend` 和实际 `.app` 均不存在旧 domain、
  infrastructure、service 或 repository 路径。

保留项没有变化：所有现行共用表、历史行、`planner_run_id` 物理列、legacy
implementation identity、历史终态 Turn 查询和历史工具标签只读 adapter 均未删除。
扩大 Screenplay/路由/恢复/历史回放测试、89 项 Screenplay acceptance、TypeScript、
38 项 Electron 单测、应用深度签名和 DMG 校验均通过。该构建是本地 ad-hoc 签名、
未公证产物，不是发布证据。

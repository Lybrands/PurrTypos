## Task 9: 清理旧 Planner Run 代码并完成 canonical replay 验收

Status: IN PROGRESS

### Objective

删除独立 Screenplay Planner Run 的最后兼容实现，让所有生产应用语义与前端状态只使用
`rootRunId`。SQLite 的 `planner_run_id` 物理列保留用于向前兼容，旧客户端字段只允许在显式
只读兼容反序列化边界出现。完整 live/replay fixture 必须证明 Root plan、私有 durable
progress、AI Child、checkpoint revision、Candidate 与 Root final 使用同一 canonical reducer。

### Audit findings

- `screenplay_agent_planner.py` 仅剩 `SqliteScreenplayTaskResolver` re-export；生产引用已经为零，
  只有 rewrite test 与 boundary allow-list 仍引用旧模块。
- 后端新 snapshot 已同时输出 `rootRunId` 与 `plannerRunId`；repository/service/finalizer 中仍有
  多个参数或局部变量沿用 planner 命名，需要收缩到 SQLite column adapter。
- 前端 `conversationState.ts`、page 和 shared types 仍以 `plannerRunId` 选择 Run；必须改为
  canonical `rootRunId`，旧 alias 只在一个 decoder/normalizer 边界读取。
- shared `agent-runtime` 已拥有 `run.todos_updated` 与 Root ownership reducer；不得新增
  Screenplay-specific plan reducer。
- 现有 replay tests 分段覆盖 todos、durable progress 与 continuation，但缺少一条完整
  Root→Operation/LongTask→AI Child→checkpoint→Candidate→Root final 的等价 fixture。

### TDD sequence

1. RED：rg boundary test 要求旧 planner module / Model planner / fixed durable plan / planner phase
   生产引用为零；resolver tests 改从新模块导入。
2. RED：snapshot/controller 首选 `rootRunId`，legacy `plannerRunId` 只在 decoder 输入边界可读，
   新类型与 page/controller 不直接访问 alias。
3. RED：runtime cleanup 使用 root 命名收集旧列中的 Root lineage，旧数据库仍完整清理。
4. RED：构造一条 canonical backend wire fixture，公开 todo 仅含 Root semantic steps；Recipe
   unit 只出现在 private durable progress；过程公开面仅保留 tool、context compaction 与显式
   delegation。
5. RED：同一 fixture 经 live chunks 与 persisted replay 输入 shared Root reducer，断言 todos、
   progress、Child tool/output、checkpoint revision、Candidate、final 与 run ownership 完全等价。
6. GREEN：删除旧 planner module/allow-list，收缩 backend/frontend legacy alias boundary，复用
   shared canonical reducer；不新增业务 plan reducer。
7. VERIFY：运行计划指定 backend/frontend/typecheck、rg gate、diff-check；记录 Task9 report。

### Scope watch

- 不删除或重命名 SQLite `planner_run_id` 列，不做 schema migration。
- PurrA Core 的 `_durable_plan_step_statuses` 是通用 durable helper，不属于旧业务
  `_durable_plan`，rg gate 必须区分精确符号/文件。
- `plannerRunId` 若为旧客户端输入兼容，只能出现在一个显式 legacy decoder 中；不得继续由
  新 snapshot 输出或由 page/controller 读取。
- replay fixture 不得把 Recipe unit title 投影成 Root todos，也不得暴露 Candidate body 或
  private reasoning。

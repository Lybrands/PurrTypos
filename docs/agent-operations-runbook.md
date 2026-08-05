# Agent 单路径运维手册

## 1. 运行模型

PurrTypos 只有一条 Agent 对话路径：

```text
HTTP /ai/chat/stream
  -> Application request mapping
  -> AgentComposition
  -> Agent Core
  -> Writing Domain / Infrastructure adapters
  -> Application SSE mapping
```

不需要设置 Agent 架构开关，也不能切回旧 Agent 实现。

## 2. 启动前检查

应用 lifespan 会：

1. 初始化数据库；
2. 创建实例级 Writing 技能目录快照；
3. 装配 `AgentComposition`；
4. 注册 HTTP Router；
5. 在关闭时取消未完成审批并释放 Composition 和数据库。

若 Composition 未初始化，Agent 请求应失败关闭；不得创建临时旧执行器作为兜底。

常用配置仍包括模型 API Key、Provider、Base URL、Model、上下文窗口和审批超时。API Key 只存在于请求范围，不能写入 Run、Trace 或日志。

## 3. 请求契约

### 普通直接回答

没有 `bookId` 或关闭 Agent 工具时，请求仍进入同一个 Core，但 Writing 工具范围为空。模型可以直接回答，不能获得隐式数据库能力。

### Writing Agent

当请求包含有效 `bookId` 且启用 Agent 工具时，Writing Domain 根据当前书籍、章节和用户目标提供规划约束与工具目录。

### 不支持的调用方工具

产品端只发送空 `tools`。若第三方请求发送非空自定义 `tools` 或显式 `tool_choice`，后端返回安全错误，并保证：

- 不调用模型；
- 不启动 Planner；
- 不执行工具；
- 不回退到其他运行路径。

## 4. 流式事件与断连

SSE 事件由 `application/sse_mapping.py` 统一生成。前端依赖的主要事件包括：

- Run 开始、计划和步骤状态；
- `delta` / `thinkingDelta`；
- 工具调用、结果和进度；
- 审批请求与审批结果；
- Writing Domain effects；
- Run 完成、阻塞、失败或取消；
- 最终 `done` 或安全错误。

`EventSourceResponse` 是 ASGI `receive` 的唯一所有者。收到断连或发送失败后，Router 设置共享取消信号并等待 Core 完成清理。不要增加第二个读取 `http.disconnect` 的协程。

## 5. 人工审批

高风险工具通过：

```text
POST /api/ai/tool-approvals/{approval_id}
```

提交 `{ "approved": true }` 或 `{ "approved": false }`。审批必须满足：

- `approval_id` 属于当前仍存活的 Run；
- 每个审批只能成功消费一次；
- 拒绝不会执行工具；
- 超时、断连和 shutdown 会关闭等待；
- 已提交的原子事务不能被迟到的取消伪装成未执行。

不存在旧审批 Broker fallback。

## 6. 诊断与确定性检查

单次 Run 诊断：

```text
GET /api/ai/agent-runs/{run_id}/diagnostics
```

诊断包含 Run 终态、Trace 覆盖、工具治理、受控恢复决策、性能信息以及 Artifact/Work Item 运维快照，不包含人工 Pilot 评分。`recovery.decisions` 会说明每次重试、兼容降级或重规划为何被允许/拒绝；`artifactMaintenance` 会区分有效、过期、失联和目标状态失效的 writer claim，并报告结构一致性。两者都只保存控制元数据，不包含工具 JSON、Artifact 正文或 claim token。

调试面板的“安全维护 Artifact”调用：

```text
POST /api/ai/artifacts/maintenance
```

该入口没有保留期参数，只回收可以确定失效的 writer claim，不删除 Artifact、批次或 Work Item。终态内容清理仍只能通过宿主环境变量显式配置，不能由调试面板触发。

确定性检查：

```text
GET /api/ai/agent-runtime-regressions
GET /api/ai/agent-security-redteam
```

它们不调用外部模型，可用于本地排障和 CI。仓库不再提供 Pilot Runner、双 cohort 报告、Gate 或 Rollout API。

## 7. 故障处理

### `missing_required_tool_call`

模型没有返回当前计划要求的结构化调用。Core 只允许协议内的有限修复；仍失败则停止，不解析普通文本中的伪调用。

### `tool_not_authorized` / `tool_scope_violation`

模型请求了当前步骤未授权的工具，或参数越过当前书籍/章节范围。整批不执行。应检查 Planning Policy、Catalog 和宿主上下文，不要放宽默认权限。

### `approval_unavailable`

审批不存在、超时或生命周期已经关闭。操作未执行。检查前端是否展示当前 approval，以及应用是否发生 shutdown/断连。

### `upstream_stream_interrupted`

供应商流在明确终止前断开。Core 仅在没有向用户暴露内容、没有半成品工具批次且仍有轮次预算时进行一次安全重试。已完成工具不会因重试再次执行。

### `response_constraint_violation`

候选回答不满足 Writing 输出约束，且一次确定性修复仍失败。候选不会展示。应检查任务范围、输入材料和约束实现。

### `response_judge_error`

语义 Judge 无法完成或返回无效契约。候选不会展示。它属于外部模型/协议故障，不应通过跳过 Judge 来兜底。

## 8. 修改后的最小验证

按改动范围选择测试，至少覆盖：

```bash
.venv/bin/python -m pytest \
  backend/tests/test_agent_core_runtime.py \
  backend/tests/test_agent_core_engine.py \
  backend/tests/test_agent_composition.py \
  backend/tests/test_ai_composed_sse_wire_contract.py \
  backend/tests/test_main_lifespan.py -q
```

随后运行完整后端测试、TypeScript 检查、Electron 单测、Python 编译与 `git diff --check`。

## 9. 变更原则

- Core 的通用不变量只在 `agent_core/` 修改。
- Writing 语义和工具政策只在 `domains/writing/` 修改。
- Provider、SQLite 和具体工具实现放在 `infrastructure/`。
- Router 只处理 HTTP、SSE 和取消传播。
- 不为临时故障重新引入双路径、环境开关或兼容 facade。

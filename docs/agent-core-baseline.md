# Agent Core 抽离前行为基线

> 基线日期：2026-07-13<br>
> 作用：记录三层架构改造开始前，现有 Agent 的确定性行为与验证入口。

## 1. 自动化基线

在 `backend` 目录执行：

```powershell
py -m pytest tests/test_agent_run_controller.py tests/test_ai_chat_stream_agent_run.py tests/test_task_planner.py tests/test_tool_approval_service.py tests/test_tool_contract.py tests/test_agent_runtime_regression.py tests/test_agent_security.py tests/test_context_budget.py tests/test_agent_core_baseline.py tests/test_agent_core_legacy_behavior_baseline.py tests/test_agent_runtime_tape_baseline.py tests/test_agent_core_contracts.py tests/test_agent_core_boundaries.py tests/test_agent_core_adapters.py tests/test_agent_core_runtime.py tests/test_agent_core_runtime_real_tool_stack.py tests/test_sqlite_run_repository.py tests/test_legacy_tool_execution_gateway.py tests/test_ai_sse_wire_contract.py tests/test_writing_planning_policy.py
```

改造前原有相关测试结果：`64 passed`。阶段 0 加入组件契约、审批/提案特征和第一版旧 Runtime Tape 后的冻结结果：`86 passed`。阶段 3A 加入 Core 契约/边界、RunRepository、双路径 Tape、Core Runtime、真实审批工具栈和 ASGI wire 门禁后，上述当前命令结果为：`164 passed in 19.45s`（2026-07-13）。同日完整后端回归结果为：`414 passed in 52.53s`。

覆盖范围：

- Run 创建、To-do 状态转换和终态不可覆盖。
- Planner 正常、非法输出、异常和 fail-closed。
- 模型流、工具回合、顺序工具步骤和最终回答。
- 当前步骤工具 allowlist 与供应商 `tool_choice` 降级。
- `required` 降级在后续模型轮持续生效，调用方显式 `tool_choice` 原样走旧路径。
- Provider 给出 `finish_reason` 后立即结束该模型流，不消费终止块之后的数据。
- 人工审批的批准、拒绝、超时和只执行一次。
- 客户端断连、上游中断、工具异常和安全错误信息。
- 工具 Schema、handler、缓存和 Policy 的启动契约。
- 参数、批量、书籍作用域、跨书写入和结果清洗。
- 上下文预算、完整轮次裁剪和溢出停止。
- Core 工具续轮预算不低估实际 Provider wire 结构。

## 2. 确定性组件夹具

`backend/tests/fixtures/agent_core/runtime_replay_v1.json` 当前固化以下组件输入与预期：

- Planner 输出规范化。
- 普通模型流的正文、思考和结束原因。
- 分片结构化工具调用的合并结果。
- 工具结果续接消息。
- 审批拒绝、超时、取消、handler 失败和正常完成的分类。

`backend/tests/test_agent_core_baseline.py` 当前消费的是旧 Planner normalizer、StreamAccumulator、工具续接消息和结果分类器，并没有驱动完整的 `chat_stream`、Run Controller 或 Tool Executor。因此它是“确定性组件契约”，不能单独作为新旧 Runtime 等价的证据。

新 `agent_core.runtime` 建立后必须先通过同一份组件夹具，并继续通过下一节定义的完整 Runtime Tape。两类测试都通过后才能删除旧运行路径。

## 3. 旧 Runtime 编排 Tape（脚本化 Tool Gateway）

`backend/tests/fixtures/agent_core/legacy_runtime_tape_v1.json` 与 `backend/tests/test_agent_runtime_tape_baseline.py` 会实际驱动当前 `chat_stream`、Run Controller、模型轮次、工具续接、SSE 编排和 SQLite Run/To-do/Trace 持久化。为了确定性，Tape 会用脚本化 Tool Gateway 替换真实 `ToolExecutor`、ApprovalBroker 和写作 handler；因此它锁定的是旧 Runtime 编排主链，而不是完整工具执行栈。

第一版 Tape 固化六类运行：

- 无工具直接回答。
- 读取章节后回答。
- 正文修改只产生提案。
- 破坏性操作被用户拒绝。
- 两个工具严格按步骤顺序执行。
- 客户端断连后取消 Run。

Tape 对 Run ID 和 Approval ID 做占位符规范化，对脚本化路径的 SSE 逻辑事件序列计算稳定 SHA-256 快照；同时显式比较事件顺序、每轮模型可见工具、模型调用次数、脚本工具序列、To-do 终态、Run 终态、Trace 顺序、最终文本和领域副作用。Tape 还会断言 Planner 输入、每轮 `tool_choice`、typed 工具续接消息、工具作用域上下文和取消信号没有丢失。

阶段 3A 建立后，`test_agent_runtime_tape_baseline.py` 会让旧路由循环和 Core Runtime 兼容分支分别消费同一组 Tape，并要求两者产出完全相同的摘要。另有独立测试覆盖 Core Runtime → 兼容 Gateway → 真实 ToolExecutor/ApprovalBroker/handler 的实时审批链，以及旧/新路径的 ASGI `text/event-stream` 响应头、`data:` framing 和结束行为；ASGI 测试还会对 Core bridge 调用次数做断言，确保开关打开时确实经过新路径、关闭时确实经过旧路径。

当前仍没有把“真实审批工具链”和“ASGI HTTP wire”串进同一个端到端 Tape，因此现有 SHA-256 仍称为编排快照，而不称为完整线上工具 SSE 快照。

## 4. 真实模型基线

真实模型质量不采用逐字或逐工具序列一致作为验收标准。继续使用 `agent_pilot_cases.py` 中的 Pilot，并比较：

- 工具成功率与越权调用数。
- Run 终态正确率。
- 人工写作质量评分。
- 上下文引用和记忆一致性。
- Token、模型调用次数和总延迟。

真实 Pilot 需要已配置的模型和人工审批，因此不作为离线 CI 的前置条件。

## 5. 迁移门禁

任何阶段出现以下情况都不得继续：

- 安全回归或未分类工具可以执行。
- 持久化写操作绕过审批。
- 工具失败或断连被记录为 `done`。
- 写作上下文、章节作用域或记忆作用域丢失。
- 前端依赖的 SSE 事件不兼容。
- Token 或延迟出现无法解释的明显退化。

# Agent 单路径确定性基线

本基线只验证当前三层架构，不比较旧 Agent 实现，也不调用外部模型。

> 本文继续作为 Writing Agent 历史基线；2026-08 Core/剧本对话重构的现状清单与完整门禁见 [`docs/design/purra-screenplay-refactor-baseline.md`](design/purra-screenplay-refactor-baseline.md)。

## 覆盖范围

- `packages/purra/src/purra/`：契约、Planner、Runtime、Run Controller、工具安全、审批与取消；
- `backend/domains/writing/`：Planning Policy、上下文、工具作用域和响应约束；
- `backend/application/`：Composition、请求映射、SSE 映射和确定性检查组合；
- `backend/infrastructure/`：模型协议、SQLite Repository 和 Writing 工具实现；
- `backend/routers/ai.py`：唯一 composed HTTP/SSE 路径、审批和断连清理。

## 最小回归

```bash
.venv/bin/python -m pytest \
  packages/purra/tests/test_standalone_agent_conformance.py \
  packages/purra/tests/test_model_tool_gateway_conformance.py \
  packages/purra/tests/test_durable_execution.py -q
.venv/bin/python -m pytest \
  backend/tests/test_purra_runtime.py \
  backend/tests/test_purra_tool_executor.py \
  backend/tests/test_writing_domain_adapter.py \
  backend/tests/test_writing_response_validation.py \
  backend/tests/test_agent_composition.py \
  backend/tests/test_ai_composed_sse_wire_contract.py \
  backend/tests/test_main_lifespan.py -q
```

提交前还应运行完整后端测试、TypeScript 检查、Electron 单测、Python 编译和 `git diff --check`。

## 不变量

1. 不支持的 caller tools/tool choice 在模型调用前失败关闭。
2. 未授权、重复、畸形或超限的工具批次不得部分执行。
3. 确认型工具在审批前不得产生副作用。
4. 拒绝、超时、取消和失败保持不同终态。
5. 断流重试不得重复执行已经完成的工具。
6. Writing 数据按当前书籍和章节作用域校验。
7. 不符合响应约束的候选不得向用户流出。
8. Run 终态和模型尝试必须具有完整 Trace。

历史旧路由 Tape、双路径一致性测试和真实模型 Pilot 已随迁移设施删除，不属于当前基线。

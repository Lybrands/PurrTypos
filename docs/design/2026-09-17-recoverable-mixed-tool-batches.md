# 混发读写工具批次改为可恢复失败（2026-09-17，PurrA 同版本候选 rev2（时为 1.0.1，2026-09-18 整线改号 1.1.1））

## 背景

`run_4cdc32ac293f40a0`（err_db88993294444656）：DeepSeek 系模型在工具轮把
`delegateToAgents`（PROPOSE）与只读工具并行混发一批，PurrA 执行器以
`multi_call_batch_requires_read_only_tools` 整批 REJECTED，orchestrator 把
REJECTED 定性为终态失败——一次模型可自行纠正的协议偏差（并行多调用是
OpenAI 兼容协议下常见行为）令整轮 Run 报废。

## 变更（PurrA 仓库，基线 508b511）

- `purra/tools/executor.py`：多调用批次含非只读工具时，批次结果从 REJECTED
  改为 **FAILED**（逐调用错误结果不变，发生在任何 scope/approval/handler
  之前，`effect_state=NOT_STARTED`），消息附带恢复指引（非只读工具每轮单独
  调用后重发）。
- `purra/runtime/tool_recovery.py`：`multi_call_batch_requires_read_only_tools`
  加入 `_RECOVERABLE_TOOL_INPUT_ERROR_CODES`，走既有 RETRY_MODEL 路径——
  错误结果与指引回传模型，RecoveryLedger 有界重试，超限自动终态（天然熔断，
  不会无限循环）。
- **不变的部分**：批次必须全只读的约束本身保留（顺序确定性、审批原子性、
  恢复对账都依赖它）；授权/范围类拒绝（`unknown_tool`、`invalid_tool_scope`、
  `tool_not_authorized`）仍为 REJECTED 终态，fail-closed 姿态不变。

## 分发

- 版本线保持 1.0.1：修复作为同版本候选 rev2 迭代（`verify-purra-candidate`
  的强制重装流程即为此设计），核心 wheel 以新 SHA 替换
  `backend/vendor/purra-1.0.1/` 内旧件，`requirements-purra.txt` 与
  `purra-candidate.json` 同步（新基线 commit + 724 文件源码哈希）。
- `scripts/verify-purra-candidate.py` 改为按文件名在 vendor 子目录定位，
  不再硬编码版本目录。
- 2026-09-18 更正：1.0.x 版本线实际承载 1.1 范围特性，purra 整线改号为
  1.1.1；本节其余版本表述为当时（rev2 迭代）的记录，当前候选位于
  `backend/vendor/purra-1.1.1/`，基线 commit `a2e69c6`。

## 验证

- purra 仓全量测试通过，新增 `tests/test_multi_call_recovery.py`
  （FAILED 化、RETRY_MODEL、账本熔断）。
- PurrTypos：`test_purra_tool_executor.py` 断言更新为 FAILED；
  purra 相关后端套件（adapters/tool_choice/model_boundary/contracts/
  gateways/profiles/package）全部通过。

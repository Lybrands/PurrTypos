# 来源分析参数修正

## 现场证据

只读查询本地持久化事件：根 Run `run_833bbb7919e54371`，失败子 Run `run_563594292d5f4a60`。

1. appendAnalysisFacts 首次提交 16 条，超过 maxItems 8；返回 invalid_tool_arguments_schema。
2. 第二次提交触发 facts[6].evidence[0] 引文逐字匹配失败，返回 tool_input_invalid。
3. recovery_decision 为 attempt_budget_exhausted，maxAttempts 1；整个任务终止。不是 Provider 网络错误。

## 修复

- 仅小说分析 DomainAdapter 将 TOOL_INPUT_INVALID 修正额度设置为 3，保留其他默认策略及总模型轮次限制。
- 分批资料/观察保存前收集所有引文错误，一次返回字段路径；仍然整批原子拒绝，禁止模糊匹配或静默接受错误引文。
- 回归覆盖同批多个错误、失败无副作用、修正成功、幂等保存，以及修正预算不影响其他错误类型。

## 验证边界

确定性回归：writing_technique_generation、novel_analysis_conversation、novel_analysis、novel_sources。
未重新调用真实 Provider，未重写失败 Run 状态，未自动重跑全部来源分析。运行中的后端需重新加载修复后代码。

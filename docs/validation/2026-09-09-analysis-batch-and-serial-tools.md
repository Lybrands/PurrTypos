# 资料批次与串行工具调用

- 删除 appendAnalysisFacts 的 maxItems=8 和对应提示词；保留 max_argument_chars=300000、逐字引文校验、事务、取消和幂等边界。单元最终提交原有 48 条限制未调整。
- 回归使用 16 条不同事实，验证保存、重放不重复、失败整批不保存及最终汇总。
- Run run_1f41f52b36204fa2 的失败子 Run run_8c0caa22f75e4c67 同轮调用 appendAnalysisFacts 与 appendAnalysisObservations，被执行器以 multi_call_batch_requires_read_only_tools 拒绝。
- 小说分析 unit 请求设置 parallel_tool_calls=false；兼容 OpenAI Chat 请求编译器传递该字段，提示词明确保存/提交逐次调用。不修改 PurrA，不把写工具改成只读。
- 确定性验证覆盖模型选项保留、实际请求参数编译和来源分析回归。未重跑真实 Provider，未更改历史 Run 状态；运行中的后端需要重新加载代码。

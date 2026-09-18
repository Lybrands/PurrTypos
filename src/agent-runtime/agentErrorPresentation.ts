const AGENT_ERROR_MESSAGES: Record<string, string> = {
  parent_delivery_reconciliation_required: '阶段交付状态未确定，已停止自动重试。请核对已显示内容后发起新任务。',
  child_result_idle_timeout: '等待子任务结果超时，已停止本轮执行。',
  planning_invalid: 'Agent 计划格式无效，已安全停止。',
  planning_contract_violation: 'Agent 计划超出当前工具授权，已安全停止。',
  planning_failed: 'Agent 计划生成失败，已停止执行。',
  missing_required_tool_call: '当前计划步骤必须调用工具，但模型未返回结构化调用。',
  response_constraint_violation: '模型回答未满足当前写作要求，未展示不合规内容。',
  response_judge_error: '语义校验暂时无法完成，候选回答未展示。',
  response_judge_contract_violation: '语义校验返回了无效结果，候选回答未展示。',
  empty_model_response: '模型没有生成可展示的答复。',
  model_output_truncated: '模型回答达到输出上限且未完整结束。',
  tool_call_truncated: '模型生成的工具参数不完整，工具未执行。',
  tool_execution_failed: '工具执行失败，相关操作未完成。',
  tool_input_invalid: '工具参数未通过校验，相关操作未执行。',
  provider_insufficient_balance: '模型服务账户余额或额度不足，请充值或更换模型。',
  provider_authentication_failed: '模型服务鉴权失败，请检查 API Key 和接口地址。',
  provider_rate_limited: '模型服务请求过于频繁或已达到限额，请稍后重试。',
  provider_unavailable: '模型服务暂时不可用，请稍后重试。',
  upstream_stream_interrupted: '模型服务流式响应中断，请检查网络或稍后重试。',
  model_invocation_deadline_exceeded: '模型单次处理超过当前时限，已安全停止。',
  context_overflow_initial: '本轮请求的上下文超出所选模型的窗口（含必选的写作技法与受保护内容）。请减少手动选择的写作技法，或改用更大上下文窗口的模型后重试。',
  context_overflow_after_tool: '对话与工具结果累计超出模型上下文窗口，本轮已安全停止。请开启新会话继续，或改用更大上下文窗口的模型。',
}

export function presentAgentRunError(
  status: string | undefined,
  errorCode: string | null | undefined,
): string {
  if (status === 'blocked') return 'Agent 未完成全部计划步骤，已安全停止。'
  const code = String(errorCode || '').trim()
  return AGENT_ERROR_MESSAGES[code]
    || 'Agent 运行过程中发生异常，已安全停止；请稍后重试。'
}

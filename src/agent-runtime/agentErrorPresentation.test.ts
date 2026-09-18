import assert from 'node:assert/strict'
import test from 'node:test'
import { presentAgentRunError } from './agentErrorPresentation.ts'

test('transport error codes are localized only in the product read model', () => {
  assert.equal(
    presentAgentRunError('failed', 'missing_required_tool_call'),
    '当前计划步骤必须调用工具，但模型未返回结构化调用。',
  )
  assert.equal(
    presentAgentRunError('blocked', null),
    'Agent 未完成全部计划步骤，已安全停止。',
  )
  assert.equal(
    presentAgentRunError('failed', 'model_invocation_deadline_exceeded'),
    '模型单次处理超过当前时限，已安全停止。',
  )
})

test('context overflow codes present actionable guidance instead of a generic failure', () => {
  assert.equal(
    presentAgentRunError('failed', 'context_overflow_initial'),
    '本轮请求的上下文超出所选模型的窗口（含必选的写作技法与受保护内容）。请减少手动选择的写作技法，或改用更大上下文窗口的模型后重试。',
  )
  assert.equal(
    presentAgentRunError('failed', 'context_overflow_after_tool'),
    '对话与工具结果累计超出模型上下文窗口，本轮已安全停止。请开启新会话继续，或改用更大上下文窗口的模型。',
  )
})

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

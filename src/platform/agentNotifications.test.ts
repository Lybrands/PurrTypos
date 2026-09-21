import assert from 'node:assert/strict'
import test from 'node:test'
import { buildAgentCompletionContent } from './agentNotifications.ts'

test('conversation title becomes the notification content', () => {
  const { title, body } = buildAgentCompletionContent({ conversationTitle: '小说故事背景设定' })
  assert.equal(title, '小说故事背景设定')
  assert.equal(body, '任务已完成，可以返回查看结果。')
})

test('long conversation titles are clamped with an ellipsis', () => {
  const { title } = buildAgentCompletionContent({ conversationTitle: '长'.repeat(80) })
  assert.ok(title.length <= 60)
  assert.ok(title.endsWith('…'))
})

test('missing conversation title falls back to the generic title', () => {
  assert.equal(
    buildAgentCompletionContent({ conversationTitle: undefined }).title,
    'Agent 任务已完成',
  )
  assert.equal(
    buildAgentCompletionContent({ conversationTitle: '   ' }).title,
    'Agent 任务已完成',
  )
})

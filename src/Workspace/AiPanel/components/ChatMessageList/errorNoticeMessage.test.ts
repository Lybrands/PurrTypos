import assert from 'node:assert/strict'
import test from 'node:test'
import { getErrorNoticeMessage } from './errorNoticeMessage.ts'

test('error notice exposes metadata when terminal content is intentionally empty', () => {
  assert.equal(
    getErrorNoticeMessage({ content: '', error: '上游连接失败' }),
    '上游连接失败',
  )
})

test('error notice keeps legacy content and a stable fallback', () => {
  assert.equal(
    getErrorNoticeMessage({ content: '旧版错误正文' }),
    '旧版错误正文',
  )
  assert.equal(getErrorNoticeMessage({ content: '' }), '本轮执行失败')
})

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { buildUserQuotePrefill, buildUserQuotesPrefill, splitUserQuote } from './userQuote.ts'

test('splitUserQuote 拆出头部引用块与剩余正文', () => {
  const content = '> 引用《第一章》：\n> 被选中的句子\n\n这里是我的问题？'
  const { quoteLines, body } = splitUserQuote(content)
  assert.deepEqual(quoteLines, ['引用《第一章》：', '被选中的句子'])
  assert.equal(body, '这里是我的问题？')
})

test('splitUserQuote 无引用块时原样返回', () => {
  const { quoteLines, body } = splitUserQuote('普通提问\n没有引用')
  assert.deepEqual(quoteLines, [])
  assert.equal(body, '普通提问\n没有引用')
})

test('splitUserQuote 仅引用块时正文为空', () => {
  const { quoteLines, body } = splitUserQuote('> 只有引用')
  assert.deepEqual(quoteLines, ['只有引用'])
  assert.equal(body, '')
})

test('splitUserQuote 不吞正文中段的 > 行（只认头部连续块）', () => {
  const { quoteLines, body } = splitUserQuote('正文\n> 中段引用\n继续')
  assert.deepEqual(quoteLines, [])
  assert.equal(body, '正文\n> 中段引用\n继续')
})

test('buildUserQuotePrefill 与 splitUserQuote 互逆', () => {
  const prefill = buildUserQuotePrefill('第一行\n第二行', '第三章 标题')
  const { quoteLines, body } = splitUserQuote(prefill)
  assert.deepEqual(quoteLines, ['引用《第三章 标题》：', '第一行', '第二行'])
  assert.equal(body, '')
})

test('buildUserQuotePrefill 无章节标题', () => {
  assert.equal(buildUserQuotePrefill('句子'), '> 引用：\n> 句子\n\n')
})

test('buildUserQuotesPrefill 多条引用拼成单个连续引用块', () => {
  const prefill = buildUserQuotesPrefill([
    { quote: '第一处', chapterTitle: '第一章' },
    { quote: '第二处', chapterTitle: '第二章' },
  ])
  assert.equal(
    prefill,
    '> 引用《第一章》：\n> 第一处\n>\n> 引用《第二章》：\n> 第二处\n\n',
  )
  // 整体仍是一个连续 `> ` 块：split 能全部拆出
  const { quoteLines, body } = splitUserQuote(prefill)
  assert.deepEqual(quoteLines, [
    '引用《第一章》：',
    '第一处',
    '',
    '引用《第二章》：',
    '第二处',
  ])
  assert.equal(body, '')
})

test('buildUserQuotesPrefill 空白与去重由调用方处理、空输入返回空串', () => {
  assert.equal(buildUserQuotesPrefill([]), '')
  assert.equal(buildUserQuotesPrefill([{ quote: '   ' }]), '')
})

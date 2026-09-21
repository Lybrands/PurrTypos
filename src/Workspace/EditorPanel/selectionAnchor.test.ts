import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  findAllMatchStarts,
  findAnchorStart,
  truncateAroundSelection,
} from './selectionAnchor.ts'

test('findAllMatchStarts 大小写不敏感且不重叠', () => {
  assert.deepEqual(findAllMatchStarts('abcABCabc', 'abc'), [0, 3, 6])
  assert.deepEqual(findAllMatchStarts('aaaa', 'aa'), [0, 2])
  assert.deepEqual(findAllMatchStarts('abc', ''), [])
})

test('findAnchorStart 取离原位置最近的匹配', () => {
  const text = 'x引文x中间x引文x尾部引文'
  assert.equal(findAnchorStart(text, '引文', 0), 1)
  assert.equal(findAnchorStart(text, '引文', 6), 7)
  assert.equal(findAnchorStart(text, '引文', text.length - 2), 12)
  assert.equal(findAnchorStart(text, '不存在', 3), null)
})

test('truncateAroundSelection 不超限直接全文返回', () => {
  const res = truncateAroundSelection('短文本', 0, 3, 100)
  assert.equal(res.truncated, false)
  assert.equal(res.text, '短文本')
})

test('truncateAroundSelection 超限时选区完整保留且前后对半', () => {
  const paras = Array.from({ length: 40 }, (_, i) => `第${i}段落内容`).join('\n')
  const selStart = paras.indexOf('第20段落内容')
  const selEnd = selStart + '第20段落内容'.length
  const res = truncateAroundSelection(paras, selStart, selEnd, 120)
  assert.equal(res.truncated, true)
  assert.ok(res.text.includes('第20段落内容'), '选区必须完整保留')
  assert.ok(res.text.startsWith('……'), '从中间截断应有前省略号')
  assert.ok(res.text.includes('已截断，原文约'))
  assert.ok(res.text.length < paras.length)
})

test('truncateAroundSelection 选区靠开头时不给前侧留空', () => {
  const paras = Array.from({ length: 60 }, (_, i) => `第${i}段落`).join('\n')
  const selStart = 0
  const selEnd = '第0段落'.length
  const res = truncateAroundSelection(paras, selStart, selEnd, 60)
  assert.ok(res.text.includes('第0段落'))
  assert.ok(!res.text.startsWith('……'), '开头截断不应有前省略号')
})

test('truncateAroundSelection 选区自身超限时原样保留选区', () => {
  const long = 'a'.repeat(300)
  const res = truncateAroundSelection(long, 0, 300, 50)
  assert.equal(res.truncated, true)
  assert.ok(res.text.includes('a'))
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { appendImportedMarkdown, looksLikeMarkdown } from './markdown.ts'

test('recognizes common Markdown structures without treating plain prose as markup', () => {
  assert.equal(looksLikeMarkdown('## 人物定位'), true)
  assert.equal(looksLikeMarkdown('- 勇敢\n- 克制'), true)
  assert.equal(looksLikeMarkdown('普通的一段人物介绍。'), false)
})

test('appends imported Markdown with one empty line between documents', () => {
  assert.equal(appendImportedMarkdown('', '# 新资料'), '# 新资料')
  assert.equal(
    appendImportedMarkdown('# 原资料', '## 新资料'),
    '# 原资料\n\n## 新资料',
  )
})

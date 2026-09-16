import assert from 'node:assert/strict'
import test from 'node:test'
import { htmlToMarkdown, markdownToHtml, markdownToYamlFrontmatter, yamlFrontmatterToMarkdown } from '../../utils/markdown.ts'
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

test('YAML frontmatter survives the shared Markdown editor representation', () => {
  const source = '---\nname: 节奏控制\ndescription: 控制场景节奏\nmetadata:\n  retrieval:\n    keywords: [节奏]\n---\n\n## 使用说明\n\n正文。'
  assert.equal(markdownToYamlFrontmatter(yamlFrontmatterToMarkdown(source)), source)
  assert.equal(
    markdownToYamlFrontmatter(htmlToMarkdown(markdownToHtml(yamlFrontmatterToMarkdown(source)))),
    source,
  )
})

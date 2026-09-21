import assert from 'node:assert/strict'
import { test } from 'node:test'

import { normalizeGeneratedPlainText } from './generationText.ts'

test('剥离加粗/斜体/删除线/行内代码', () => {
  assert.equal(normalizeGeneratedPlainText('**加粗**与*斜体*与~~删除~~与`代码`'), '加粗与斜体与删除与代码')
  assert.equal(normalizeGeneratedPlainText('__下划线加粗__'), '下划线加粗')
})

test('剥离标题/引用/列表前缀', () => {
  assert.equal(normalizeGeneratedPlainText('## 标题'), '标题')
  assert.equal(normalizeGeneratedPlainText('> 引用句'), '引用句')
  assert.equal(normalizeGeneratedPlainText('- 列表项'), '列表项')
  assert.equal(normalizeGeneratedPlainText('3. 有序项'), '有序项')
})

test('剥离围栏代码块围栏行但保留内容', () => {
  const input = '```text\n围栏内\n```\n后文'
  assert.equal(normalizeGeneratedPlainText(input), '围栏内\n后文')
})

test('收敛 3+ 连续空行为一个空行并去首尾空白', () => {
  assert.equal(normalizeGeneratedPlainText('\n\n段一\n\n\n\n段二\n\n'), '段一\n\n段二')
})

test('普通中文文本原样保留（不误伤）', () => {
  assert.equal(normalizeGeneratedPlainText('他说：3*4=12，明天见。'), '他说：3*4=12，明天见。')
  assert.equal(normalizeGeneratedPlainText('2024.1.1 出发'), '2024.1.1 出发')
})

test('多段 Markdown 输出整段净化', () => {
  const input = '### 改写结果\n\n**第一段**开头。\n\n- 要点一\n- 要点二'
  assert.equal(normalizeGeneratedPlainText(input), '改写结果\n\n第一段开头。\n\n要点一\n要点二')
})

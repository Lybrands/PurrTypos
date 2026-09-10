import assert from 'node:assert/strict'
import test from 'node:test'
import { buildTechniqueFileTree } from './fileTree.ts'

test('目录按路径合并，保留多级目录及不同目录下的同名文件', () => {
  const tree = buildTechniqueFileTree(['场景/对白/示例.md', '节奏/示例.md', '场景/说明.md', 'SKILL.md', '场景/说明.md'])
  assert.equal(tree[0].path, 'SKILL.md')
  const scenes = tree.find(node => node.path === '场景')!
  assert.equal(scenes.kind, 'folder')
  assert.deepEqual(scenes.children.map(node => node.name), ['对白', '说明.md'])
  assert.equal(scenes.children[0].children[0].path, '场景/对白/示例.md')
  assert.equal(tree.find(node => node.path === '节奏')!.children[0].path, '节奏/示例.md')
})

test('单入口无需目录，重新构建后不保留已移走文件的旧目录', () => {
  assert.deepEqual(buildTechniqueFileTree([]), [])
  assert.deepEqual(buildTechniqueFileTree(['SKILL.md']).map(node => [node.kind, node.name]), [['file', 'SKILL.md']])
  buildTechniqueFileTree(['旧目录/正文.md'])
  assert.deepEqual(buildTechniqueFileTree(['新目录/正文.md']).map(node => node.path), ['新目录'])
})

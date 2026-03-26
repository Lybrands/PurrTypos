/**
 * 校验 `electron/skills/<name>/SKILL.md` 可被 toolRouter 加载，且与预期工具名集合一致。
 * 用法：node electron/validateSkills.js
 */

const path = require('path')
const assert = require('assert')
const toolRouter = require('./toolRouter')

/** 须与 `toolExecutor.js` 对外工具分支保持一致 */
const EXPECTED_TOOL_NAMES = [
  'addForeshadowing',
  'addMemory',
  'batchGetChapterContents',
  'editChapterContent',
  'editGlobalOutline',
  'getBookCharacters',
  'getChapterContent',
  'getGlobalOutline',
  'getStoryBackground',
  'listBookCharacters',
  'listOutlines',
  'listWritingChapters',
  'queryOutline',
  'searchMemories',
  'updateOutline',
]

function main() {
  toolRouter.setSkillsPath(path.join(__dirname, 'skills'))
  const items = toolRouter.getApiSkillItems()
  const loaded = new Set(items.map((s) => s.name))

  const expected = new Set(EXPECTED_TOOL_NAMES)
  const missing = [...expected].filter((n) => !loaded.has(n))
  const extra = [...loaded].filter((n) => !expected.has(n))

  assert.strictEqual(
    missing.length,
    0,
    `SKILL.md 缺失或无法加载的工具: ${missing.join(', ') || '(无)'}`,
  )
  assert.strictEqual(
    extra.length,
    0,
    `未列入 EXPECTED_TOOL_NAMES 的已加载工具: ${extra.join(', ') || '(无)'}`,
  )

  for (const s of items) {
    const props = s.parameters && s.parameters.properties
    assert.ok(
      props && typeof props === 'object',
      `${s.name}: parameters.properties 缺失或非法`,
    )
  }

  console.log(`[validateSkills] OK ${items.length} tools`)
}

main()

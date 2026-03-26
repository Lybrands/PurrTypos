/**
 * 协作共创：提示与工具护栏（手动运行）
 *   node electron/collabMode.scenarios.js
 */

const assert = require('assert')
const {
  buildCollabTurnAppendix,
  filterCollabTools,
  COLLAB_WRITE_TOOL_NAMES,
} = require('./collabPrompt')

function scenarioAppendixFirstTurn() {
  const msgs = [
    { role: 'system', content: 'x' },
    { role: 'user', content: '我想写个短篇' },
  ]
  const a = buildCollabTurnAppendix(msgs)
  assert(a.includes('对话尚浅') || a.includes('结构'), 'first turn should nudge outline')
}

function scenarioAppendixSkipNegotiation() {
  const msgs = [
    { role: 'system', content: 'x' },
    { role: 'user', content: '别问了，直接全文生成' },
  ]
  const a = buildCollabTurnAppendix(msgs)
  assert(/跳过|一次成稿|跳过协商/.test(a), 'should allow skip when user insists')
}

function scenarioFilterWriteTools() {
  const tools = [
    { type: 'function', function: { name: 'getChapterContent', parameters: {} } },
    { type: 'function', function: { name: 'editChapterContent', parameters: {} } },
  ]
  const f1 = filterCollabTools(tools, '随便聊聊')
  assert.strictEqual(f1.length, 1)
  assert.strictEqual(f1[0].function.name, 'getChapterContent')
  const f2 = filterCollabTools(tools, '请把这段写入章节保存')
  assert.strictEqual(f2.length, 2)
}

function scenarioWriteToolSet() {
  assert(COLLAB_WRITE_TOOL_NAMES.has('editChapterContent'))
  assert(!COLLAB_WRITE_TOOL_NAMES.has('getChapterContent'))
}

function run() {
  scenarioAppendixFirstTurn()
  scenarioAppendixSkipNegotiation()
  scenarioFilterWriteTools()
  scenarioWriteToolSet()
  console.log('[collabMode.scenarios] all passed')
}

run()

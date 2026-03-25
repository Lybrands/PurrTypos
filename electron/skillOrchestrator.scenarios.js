/**
 * 轻量回归场景（手动运行）：
 *   node electron/skillOrchestrator.scenarios.js
 */

const assert = require('assert')
const path = require('path')
const toolRouter = require('./toolRouter')
const { planToolCalls, executeWithRepair } = require('./skillOrchestrator')
const { getSkillSpecs } = require('./agentToolDefinitions')

toolRouter.setSkillsPath(path.join(__dirname, 'skills'))

async function scenarioMissingOutlineIdAutoRepair() {
  const skillSpecs = getSkillSpecs()
  const toolCtx = {
    bookId: 1,
    availableOutlines: [
      { id: 10, title: '世界观补充', type: 'other' },
      { id: 11, title: '角色设定', type: 'other' },
    ],
  }
  const inputCalls = [
    {
      id: 'tc_update',
      type: 'function',
      function: {
        name: 'updateOutline',
        arguments: JSON.stringify({ bookId: 1, markdown_content: 'new content' }),
      },
    },
  ]
  const plan = planToolCalls({
    toolCalls: inputCalls,
    skillSpecs,
    toolCtx,
    latestUserText: '请把世界观补充这个大纲改写得更黑暗',
  })
  assert(plan.executableCalls.some((x) => x.function.name === 'listOutlines'))

  let retried = false
  const result = await executeWithRepair({
    plannedCalls: plan.executableCalls,
    toolCtx,
    latestUserText: '请把世界观补充这个大纲改写得更黑暗',
    maxRepairRounds: 1,
    runTools: async (calls) => {
      return calls.map((c) => {
        if (c.function.name === 'updateOutline') {
          const args = JSON.parse(c.function.arguments || '{}')
          if (!args.outlineId) {
            return {
              tool_call_id: c.id,
              content: JSON.stringify({ success: false, error: '缺少有效 outlineId' }),
            }
          }
          retried = true
          return {
            tool_call_id: c.id,
            content: JSON.stringify({ success: true, outlineId: args.outlineId }),
          }
        }
        return {
          tool_call_id: c.id,
          content: JSON.stringify({ success: true, outlines: toolCtx.availableOutlines }),
        }
      })
    },
  })

  assert(retried, 'should execute updateOutline with resolved outlineId')
  assert(result.repairedRounds >= 0, 'repair rounds should be non-negative')
}

async function scenarioGlobalOutlineWriteNoRepairNeeded() {
  const plan = planToolCalls({
    toolCalls: [
      {
        id: 'tc_global',
        type: 'function',
        function: {
          name: 'editGlobalOutline',
          arguments: JSON.stringify({ markdownContent: '# 总纲' }),
        },
      },
    ],
    skillSpecs: getSkillSpecs(),
    toolCtx: { bookId: 2 },
    latestUserText: '重写总纲为三幕结构',
  })
  const names = plan.executableCalls.map((x) => x.function.name)
  assert(names.includes('getGlobalOutline'))
  assert(names.includes('editGlobalOutline'))
}

async function run() {
  await scenarioMissingOutlineIdAutoRepair()
  await scenarioGlobalOutlineWriteNoRepairNeeded()
  console.log('[skillOrchestrator.scenarios] all scenarios passed')
}

run().catch((err) => {
  console.error('[skillOrchestrator.scenarios] failed:', err)
  process.exit(1)
})

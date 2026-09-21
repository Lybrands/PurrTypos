const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { loadTypeScriptModule } = require('../../scripts/load-typescript-module.cjs')
const { backendTimestampMs, buildNovelAnalysisTaskPlan, buildNovelAnalysisTiming } = loadTypeScriptModule(
  path.join(__dirname, 'analysisTaskPlan.ts'),
)

test('backend timestamps without an offset are interpreted as UTC', () => {
  assert.equal(
    backendTimestampMs('2026-09-14 16:57:50.627'),
    Date.parse('2026-09-14T16:57:50.627Z'),
  )
})

const run = (overrides = {}) => ({
  runId: 'analysis-run', runStatus: 'done', taskId: 'analysis-task',
  taskStatus: 'completed', workflowStatus: 'completed',
  createTime: '2026-09-11T00:00:00Z', updateTime: '2026-09-11T00:01:00Z',
  analysisPlan: {
    title: '来源分析',
    steps: [{ id: 'extract', title: '提取证据', type: 'analyze', executor: 'model', dependsOn: [] }],
  },
  units: [{ unitId: 'extract:1', title: '提取证据', kind: 'extract_section', plannerStepId: 'extract', status: 'completed', attempt: 1, maxAttempts: 2 }],
  ...overrides,
})

test('a finalized root does not mark its paused workflow as completed', () => {
  const input = run({ taskStatus: 'paused', workflowStatus: 'paused' })
  const plan = buildNovelAnalysisTaskPlan(input)

  assert.equal(plan.status, 'paused')
  assert.ok('durationMs' in buildNovelAnalysisTiming(input, Date.UTC(2026, 8, 11, 0, 2), 1_000))
})

test('a missing workflow status does not invent a legacy task state', () => {
  const input = run({ taskStatus: 'paused', workflowStatus: null })
  const plan = buildNovelAnalysisTaskPlan(input)

  assert.equal(plan.status, 'running')
})

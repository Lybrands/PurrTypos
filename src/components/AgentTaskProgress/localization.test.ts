import assert from 'node:assert/strict'
import test from 'node:test'

import { localizeTaskPlan } from './localization.ts'

test('task progress replaces internal English tool titles with Chinese labels', () => {
  const plan = localizeTaskPlan({
    title: 'Analyze source material',
    status: 'running',
    steps: [{
      id: 'prepare-source',
      title: 'Prepare getSourceCoveragePlan',
      type: 'read',
      executor: 'tool',
      status: 'running',
      suggestedTools: ['getSourceCoveragePlan'],
    }],
  })

  assert.equal(plan.title, '任务进度')
  assert.equal(plan.steps[0].title, '规划原作阅读范围')
})

test('task progress preserves model-provided Chinese titles', () => {
  const plan = localizeTaskPlan({
    title: '分析原作范围',
    status: 'running',
    steps: [{
      id: 'analyze',
      title: '整理人物关系',
      type: 'analyze',
      executor: 'model',
      status: 'running',
    }],
  })

  assert.equal(plan.title, '分析原作范围')
  assert.equal(plan.steps[0].title, '整理人物关系')
})

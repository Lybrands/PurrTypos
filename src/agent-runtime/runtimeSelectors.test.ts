import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getActiveTaskPlan,
  getTaskPlanCountLabel,
  getVisibleTaskPlanSteps,
} from './taskPlan.ts'
import { buildHistoryConverter } from './chatHistory.ts'

test('task progress hides protocol Respond and uses sequential position', () => {
  const plan = {
    title: '任务',
    status: 'running' as const,
    steps: [
      { id: 'read-1', title: '读取', type: 'read' as const, status: 'done' as const },
      { id: 'write-1', title: '写作', type: 'write' as const, status: 'running' as const },
      { id: 'review-1', title: '审阅', type: 'review' as const, status: 'pending' as const },
      { id: 'respond-1', title: 'Respond', type: 'write' as const, status: 'pending' as const },
    ],
  }
  assert.equal(getVisibleTaskPlanSteps(plan).length, 3)
  assert.equal(getTaskPlanCountLabel(plan), '第 2/3 步')
  assert.equal(getActiveTaskPlan([{ role: 'assistant', content: '', taskPlan: plan }], true), plan)
})

test('history conversion never invents Assistant prose', () => {
  const convert = buildHistoryConverter()
  assert.equal(convert({ role: 'assistant', content: '' }), null)
  assert.deepEqual(convert({ role: 'assistant', content: '模型原文' }), {
    role: 'assistant',
    content: '模型原文',
  })
})

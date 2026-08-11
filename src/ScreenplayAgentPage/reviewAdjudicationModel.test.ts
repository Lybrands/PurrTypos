import assert from 'node:assert/strict'
import test from 'node:test'

import {
  batchDecisionLabel,
  finalizationDisabledReason,
  pendingReviewFindingIds,
  reviewAgentActionAvailable,
  reviewPrimaryAction,
  reviewRequiresRerun,
  reviewVersionLabel,
  reviewWorkspaceEntry,
} from './reviewAdjudicationModel.ts'

const findings = [
  { id: 'issue-1', status: 'riskAccepted' },
  { id: 'issue-2', status: 'riskAccepted' },
  { id: 'issue-3', status: 'riskAccepted' },
  { id: 'issue-4', status: 'riskAccepted' },
  { id: 'issue-5', status: 'riskAccepted' },
  { id: 'issue-6', status: 'dismissed' },
] as const

test('select all targets only findings that still need a user decision', () => {
  assert.deepEqual(pendingReviewFindingIds([
    ...findings,
    { id: 'issue-7', status: 'pending' as const },
    { id: 'issue-8', status: 'planned' as const },
  ]), ['issue-7'])
})

test('batch actions use explicit meanings and affected counts', () => {
  assert.equal(batchDecisionLabel('planned', 6), '将 6 条意见接受并准备修订')
  assert.equal(batchDecisionLabel('resolved', 6), '将 6 条意见标记为已解决')
  assert.equal(batchDecisionLabel('dismissed', 6), '将 6 条意见标记为不成立')
  assert.equal(batchDecisionLabel('riskAccepted', 6), '将 6 条意见标记为接受风险')
})

test('pending and planned findings explain why finalization is unavailable', () => {
  assert.equal(finalizationDisabledReason({
    counts: { pending: 2, planned: 0 },
    hardChecks: [],
    canFinalize: false,
  }), '还有 2 条意见待处理')
  assert.equal(finalizationDisabledReason({
    counts: { pending: 0, planned: 1 },
    hardChecks: [],
    canFinalize: false,
  }), '还有 1 条意见等待修订')
  assert.equal(finalizationDisabledReason({
    counts: { pending: 0, planned: 0 },
    hardChecks: [{ code: 'review_stale', message: '审阅报告已经过期' }],
    canFinalize: false,
  }), '审阅报告已经过期')
  assert.equal(finalizationDisabledReason({
    counts: { pending: 0, planned: 0 },
    hardChecks: [],
    canFinalize: true,
  }), null)
})

test('review phase selects a user action without trusting the Agent recommendation', () => {
  assert.deepEqual(reviewPrimaryAction({
    phase: 'readyToFinalize',
    recommendation: 'revise',
    hardChecks: [{
      code: 'review_execution_contaminated',
      message: '当前审阅报告混入了执行故障，需要重新审阅',
    }],
  }), { kind: 'startReview', label: '重新审阅' })
  assert.deepEqual(reviewPrimaryAction({
    phase: 'readyToFinalize',
    recommendation: 'revise',
    hardChecks: [{
      code: 'review_input_unverified',
      message: '当前审阅报告没有可验证的正文输入，需要重新审阅',
    }],
  }), { kind: 'startReview', label: '重新审阅' })
  assert.deepEqual(reviewPrimaryAction({
    phase: 'awaitingReview',
    recommendation: null,
  }), { kind: 'startReview', label: '开始审阅' })
  assert.deepEqual(reviewPrimaryAction({
    phase: 'adjudicating',
    recommendation: 'ready',
  }), { kind: 'processFindings', label: '处理审阅意见' })
  assert.deepEqual(reviewPrimaryAction({
    phase: 'readyToRevise',
    recommendation: 'ready',
  }), { kind: 'startRevision', label: '开始修订' })
  assert.deepEqual(reviewPrimaryAction({
    phase: 'readyToFinalize',
    recommendation: 'major_rework',
  }), { kind: 'finalize', label: '确认定稿' })
  assert.deepEqual(reviewPrimaryAction({
    phase: 'completed',
    recommendation: 'major_rework',
  }), { kind: 'completed', label: '创作已完成' })
})

test('review workspace entry never duplicates the Agent rerun action', () => {
  assert.equal(reviewWorkspaceEntry({
    phase: 'readyToFinalize',
    counts: { pending: 0 },
    hardChecks: [{
      code: 'review_execution_contaminated',
      message: '当前审阅报告混入了执行故障，需要重新审阅',
    }],
  }), null)
  assert.equal(reviewWorkspaceEntry({
    phase: 'readyToFinalize',
    counts: { pending: 0 },
    hardChecks: [{
      code: 'review_input_unverified',
      message: '当前审阅报告没有可验证的正文输入，需要重新审阅',
    }],
  }), null)
  assert.equal(reviewWorkspaceEntry({
    phase: 'awaitingReview',
    counts: { pending: 0 },
  }), null)
  assert.deepEqual(reviewWorkspaceEntry({
    phase: 'adjudicating',
    counts: { pending: 8 },
  }), { label: '查看审阅项（8）', emphasis: 'secondary' })
  assert.deepEqual(reviewWorkspaceEntry({
    phase: 'readyToRevise',
    counts: { pending: 0 },
  }), { label: '查看审阅决定', emphasis: 'secondary' })
  assert.deepEqual(reviewWorkspaceEntry({
    phase: 'readyToFinalize',
    counts: { pending: 0 },
  }), { label: '确认定稿', emphasis: 'primary' })
  assert.deepEqual(reviewWorkspaceEntry({
    phase: 'completed',
    counts: { pending: 0 },
  }), { label: '查看定稿记录', emphasis: 'secondary' })
})

test('review phase exposes the Agent shortcut only when Agent work is available', () => {
  assert.equal(reviewAgentActionAvailable({ phase: 'awaitingReview' }), true)
  assert.equal(reviewAgentActionAvailable({ phase: 'adjudicating' }), true)
  assert.equal(reviewAgentActionAvailable({ phase: 'readyToRevise' }), true)
  assert.equal(reviewAgentActionAvailable({ phase: 'readyToFinalize' }), false)
  assert.equal(reviewAgentActionAvailable({
    phase: 'readyToFinalize',
    hardChecks: [{
      code: 'review_input_unverified',
      message: '当前审阅报告没有可验证的正文输入',
    }],
  }), true)
})

test('review version label never exposes the internal revision identifier', () => {
  const internalId = 'review-internal-secret-1234'
  const label = reviewVersionLabel(internalId)

  assert.equal(label, '当前审阅版本')
  assert.equal(reviewVersionLabel(null), '')
  assert.doesNotMatch(label, /internal|secret|1234/)
})

test('review rerun is driven by validation checks, not formal review findings', () => {
  assert.equal(reviewRequiresRerun({
    hardChecks: [{
      code: 'review_execution_contaminated',
      message: '当前审阅报告混入了执行故障，需要重新审阅',
    }],
  }), true)
})

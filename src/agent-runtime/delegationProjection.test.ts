import assert from 'node:assert/strict'
import test from 'node:test'
import {
  delegationStatus,
  mergeDelegations,
  relatedRunsToDelegations,
} from './delegationProjection.ts'

test('relatedRuns 只投影子 Run 并映射为委派视图', () => {
  const delegations = relatedRunsToDelegations([
    {
      runId: 'child-1',
      status: 'claimed',
      role: 'child',
      agentId: 'agent-a',
      agentName: 'draft-agent',
      agentTitle: '草稿',
      objective: '写出草稿。',
      createTime: '2026-09-18 08:00:00',
      unitId: 'unit-1',
      attempt: 2,
    },
    {
      runId: 'prev-root',
      status: 'done',
      role: 'previous_root',
    },
    {
      runId: 'child-2',
      status: 'waiting',
      role: 'child',
    },
  ])

  assert.deepEqual(delegations.map((item) => [item.runId, item.status]), [
    ['child-1', 'claimed'],
    ['child-2', 'running'],
  ])
  assert.equal(delegations[0].delegationId, 'run:child-1')
  assert.equal(delegations[0].unitId, 'unit-1')
  assert.equal(delegations[0].attempt, 2)
  assert.equal(delegations[1].agentName, '子 Agent')
})

test('运行状态按服务端子 Run 状态词汇归一', () => {
  assert.equal(delegationStatus('pending'), 'queued')
  assert.equal(delegationStatus('claimed'), 'claimed')
  assert.equal(delegationStatus('waiting'), 'running')
  assert.equal(delegationStatus('completed'), 'done')
  assert.equal(delegationStatus('canceled'), 'canceled')
  assert.equal(delegationStatus('crashed'), 'failed')
})

test('mergeDelegations 以投影状态为准合并运行时委派', () => {
  const runtime = [
    {
      delegationId: 'run:child-1',
      runId: 'child-1',
      agentName: 'draft-agent',
      agentTitle: null,
      objective: '写出草稿。',
      status: 'running' as const,
      required: true,
      priority: 0,
    },
  ]
  const projected = relatedRunsToDelegations([
    { runId: 'child-1', status: 'done', role: 'child' },
    { runId: 'child-2', status: 'done', role: 'child' },
  ])

  const merged = mergeDelegations(runtime, projected)

  assert.deepEqual(merged?.map((item) => [item.runId, item.status]), [
    ['child-1', 'done'],
    ['child-2', 'done'],
  ])
  // 运行时补充字段（agentName 等）在合并中被保留。
  assert.equal(merged?.[0].agentName, 'draft-agent')
  assert.equal(mergeDelegations(undefined, []), undefined)
})

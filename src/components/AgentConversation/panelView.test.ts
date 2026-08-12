import assert from 'node:assert/strict'
import test from 'node:test'
import { buildAgentConversationPanelView } from './panelView.ts'

test('panel shows queue mode and hides a terminal task capsule', () => {
  const view = buildAgentConversationPanelView({
    running: true,
    queuedCount: 2,
    taskPlan: { title: '完成', status: 'done', steps: [] },
    submitMode: 'queue',
  })
  assert.equal(view.submitLabel, '加入发送队列')
  assert.equal(view.queueLabel, '排队 2')
  assert.equal(view.showTaskProgress, false)
})

test('panel keeps sequential and parallel task labels in the shared selector', () => {
  const sequential = buildAgentConversationPanelView({
    running: true,
    queuedCount: 0,
    taskPlan: {
      title: '任务',
      status: 'running',
      steps: [
        { id: 'read-1', title: '读取', type: 'read', status: 'done' },
        { id: 'write-1', title: '写作', type: 'write', status: 'running' },
        { id: 'review-1', title: '审阅', type: 'review', status: 'pending' },
      ],
    },
    submitMode: 'send',
  })
  assert.equal(sequential.taskCountLabel, '第 2/3 步')
})

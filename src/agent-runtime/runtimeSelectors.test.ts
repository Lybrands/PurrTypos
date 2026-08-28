// @ts-nocheck
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getActiveTaskPlan,
  getTaskPlanCountLabel,
  getTaskPlanProgress,
  getVisibleTaskPlanSteps,
  shouldShowTaskPlan,
} from './taskPlan.ts'
import { buildHistoryConverter } from './chatHistory.ts'
import { calculateContextUsage } from './contextUsage.ts'
import {
  buildNovelAnalysisTaskPlan,
  buildNovelAnalysisTiming,
} from '../NovelSourcesPage/analysisTaskPlan.ts'
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

test('context indicator uses the final prepared input estimate including tool schemas', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        outputReserveTokens: 8000,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.deepEqual(usage, {
    usedTokens: 13000,
    windowTokens: 200000,
    inputCapacityTokens: 192000,
    outputReserveTokens: 8000,
    ratio: 13000 / 200000,
    source: 'estimate',
  })
})

test('context indicator uses provider input as the current-context baseline', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        actualInputTokens: 1234,
        outputReserveTokens: 8000,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.equal(usage.usedTokens, 1234)
  assert.equal(usage.inputCapacityTokens, 192000)
  assert.equal(usage.source, 'provider')
})

test('a newly prepared request replaces the previous provider usage', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 12000,
        toolSchemaTokens: 1000,
        actualInputTokens: 1234,
      },
    },
    { role: 'user', content: 'new question' },
    {
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        estimatedInputTokens: 50000,
        toolSchemaTokens: 10000,
        outputReserveTokens: 8000,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.equal(usage.usedTokens, 60000)
  assert.equal(usage.source, 'estimate')
})

test('first request exposes its prepared input estimate before provider usage', () => {
  const usage = calculateContextUsage({
    messages: [
      { role: 'user', content: 'first question' },
      {
        role: 'assistant',
        content: '',
        model: 'test-model',
        contextBudget: {
          windowTokens: 200000,
          estimatedInputTokens: 50000,
          toolSchemaTokens: 10000,
          outputReserveTokens: 8000,
        },
      },
    ],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.deepEqual(usage, {
    usedTokens: 60000,
    windowTokens: 200000,
    inputCapacityTokens: 192000,
    outputReserveTokens: 8000,
    ratio: 60000 / 200000,
    source: 'estimate',
  })
})

test('switching to a different model window keeps the current conversation estimate', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      model: 'previous-model',
      contextBudget: {
        windowTokens: 256000,
        actualInputTokens: 18921,
      },
    }],
    windowTokens: 1000000,
    modelName: 'deepseek-v4-flash',
  })

  assert.ok(usage.usedTokens > 18921)
  assert.equal(usage.windowTokens, 1000000)
  assert.equal(usage.inputCapacityTokens, 1000000)
  assert.equal(usage.outputReserveTokens, 0)
  assert.equal(usage.source, 'estimate')
})

test('models with the same window keep usage but do not claim provider calibration', () => {
  const usage = calculateContextUsage({
    messages: [{
      role: 'assistant',
      content: 'previous answer',
      model: 'glm-5.2',
      contextBudget: {
        windowTokens: 1000000,
        estimatedInputTokens: 20000,
        toolSchemaTokens: 5000,
        actualInputTokens: 24000,
      },
    }],
    windowTokens: 1000000,
    modelConfigId: 'builtin_deepseek_deepseek_v4_flash',
    modelName: 'deepseek-v4-flash',
  })

  assert.ok(usage.usedTokens > 24000)
  assert.equal(usage.windowTokens, 1000000)
  assert.equal(usage.inputCapacityTokens, 1000000)
  assert.equal(usage.outputReserveTokens, 0)
  assert.equal(usage.source, 'estimate')
})

test('active request switches to actual usage once the provider reports it', () => {
  const usage = calculateContextUsage({
    messages: [
      { role: 'user', content: 'new question' },
      {
        role: 'assistant',
        content: 'partial answer',
        model: 'test-model',
        contextBudget: {
          windowTokens: 200000,
          estimatedInputTokens: 50000,
          toolSchemaTokens: 10000,
          actualInputTokens: 1234,
          outputReserveTokens: 8000,
        },
      },
    ],
    windowTokens: 200000,
    modelName: 'test-model',
  })

  assert.ok(usage.usedTokens > 1234)
  assert.equal(usage.source, 'provider')
})

test('context indicator grows with the assistant content already streamed', () => {
  const baseParams = {
    messages: [{
      role: 'assistant',
      content: '',
      model: 'test-model',
      contextBudget: {
        windowTokens: 200000,
        actualInputTokens: 1234,
      },
    }],
    windowTokens: 200000,
    modelName: 'test-model',
  }
  const beforeStreaming = calculateContextUsage(baseParams)
  const afterStreaming = calculateContextUsage({
    ...baseParams,
    messages: [{
      ...baseParams.messages[0],
      streamingContent: '这是已经接收的流式回答',
    }],
  })

  assert.ok(afterStreaming.usedTokens > beforeStreaming.usedTokens)
  assert.equal(afterStreaming.source, 'provider')
})

test('current input draft is excluded until the request is sent', () => {
  const baseParams = {
    messages: [
      { role: 'user', content: '已有问题' },
      { role: 'assistant', content: '已有回答' },
    ],
    windowTokens: 1000000,
    modelName: 'deepseek-v4-flash',
  }
  const withoutDraft = calculateContextUsage(baseParams)
  const withDraft = calculateContextUsage({
    ...baseParams,
    draft: '这是输入框里尚未发送的新问题',
  })

  assert.equal(withDraft.usedTokens, withoutDraft.usedTokens)
  assert.equal(withDraft.source, 'estimate')
})

test('task header does not reuse a completed plan from the previous turn', () => {
  const completedPlan = {
    title: 'previous task',
    status: 'done',
    steps: [
      { id: '1', title: 'first', type: 'analyze', status: 'done' },
      { id: '2', title: 'second', type: 'review', status: 'done' },
    ],
  }
  const conversations = [
    { role: 'user', content: 'previous question' },
    { role: 'assistant', content: 'previous answer', taskPlan: completedPlan },
    { role: 'user', content: 'new question' },
    { role: 'assistant', content: '' },
  ]

  assert.equal(getActiveTaskPlan(conversations, true), undefined)

  const currentPlan = {
    title: 'current task',
    status: 'running',
    steps: [
      {
        id: 'private-begin',
        title: 'begin private artifact',
        type: 'write',
        executor: 'tool',
        status: 'done',
        protocolPrivate: true,
      },
      {
        id: 'read',
        title: 'read context',
        type: 'read',
        executor: 'tool',
        status: 'done',
      },
      {
        id: 'analyze',
        title: 'analyze evidence',
        type: 'analyze',
        executor: 'model',
        status: 'running',
      },
      {
        id: 'review',
        title: 'review result',
        type: 'review',
        executor: 'model',
        status: 'pending',
      },
      {
        id: 'respond',
        title: 'Respond',
        type: 'review',
        status: 'pending',
      },
    ],
  }
  conversations[3] = { ...conversations[3], taskPlan: currentPlan }
  assert.equal(getActiveTaskPlan(conversations, true), currentPlan)
  assert.deepEqual(
    getVisibleTaskPlanSteps(currentPlan).map((step) => step.id),
    ['read', 'analyze', 'review'],
  )
  assert.equal(shouldShowTaskPlan(currentPlan), true)
  assert.equal(getActiveTaskPlan(conversations, false), currentPlan)

  const shortPlan = {
    ...currentPlan,
    steps: currentPlan.steps.slice(2),
  }
  conversations[3] = { ...conversations[3], taskPlan: shortPlan }
  assert.equal(getVisibleTaskPlanSteps(shortPlan).length, 2)
  assert.equal(shouldShowTaskPlan(shortPlan), false)
  assert.equal(getActiveTaskPlan(conversations, true), undefined)
})

test('sequential task progress presents the current visible step ordinal', () => {
  const plan = {
    title: 'execute plan',
    status: 'running',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'done' },
      { id: 'two', title: 'two', type: 'analyze', status: 'pending' },
      { id: 'three', title: 'three', type: 'write', status: 'running' },
      { id: 'four', title: 'four', type: 'review', status: 'pending' },
      { id: 'respond', title: 'Respond', type: 'review', status: 'pending' },
    ],
  }
  const progress = getTaskPlanProgress(plan)

  assert.equal(progress.completed, 1)
  assert.equal(progress.total, 4)
  assert.equal(progress.currentStep.id, 'three')
  assert.equal(progress.currentStepIndex, 3)
  assert.deepEqual(progress.runningSteps.map((step) => step.id), ['three'])
  assert.equal(progress.percent, 25)
  assert.equal(getTaskPlanCountLabel(plan), '第 3/4 步')
})

test('task progress exposes concurrent Planner Agent steps as one frontier', () => {
  const progress = getTaskPlanProgress({
    title: 'parallel plan',
    status: 'running',
    steps: [
      { id: 'write-5', title: 'write 5', type: 'write', executor: 'agent', status: 'running' },
      { id: 'write-6', title: 'write 6', type: 'write', executor: 'agent', status: 'running' },
      { id: 'write-7', title: 'write 7', type: 'write', executor: 'agent', status: 'running' },
      { id: 'submit', title: 'submit', type: 'write', executor: 'tool', status: 'pending' },
    ],
  })

  assert.deepEqual(
    progress.runningSteps.map((step) => step.id),
    ['write-5', 'write-6', 'write-7'],
  )
  assert.equal(progress.currentStep.id, 'write-5')
  assert.equal(progress.completed, 0)
  assert.equal(progress.total, 4)
})

test('parallel task progress presents concurrency and completed count', () => {
  const plan = {
    title: 'parallel plan',
    status: 'running',
    steps: [
      { id: 'read-1', title: 'read 1', type: 'read', status: 'done' },
      { id: 'read-2', title: 'read 2', type: 'read', status: 'done' },
      { id: 'write-3', title: 'write 3', type: 'write', status: 'running' },
      { id: 'write-4', title: 'write 4', type: 'write', status: 'running' },
      { id: 'write-5', title: 'write 5', type: 'write', status: 'running' },
      { id: 'submit', title: 'submit', type: 'write', status: 'pending' },
    ],
  }

  assert.equal(getTaskPlanCountLabel(plan), '并行 3 项 · 已完成 2/6')
})

test('planned task progress points at the next sequential step', () => {
  const plan = {
    title: 'planned task',
    status: 'planned',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'pending' },
      { id: 'two', title: 'two', type: 'analyze', status: 'pending' },
      { id: 'three', title: 'three', type: 'write', status: 'pending' },
      { id: 'four', title: 'four', type: 'review', status: 'pending' },
    ],
  }

  assert.equal(getTaskPlanCountLabel(plan), '第 1/4 步')
})

test('completed task capsule exists only while the final answer is streaming', () => {
  const plan = {
    title: 'completed task',
    status: 'done',
    steps: [
      { id: 'one', title: 'one', type: 'read', status: 'done' },
      { id: 'two', title: 'two', type: 'analyze', status: 'done' },
      { id: 'three', title: 'three', type: 'write', status: 'done' },
      { id: 'four', title: 'four', type: 'review', status: 'done' },
    ],
  }
  const conversations = [{ role: 'assistant', content: '总结输出中', taskPlan: plan }]

  assert.equal(getTaskPlanCountLabel(plan), '已完成 4/4')
  assert.equal(getActiveTaskPlan(conversations, true), plan)
  assert.equal(getActiveTaskPlan(conversations, false), undefined)
  assert.equal(getActiveTaskPlan([
    ...conversations,
    { role: 'assistant', content: '新的历史消息' },
  ], true), undefined)
})

test('novel-analysis timing resumes the shared ticker and freezes terminal duration', () => {
  const startedAt = Date.parse('2026-08-28T10:00:00Z')
  const baseRun = {
    runId: 'run-1', commandId: 'command-1', taskId: 'task-1', taskRevision: 1,
    totalUnits: 1, completedUnits: 0, failedUnits: 0,
    createTime: '2026-08-28 10:00:00',
  }

  assert.deepEqual(buildNovelAnalysisTiming({
    ...baseRun, runStatus: 'running', taskStatus: 'running',
  }, startedAt + 6500, 15000), { turnStartedAt: 8500 })
  assert.deepEqual(buildNovelAnalysisTiming({
    ...baseRun, runStatus: 'failed', taskStatus: 'failed',
    updateTime: '2026-08-28 10:00:09.250',
  }, startedAt + 20000, 30000), { durationMs: 9250 })
})

test('pending novel-analysis protocol units stay pending', () => {
  const plan = buildNovelAnalysisTaskPlan({
    runId: 'run-1', runStatus: 'running', commandId: 'command-1',
    taskId: 'task-1', taskStatus: 'running', taskRevision: 1,
    totalUnits: 2, completedUnits: 0, failedUnits: 0,
    units: [{
      unitId: 'extract', title: '分析章节', kind: 'extract_section',
      status: 'running', attempt: 0, maxAttempts: 2,
    }, {
      unitId: 'review', title: '形成结果', kind: 'build_review_artifact',
      status: 'pending', attempt: 0, maxAttempts: 1,
    }],
  })

  assert.deepEqual(plan.steps.map((step) => step.status), ['running', 'pending'])
})

test('novel-analysis semantic steps aggregate mapped durable units', () => {
  const plan = buildNovelAnalysisTaskPlan({
    runId: 'run-1', runStatus: 'running', commandId: 'command-1',
    taskId: 'task-1', taskStatus: 'running', taskRevision: 1,
    totalUnits: 3, completedUnits: 2, failedUnits: 0,
    analysisPlan: {
      title: '人物与因果分析', goal: '核对事实链',
      steps: [{
        id: 'facts', title: '梳理事实链', type: 'analyze', executor: 'model',
        dependsOn: [],
      }, {
        id: 'review', title: '复核证据', type: 'review', executor: 'model',
        dependsOn: ['facts'],
      }],
    },
    units: [{
      unitId: 'extract', title: '分析章节', kind: 'extract_section',
      plannerStepId: 'facts', status: 'completed', attempt: 1, maxAttempts: 2,
    }, {
      unitId: 'aggregate', title: '聚合事实', kind: 'aggregate_story',
      plannerStepId: 'facts', status: 'completed', attempt: 1, maxAttempts: 2,
    }, {
      unitId: 'review', title: '形成结果', kind: 'build_review_artifact',
      plannerStepId: 'review', status: 'running', attempt: 1, maxAttempts: 1,
    }],
  })

  assert.equal(plan.title, '人物与因果分析')
  assert.deepEqual(plan.steps.map((step) => step.status), ['done', 'running'])
})

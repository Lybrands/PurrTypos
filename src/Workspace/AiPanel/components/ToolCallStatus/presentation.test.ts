import assert from 'node:assert/strict'
import test from 'node:test'

test('tool presentation exposes direct completed running and pending rows', async () => {
  const { buildToolCallRows } = await import('./presentation.ts')

  const rows = buildToolCallRows({
    labels: ['读取人物资料', '检查人物弧光', '写入候选稿'],
    completedToolCount: 1,
  })

  assert.deepEqual(rows, [
    {
      index: 0,
      label: '读取人物资料',
      outcome: 'ok',
      phase: 'done',
      text: '已完成 读取人物资料',
    },
    {
      index: 1,
      label: '检查人物弧光',
      outcome: 'ok',
      phase: 'running',
      text: '正在执行 检查人物弧光',
    },
    {
      index: 2,
      label: '写入候选稿',
      outcome: 'ok',
      phase: 'pending',
      text: '待执行 写入候选稿',
    },
  ])
  assert.doesNotMatch(
    rows.map((row) => row.text).join('\n'),
    /已操作|正在操作|准备操作/,
  )
})

test('tool presentation omits cached rows and preserves context errors', async () => {
  const { buildToolCallRows } = await import('./presentation.ts')

  const rows = buildToolCallRows({
    labels: ['缓存读取', '无效章节'],
    cachedFlags: [true, false],
    labelOutcomes: ['ok', 'context_error'],
    completedToolCount: 2,
  })

  assert.deepEqual(rows, [{
    index: 1,
    label: '无效章节',
    outcome: 'context_error',
    phase: 'done',
    text: '失败：无效章节— 信息有误（当前书籍章节目录中无对应章节或工具参数无效）',
  }])
})

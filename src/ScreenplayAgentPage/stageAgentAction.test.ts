import assert from 'node:assert/strict'
import test from 'node:test'

import type { ScreenplayProject } from '../types.ts'

function project(
  values: Partial<ScreenplayProject> & Pick<ScreenplayProject, 'active_stage'>,
): ScreenplayProject {
  return {
    id: 'project-1',
    title: '测试剧本',
    source_kind: 'original',
    source_book_id: null,
    source_scope: {
      schemaVersion: 1,
      mode: 'whole_book',
      requestedCount: null,
      chapterIds: [],
      volumeIds: [],
      chapters: [],
    },
    format: '电影',
    approach: '',
    premise: '',
    delivery_manifest: null,
    status: 'active',
    ...values,
  }
}

test('automatic stage actions contain only the clicked user action', async () => {
  const { stageAgentAction } = await import('./stageAgentAction.ts')
  const cases = [
    {
      input: {
        project: project({
          active_stage: 'orientation',
          source_kind: 'book',
          source_book_id: 'book-1',
        }),
      },
      expected: '开始分析',
    },
    {
      input: { project: project({ active_stage: 'brief' }) },
      expected: '生成创作简报',
    },
    {
      input: {
        project: project({ active_stage: 'structure', format: '连续剧' }),
      },
      expected: '设计分集结构',
    },
    {
      input: {
        project: project({ active_stage: 'structure', format: '电影' }),
      },
      expected: '设计故事节拍',
    },
    {
      input: {
        project: project({ active_stage: 'draft', format: '连续剧' }),
        draftScope: 'next_3_episodes' as const,
      },
      expected: '连续创作 3 集',
    },
    {
      input: {
        project: project({ active_stage: 'draft', format: '连续剧' }),
        draftScope: 'all_remaining' as const,
      },
      expected: '创作全部剩余正文',
    },
    {
      input: {
        project: project({ active_stage: 'review' }),
        reviewState: {
          phase: 'readyToFinalize' as const,
          recommendation: 'revise' as const,
          hardChecks: [{
            code: 'review_input_unverified',
            message: '当前审阅报告没有可验证的正文输入',
          }],
        },
      },
      expected: '重新审阅',
    },
  ]

  const actual = cases.map(({ input }) => stageAgentAction(input))

  assert.deepEqual(actual, cases.map(({ expected }) => expected))
  for (const action of actual) {
    assert.doesNotMatch(
      action,
      /不得沿用|重点检查|并生成可应用|只有缺少/,
    )
  }
})

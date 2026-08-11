import assert from 'node:assert/strict'
import test from 'node:test'

import type {
  ScreenplayDocument,
  ScreenplayDocumentEpisode,
  ScreenplayDraftEpisode,
  ScreenplayProject,
} from '../types.ts'

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
      expected: {
        label: '开始分析',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'sourceAnalysis',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: { project: project({ active_stage: 'orientation' }) },
      expected: {
        label: '生成创作简报',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'creativeBrief',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: { project: project({ active_stage: 'brief' }) },
      expected: {
        label: '生成创作简报',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'creativeBrief',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: {
        project: project({ active_stage: 'structure', format: '连续剧' }),
      },
      expected: {
        label: '设计分集结构',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'structure',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: {
        project: project({ active_stage: 'structure', format: '电影' }),
      },
      expected: {
        label: '设计故事节拍',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'structure',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: { project: project({ active_stage: 'scenes' }) },
      expected: {
        label: '生成场景表',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'sceneList',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: { project: project({ active_stage: 'draft', format: '连续剧' }) },
      expected: {
        label: '创作下一集',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'screenplayDraft',
          scope: { kind: 'next_episodes', count: 1 },
        },
      },
    },
    {
      input: {
        project: project({ active_stage: 'draft', format: '连续剧' }),
        draftScope: 'next_3_episodes' as const,
      },
      expected: {
        label: '连续创作 3 集',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'screenplayDraft',
          scope: { kind: 'next_episodes', count: 3 },
        },
      },
    },
    {
      input: {
        project: project({ active_stage: 'draft', format: '连续剧' }),
        draftScope: 'all_remaining' as const,
      },
      expected: {
        label: '创作全部剩余正文',
        stageCommand: {
          kind: 'stage_action',
          action: 'create',
          targetRole: 'screenplayDraft',
          scope: { kind: 'all_remaining' },
        },
      },
    },
    {
      input: {
        project: project({ active_stage: 'review' }),
        reviewState: {
          phase: 'awaitingReview' as const,
          recommendation: null,
        },
      },
      expected: {
        label: '开始审阅',
        stageCommand: {
          kind: 'stage_action',
          action: 'review',
          targetRole: 'review',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: {
        project: project({ active_stage: 'review' }),
        reviewState: {
          phase: 'adjudicating' as const,
          recommendation: 'ready' as const,
        },
      },
      expected: { label: '处理审阅意见' },
    },
    {
      input: {
        project: project({ active_stage: 'review' }),
        reviewState: {
          phase: 'readyToRevise' as const,
          recommendation: 'revise' as const,
        },
      },
      expected: {
        label: '开始修订',
        stageCommand: {
          kind: 'stage_action',
          action: 'revise',
          targetRole: 'screenplayDraft',
          scope: { kind: 'current_stage' },
        },
      },
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
      expected: {
        label: '重新审阅',
        stageCommand: {
          kind: 'stage_action',
          action: 'review',
          targetRole: 'review',
          scope: { kind: 'current_stage' },
        },
      },
    },
    {
      input: { project: project({ active_stage: 'completed' }) },
      expected: { label: '创作已完成' },
    },
  ]

  const actual = cases.map(({ input }) => stageAgentAction(input))

  assert.deepEqual(actual, cases.map(({ expected }) => expected))
  for (const action of actual) {
    assert.doesNotMatch(
      action.label,
      /不得沿用|重点检查|并生成可应用|只有缺少/,
    )
  }
})

test('an exhausted draft advances through a formal review command', async () => {
  const { stageAgentAction } = await import('./stageAgentAction.ts')
  const sceneList = {
    id: 'scene-list-1',
    kind: 'scene_list',
    status: 'accepted',
  } as ScreenplayDocument
  const documentEpisode = {
    document_id: sceneList.id,
    item_ids: ['scene-1'],
  } as ScreenplayDocumentEpisode
  const draftEpisode = {
    scene_ids: ['scene-1'],
  } as ScreenplayDraftEpisode

  assert.deepEqual(stageAgentAction({
    project: project({ active_stage: 'draft', format: '连续剧' }),
    documents: [sceneList],
    documentEpisodes: [documentEpisode],
    draftEpisodes: [draftEpisode],
  }), {
    label: '完成剧本正文',
    stageCommand: {
      kind: 'stage_action',
      action: 'review',
      targetRole: 'review',
      scope: { kind: 'current_stage' },
    },
  })
})

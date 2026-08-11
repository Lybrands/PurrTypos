import type {
  ScreenplayDocument,
  ScreenplayDocumentEpisode,
  ScreenplayDraftEpisode,
  ScreenplayFormat,
  ScreenplayProject,
  ScreenplayStageCommand,
} from '../types.ts'
import {
  draftEpisodeCountFromScope,
  type ScreenplayDraftScope,
} from './draftBatchIntent.ts'
import { reviewPrimaryAction } from './reviewAdjudicationModel.ts'

const SERIES_FORMATS = new Set<ScreenplayFormat>(['连续剧', '竖屏短剧'])

type ReviewActionState = Parameters<typeof reviewPrimaryAction>[0]

export interface StageAgentActionInput {
  project: ScreenplayProject
  documents?: ScreenplayDocument[]
  draftEpisodes?: ScreenplayDraftEpisode[]
  documentEpisodes?: ScreenplayDocumentEpisode[]
  reviewState?: ReviewActionState
  draftScope?: ScreenplayDraftScope
}

export interface StageAgentAction {
  label: string
  stageCommand?: ScreenplayStageCommand
}

function action(
  label: string,
  commandAction: ScreenplayStageCommand['action'],
  targetRole: ScreenplayStageCommand['targetRole'],
  scope: ScreenplayStageCommand['scope'],
): StageAgentAction {
  return {
    label,
    stageCommand: {
      kind: 'stage_action',
      action: commandAction,
      targetRole,
      scope,
    },
  }
}

function fallbackReviewAction(documents: ScreenplayDocument[]): string {
  const acceptedDraft = [...documents].reverse().find(
    (document) => document.kind === 'scene_draft' && document.status === 'accepted',
  )
  const acceptedReview = [...documents].reverse().find(
    (document) => document.kind === 'review' && document.status === 'accepted',
  )
  if (
    acceptedReview
    && String(acceptedReview.content_json?.reviewedDraftId || '')
      === String(acceptedDraft?.id || '')
    && acceptedReview.content_json?.verdict !== 'ready'
  ) {
    return '开始修订'
  }
  if (acceptedDraft?.content_json?.reviewId) return '开始复审'
  return '开始审阅'
}

export function stageAgentAction({
  project,
  documents = [],
  draftEpisodes = [],
  documentEpisodes = [],
  reviewState,
  draftScope = 'next_episode',
}: StageAgentActionInput): StageAgentAction {
  if (project.active_stage === 'completed') return { label: '创作已完成' }
  if (project.active_stage === 'orientation') {
    return project.source_kind === 'book'
      ? action('开始分析', 'create', 'sourceAnalysis', { kind: 'current_stage' })
      : action('生成创作简报', 'create', 'creativeBrief', { kind: 'current_stage' })
  }
  if (project.active_stage === 'brief') {
    return action('生成创作简报', 'create', 'creativeBrief', { kind: 'current_stage' })
  }
  if (project.active_stage === 'structure') {
    return action(
      SERIES_FORMATS.has(project.format) ? '设计分集结构' : '设计故事节拍',
      'create',
      'structure',
      { kind: 'current_stage' },
    )
  }
  if (project.active_stage === 'scenes') {
    return action('生成场景表', 'create', 'sceneList', { kind: 'current_stage' })
  }
  if (project.active_stage === 'draft') {
    if (draftScope === 'all_remaining') {
      return action(
        '创作全部剩余正文',
        'create',
        'screenplayDraft',
        { kind: 'all_remaining' },
      )
    }
    const draftEpisodeCount = draftEpisodeCountFromScope(draftScope)
    if (draftEpisodeCount != null && draftEpisodeCount > 1) {
      return action(
        `连续创作 ${draftEpisodeCount} 集`,
        'create',
        'screenplayDraft',
        { kind: 'next_episodes', count: draftEpisodeCount },
      )
    }
    const sceneList = [...documents].reverse().find(
      (document) => document.kind === 'scene_list' && document.status === 'accepted',
    )
    const sceneCount = documentEpisodes
      .filter((episode) => episode.document_id === sceneList?.id)
      .reduce((count, episode) => count + episode.item_ids.length, 0)
    const completedCount = draftEpisodes.reduce(
      (count, episode) => count + episode.scene_ids.length,
      0,
    )
    if (sceneCount > 0 && completedCount >= sceneCount) {
      return action(
        '完成剧本正文',
        'review',
        'review',
        { kind: 'current_stage' },
      )
    }
    return action(
      SERIES_FORMATS.has(project.format) ? '创作下一集' : '创作正文',
      'create',
      'screenplayDraft',
      { kind: 'next_episodes', count: 1 },
    )
  }
  if (reviewState) {
    const reviewAction = reviewPrimaryAction(reviewState)
    if (reviewAction.kind === 'startReview') {
      return action(
        reviewAction.label,
        'review',
        'review',
        { kind: 'current_stage' },
      )
    }
    if (reviewAction.kind === 'startRevision') {
      return action(
        reviewAction.label,
        'revise',
        'screenplayDraft',
        { kind: 'current_stage' },
      )
    }
    return { label: reviewAction.label }
  }
  const fallback = fallbackReviewAction(documents)
  return fallback === '开始修订'
    ? action(fallback, 'revise', 'screenplayDraft', { kind: 'current_stage' })
    : action(fallback, 'review', 'review', { kind: 'current_stage' })
}

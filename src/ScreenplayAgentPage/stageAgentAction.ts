import type {
  ScreenplayDocument,
  ScreenplayDocumentEpisode,
  ScreenplayDraftEpisode,
  ScreenplayFormat,
  ScreenplayProject,
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
}: StageAgentActionInput): string {
  if (project.active_stage === 'completed') return '创作已完成'
  if (project.active_stage === 'orientation') {
    return project.source_kind === 'book' ? '开始分析' : '生成创作简报'
  }
  if (project.active_stage === 'brief') return '生成创作简报'
  if (project.active_stage === 'structure') {
    return SERIES_FORMATS.has(project.format) ? '设计分集结构' : '设计故事节拍'
  }
  if (project.active_stage === 'scenes') return '生成场景表'
  if (project.active_stage === 'draft') {
    if (draftScope === 'all_remaining') return '创作全部剩余正文'
    const draftEpisodeCount = draftEpisodeCountFromScope(draftScope)
    if (draftEpisodeCount != null && draftEpisodeCount > 1) {
      return `连续创作 ${draftEpisodeCount} 集`
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
    if (sceneCount > 0 && completedCount >= sceneCount) return '完成剧本正文'
    return SERIES_FORMATS.has(project.format) ? '创作下一集' : '创作正文'
  }
  if (reviewState) return reviewPrimaryAction(reviewState).label
  return fallbackReviewAction(documents)
}

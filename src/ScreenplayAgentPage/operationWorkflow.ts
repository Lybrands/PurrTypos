import type {
  ScreenplayProject,
  ScreenplayDocumentKind,
  ScreenplayFormat,
  ScreenplaySourceKind,
  ScreenplaySourceScope,
  ScreenplaySourceScopeRequest,
  ScreenplayStage,
  ScreenplayV2DeliverableRole,
  ScreenplayV2Format,
  ScreenplayV2OperationIntentType,
  ScreenplayV2Project,
  ScreenplayV2RevisionSummary,
  ScreenplayV2Workspace,
} from '../types'
import {
  draftEpisodeCountFromScope,
  type ScreenplayDraftScope,
} from './draftBatchIntent.ts'

const FORMAT_TO_V2: Record<ScreenplayFormat, ScreenplayV2Format> = {
  短片: 'shortFilm',
  电影: 'featureFilm',
  单集剧: 'singleEpisode',
  连续剧: 'series',
  竖屏短剧: 'verticalSeries',
}

const FORMAT_FROM_V2: Record<ScreenplayV2Format, ScreenplayFormat> = {
  shortFilm: '短片',
  featureFilm: '电影',
  singleEpisode: '单集剧',
  series: '连续剧',
  verticalSeries: '竖屏短剧',
}

const SCOPE_MODE_FROM_V2 = {
  wholeBook: 'whole_book',
  firstChapters: 'first_chapters',
  firstVolumes: 'first_volumes',
  selectedChapters: 'selected_chapters',
  selectedVolumes: 'selected_volumes',
} as const

const SCOPE_MODE_TO_V2 = {
  whole_book: 'wholeBook',
  first_chapters: 'firstChapters',
  first_volumes: 'firstVolumes',
  selected_chapters: 'selectedChapters',
  selected_volumes: 'selectedVolumes',
} as const

export function projectFromWorkspace(
  _project: ScreenplayProject,
  workspace: ScreenplayV2Workspace,
): ScreenplayProject {
  return projectFromV2Project(workspace.project)
}

export function projectFromV2Project(
  project: ScreenplayV2Project,
): ScreenplayProject {
  const rawScope = project.source.scope || {}
  const rawMode = String(rawScope.mode || 'wholeBook') as keyof typeof SCOPE_MODE_FROM_V2
  const sourceScope: ScreenplaySourceScope = {
    schemaVersion: 1,
    mode: SCOPE_MODE_FROM_V2[rawMode] || 'whole_book',
    requestedCount: typeof rawScope.requestedCount === 'number'
      ? rawScope.requestedCount
      : null,
    chapterIds: Array.isArray(rawScope.chapterIds)
      ? rawScope.chapterIds.map(String)
      : [],
    volumeIds: Array.isArray(rawScope.volumeIds)
      ? rawScope.volumeIds.map(String)
      : [],
    chapters: Array.isArray(rawScope.chapters)
      ? rawScope.chapters as ScreenplaySourceScope['chapters']
      : [],
  }
  return {
    id: project.id,
    title: project.title,
    source_kind: project.source.type === 'book' ? 'book' : 'original',
    source_book_id: typeof project.source.bookId === 'string'
      ? project.source.bookId
      : null,
    source_scope: sourceScope,
    format: FORMAT_FROM_V2[project.format],
    approach: project.brief.approach,
    premise: project.brief.premise,
    delivery_manifest: null,
    active_stage: project.stage,
    status: project.lifecycle === 'archived' ? 'archived' : 'active',
    revision: project.revision,
    create_time: project.createdAt || undefined,
    update_time: project.updatedAt || undefined,
  }
}

export function screenplayFormatToV2(format: ScreenplayFormat): ScreenplayV2Format {
  return FORMAT_TO_V2[format]
}

export function screenplaySourceToV2(
  sourceKind: ScreenplaySourceKind,
  sourceBookId: string | null,
  sourceScope?: ScreenplaySourceScopeRequest,
) {
  if (sourceKind === 'original') return { type: 'original' as const }
  if (!sourceBookId) throw new Error('book screenplay source requires a book id')
  const scope = sourceScope ?? { mode: 'whole_book' as const }
  return {
    type: 'book' as const,
    bookId: sourceBookId,
    scope: {
      mode: SCOPE_MODE_TO_V2[scope.mode],
      ...(scope.count != null ? { count: scope.count } : {}),
      ...(scope.chapterIds?.length ? { chapterIds: scope.chapterIds } : {}),
      ...(scope.volumeIds?.length ? { volumeIds: scope.volumeIds } : {}),
    },
  }
}

export function operationTargetForStage(
  stage: ScreenplayStage,
  sourceKind: ScreenplaySourceKind,
): ScreenplayV2DeliverableRole | null {
  if (stage === 'orientation') {
    return sourceKind === 'book' ? 'sourceAnalysis' : 'creativeBrief'
  }
  const targetByStage: Record<
    Exclude<ScreenplayStage, 'orientation'>,
    ScreenplayV2DeliverableRole | null
  > = {
    brief: 'creativeBrief',
    structure: 'structure',
    scenes: 'sceneList',
    draft: 'screenplayDraft',
    review: 'review',
    completed: null,
  }
  return targetByStage[stage]
}

export function operationRoleForProposal(
  kind: ScreenplayDocumentKind,
): ScreenplayV2DeliverableRole {
  const roleByKind: Record<
    ScreenplayDocumentKind,
    ScreenplayV2DeliverableRole
  > = {
    source_analysis: 'sourceAnalysis',
    creative_brief: 'creativeBrief',
    beat_sheet: 'structure',
    episode_outline: 'structure',
    scene_list: 'sceneList',
    scene_draft: 'screenplayDraft',
    review: 'review',
  }
  return roleByKind[kind]
}

export function operationIntentForTask(input: {
  stage: ScreenplayStage
  prompt: string
  draftScope: ScreenplayDraftScope
  draftSceneCount: number
}): {
  type: ScreenplayV2OperationIntentType
  scope: Record<string, unknown>
  instruction: string
} {
  const type: ScreenplayV2OperationIntentType = input.stage === 'draft'
    ? 'continue'
    : input.stage === 'review'
      ? 'review'
      : 'generate'
  return {
    type,
    scope: input.stage === 'draft'
      ? {
          mode: input.draftScope,
          sceneCount: input.draftSceneCount,
        }
      : {},
    instruction: input.prompt,
  }
}

export function taskInputFromResumedOperation(input: {
  intent: Record<string, unknown>
  fallbackPrompt: string
  fallbackDraftScope: ScreenplayDraftScope
  fallbackDraftSceneCount: number
}): {
  prompt: string
  draftScope: ScreenplayDraftScope
  draftSceneCount: number
} {
  const instruction = typeof input.intent.instruction === 'string'
    ? input.intent.instruction.trim()
    : ''
  const scope = input.intent.scope && typeof input.intent.scope === 'object'
    ? input.intent.scope as Record<string, unknown>
    : {}
  const rawMode = typeof scope.mode === 'string' ? scope.mode.trim() : ''
  const fixedScopes: ScreenplayDraftScope[] = [
    'planner',
    'next_scene',
    'next_episode',
    'all_remaining',
    'count',
  ]
  const candidateScope = fixedScopes.includes(rawMode as ScreenplayDraftScope)
    || draftEpisodeCountFromScope(rawMode as ScreenplayDraftScope) != null
    ? rawMode as ScreenplayDraftScope
    : input.fallbackDraftScope
  const rawCount = scope.sceneCount
  const candidateCount = typeof rawCount === 'number'
    && Number.isInteger(rawCount)
    && rawCount > 0
    ? rawCount
    : input.fallbackDraftSceneCount
  return {
    prompt: instruction || input.fallbackPrompt,
    draftScope: candidateScope,
    draftSceneCount: ['planner', 'next_scene', 'count'].includes(candidateScope)
      ? candidateCount
      : 1,
  }
}

export function findWorkspaceRevision(input: {
  workspace: ScreenplayV2Workspace | null
  role: ScreenplayV2DeliverableRole
  revisionId?: string | null
  operationId?: string | null
  finalizingRunId?: string | null
}): ScreenplayV2RevisionSummary | null {
  if (!input.workspace) return null
  const revisions = [
    ...input.workspace.candidates,
    ...Object.values(input.workspace.workflow.heads).filter(
      (revision): revision is ScreenplayV2RevisionSummary => revision != null,
    ),
  ]
  const referenced = revisions.find((revision) => (
    Boolean(input.revisionId) && revision.id === input.revisionId
  ))
  if (referenced) return referenced
  return revisions.find((revision) => (
    revision.role === input.role
    && Boolean(input.finalizingRunId)
    && revision.finalizingRunId === input.finalizingRunId
  )) ?? revisions.find((revision) => (
    revision.role === input.role
    && Boolean(input.operationId)
    && revision.operationId === input.operationId
  )) ?? null
}

export function createScreenplayCommandId(prefix: string): string {
  const randomId = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}:${randomId}`
}

export const MAX_SCREENPLAY_DRAFT_BATCH_EPISODES = 100

export type ScreenplayEpisodeDraftScope = `next_${number}_episodes`

export type ScreenplayDraftScope =
  | 'next_episode'
  | ScreenplayEpisodeDraftScope
  | 'all_remaining'

export interface DraftBatchIntentScope {
  pendingSceneCount: number
  pendingEpisodeCount?: number
}

export interface DraftBatchAction {
  key: ScreenplayEpisodeDraftScope | 'all_remaining'
  label: string
  episodeCount: number
}

export function draftScopeForEpisodeCount(
  episodeCount: number,
): 'next_episode' | ScreenplayEpisodeDraftScope {
  const normalized = Math.trunc(episodeCount)
  if (!Number.isFinite(episodeCount) || !Number.isInteger(episodeCount)) {
    throw new RangeError('episode count must be an integer')
  }
  if (normalized < 1) {
    throw new RangeError('episode count must be positive')
  }
  if (normalized === 1) return 'next_episode'
  if (normalized > MAX_SCREENPLAY_DRAFT_BATCH_EPISODES) {
    throw new RangeError(
      `episode count must not exceed ${MAX_SCREENPLAY_DRAFT_BATCH_EPISODES}`,
    )
  }
  return `next_${normalized}_episodes`
}

export function draftEpisodeCountFromScope(
  scope: ScreenplayDraftScope,
): number | null {
  if (scope === 'next_episode') return 1
  const matched = /^next_(\d+)_episodes$/.exec(scope)
  if (!matched) return null
  const episodeCount = Number(matched[1])
  return Number.isInteger(episodeCount)
    && episodeCount >= 2
    && episodeCount <= MAX_SCREENPLAY_DRAFT_BATCH_EPISODES
    ? episodeCount
    : null
}

/** Build episode-sized ranges from the authoritative pending-scene scope. */
export function buildDraftBatchActions(
  scope: DraftBatchIntentScope,
): DraftBatchAction[] {
  const pending = Math.max(0, Math.trunc(scope.pendingSceneCount || 0))
  const pendingEpisodeCount = Math.max(
    0,
    Math.trunc(scope.pendingEpisodeCount || 0),
  )
  if (pending <= 0 || pendingEpisodeCount <= 1) return []

  const episodeRangeActions = [2, 3, 5].flatMap((episodeCount) => {
    if (pendingEpisodeCount <= episodeCount) return []
    return [{
      key: draftScopeForEpisodeCount(episodeCount) as ScreenplayEpisodeDraftScope,
      label: `连续创作 ${episodeCount} 集`,
      episodeCount,
    }]
  })
  return [
    ...episodeRangeActions,
    {
      key: 'all_remaining' as const,
      label: `创作全部剩余 ${pendingEpisodeCount} 集`,
      episodeCount: pendingEpisodeCount,
    },
  ]
}

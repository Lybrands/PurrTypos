export const MAX_SCREENPLAY_DRAFT_BATCH_SCENES = 100
export const MAX_SCREENPLAY_DRAFT_BATCH_EPISODES = 100

export type ScreenplayEpisodeDraftScope = `next_${number}_episodes`

export type ScreenplayDraftScope =
  | 'planner'
  | 'next_scene'
  | 'next_episode'
  | ScreenplayEpisodeDraftScope
  | 'all_remaining'
  | 'count'

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

/** Keep the request semantic; the backend resolves concrete scenes at run time. */
export function inferDraftScope(prompt: string): ScreenplayDraftScope {
  const normalized = prompt.replace(/\s+/g, '')
  if (!normalized) return 'planner'
  if (/(?:全部|所有).{0,8}(?:剩余|剩下|余下|场景|场次|正文|集)/.test(normalized)
    || /(?:剩余|剩下|余下).{0,8}(?:全部|所有)/.test(normalized)) {
    return 'all_remaining'
  }
  const episodeCountMatch = normalized.match(
    /(?:接下来|连续|批量|创作|完成)(?:创作|完成|写|生成|接下来)?([一二两三四五六七八九十\d]{1,3})集/,
  )
  const episodeCount = episodeCountMatch
    ? parseSceneCount(episodeCountMatch[1])
    : null
  if (
    episodeCount != null
    && episodeCount > 0
    && episodeCount <= MAX_SCREENPLAY_DRAFT_BATCH_EPISODES
  ) {
    return draftScopeForEpisodeCount(episodeCount)
  }
  if (/(?:下一集|本集|当前集)/.test(normalized)) return 'next_episode'
  if (/(?:下一场|本场|当前场)/.test(normalized)) return 'next_scene'
  return /[一二两三四五六七八九十\d]{1,3}场/.test(normalized)
    ? 'count'
    : 'planner'
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

const CHINESE_DIGITS: Record<string, number> = {
  一: 1,
  二: 2,
  两: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  七: 7,
  八: 8,
  九: 9,
  十: 10,
}

function parseSceneCount(value: string): number | null {
  if (/^\d+$/.test(value)) return Number(value)
  if (value === '十') return 10
  if (value.startsWith('十')) return 10 + (CHINESE_DIGITS[value.slice(1)] || 0)
  if (value.endsWith('十')) return (CHINESE_DIGITS[value.slice(0, -1)] || 0) * 10
  const [tens, ones] = value.split('十')
  if (ones != null) {
    return (CHINESE_DIGITS[tens] || 0) * 10 + (CHINESE_DIGITS[ones] || 0)
  }
  return CHINESE_DIGITS[value] || null
}

export function inferDraftSceneCount(
  prompt: string,
  scope: DraftBatchIntentScope,
): number {
  const pending = Math.max(
    1,
    Math.min(MAX_SCREENPLAY_DRAFT_BATCH_SCENES, Math.trunc(scope.pendingSceneCount || 1)),
  )
  const normalized = prompt.replace(/\s+/g, '')
  if (!normalized) return 1

  if (/(?:下一集|本集|当前集)/.test(normalized)) {
    return 1
  }

  if (
    /(?:本集|当前集).{0,4}(?:剩余|剩下|余下)/.test(normalized)
    || /(?:剩余|剩下|余下).{0,4}(?:本集|当前集)/.test(normalized)
  ) {
    return 1
  }

  const episodeCountMatch = normalized.match(
    /(?:接下来|连续|批量|创作|完成)([一二两三四五六七八九十\d]{1,3})集/,
  )
  const requestedEpisodes = episodeCountMatch
    ? parseSceneCount(episodeCountMatch[1])
    : null
  if (
    requestedEpisodes != null
    && requestedEpisodes > 0
  ) {
    return 1
  }

  if (
    /(?:全部|所有).{0,8}(?:剩余|剩下|余下|场景|场次|正文)/.test(normalized)
    || /(?:剩余|剩下|余下).{0,8}(?:全部|所有|场景|场次|正文)/.test(normalized)
  ) {
    return 1
  }

  const countMatch = normalized.match(
    /(?:连续|批量|一次(?:性)?|接下来|创作|写)(?:创作|完成|写|生成)?([一二两三四五六七八九十\d]{1,3})场/,
  )
  const requested = countMatch ? parseSceneCount(countMatch[1]) : null
  if (requested != null && requested > 0) {
    return Math.min(pending, MAX_SCREENPLAY_DRAFT_BATCH_SCENES, requested)
  }
  return 1
}

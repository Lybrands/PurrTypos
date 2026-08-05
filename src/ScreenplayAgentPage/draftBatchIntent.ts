export const MAX_SCREENPLAY_DRAFT_BATCH_SCENES = 100

export type ScreenplayDraftScope =
  | 'planner'
  | 'next_scene'
  | 'next_episode'
  | 'next_3_episodes'
  | 'next_5_episodes'
  | 'all_remaining'
  | 'count'

export interface DraftBatchIntentScope {
  pendingSceneCount: number
  pendingEpisodeCount?: number
}

export interface DraftBatchAction {
  key: 'next_3_episodes' | 'next_5_episodes' | 'all_remaining'
  label: string
  episodeCount: number
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
    /(?:接下来|连续|批量|创作|完成)([一二两三四五六七八九十\d]{1,3})集/,
  )
  const episodeCount = episodeCountMatch
    ? parseSceneCount(episodeCountMatch[1])
    : null
  if (episodeCount === 3) return 'next_3_episodes'
  if (episodeCount === 5) return 'next_5_episodes'
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

  const episodeRangeActions = [3, 5].flatMap((episodeCount) => {
    if (pendingEpisodeCount <= episodeCount) return []
    return [{
      key: `next_${episodeCount}_episodes` as 'next_3_episodes' | 'next_5_episodes',
      label: `创作接下来 ${episodeCount} 集`,
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

import type {
  ScreenplayV2ReviewFinding,
  ScreenplayV2ReviewFindingStatus,
  ScreenplayV2ReviewPhase,
} from '../types.ts'

export type ReviewBatchDecisionStatus = Exclude<
  ScreenplayV2ReviewFindingStatus,
  'pending'
>

export function pendingReviewFindingIds(
  findings: ReadonlyArray<Pick<ScreenplayV2ReviewFinding, 'id' | 'status'>>,
): string[] {
  return findings
    .filter((finding) => finding.status === 'pending')
    .map((finding) => finding.id)
}

export function batchDecisionLabel(
  status: ReviewBatchDecisionStatus,
  count: number,
): string {
  const action = {
    planned: '接受并准备修订',
    resolved: '标记为已解决',
    dismissed: '标记为不成立',
    riskAccepted: '标记为接受风险',
  }[status]
  return `将 ${count} 条意见${action}`
}

export function finalizationDisabledReason(input: {
  counts: { pending: number; planned: number }
  hardChecks: Array<{ code: string; message: string }>
  canFinalize: boolean
}): string | null {
  if (input.counts.pending > 0) {
    return `还有 ${input.counts.pending} 条意见待处理`
  }
  if (input.counts.planned > 0) {
    return `还有 ${input.counts.planned} 条意见等待修订`
  }
  if (input.hardChecks.length > 0) {
    return input.hardChecks[0].message
  }
  return input.canFinalize ? null : '当前剧本尚未满足定稿条件'
}

export type ReviewPrimaryAction = {
  kind: 'startReview' | 'processFindings' | 'startRevision' | 'finalize' | 'completed'
  label: string
}

function hasUnverifiedReviewInput(
  hardChecks: ReadonlyArray<{ code: string }> | undefined,
): boolean {
  return hardChecks?.some((check) => check.code === 'review_input_unverified') ?? false
}

export function reviewRequiresRerun(input: {
  failedEpisodes?: ReadonlyArray<{ episodeNumber: number }>
  hardChecks?: ReadonlyArray<{ code: string; message: string }>
}): boolean {
  return (input.failedEpisodes?.length ?? 0) > 0
    || hasUnverifiedReviewInput(input.hardChecks)
}

export function reviewPrimaryAction(input: {
  phase: ScreenplayV2ReviewPhase
  recommendation: 'ready' | 'revise' | 'major_rework' | null
  failedEpisodes?: ReadonlyArray<{ episodeNumber: number }>
  hardChecks?: ReadonlyArray<{ code: string; message: string }>
}): ReviewPrimaryAction {
  if ((input.failedEpisodes?.length ?? 0) > 0) {
    return { kind: 'startReview', label: '重新审阅失败集' }
  }
  if (hasUnverifiedReviewInput(input.hardChecks)) {
    return { kind: 'startReview', label: '重新审阅' }
  }
  const actions: Record<ScreenplayV2ReviewPhase, ReviewPrimaryAction> = {
    awaitingReview: { kind: 'startReview', label: '开始审阅' },
    adjudicating: { kind: 'processFindings', label: '处理审阅意见' },
    readyToRevise: { kind: 'startRevision', label: '开始修订' },
    readyToFinalize: { kind: 'finalize', label: '确认定稿' },
    completed: { kind: 'completed', label: '创作已完成' },
  }
  return actions[input.phase]
}

export type ReviewWorkspaceEntry = {
  label: string
  emphasis: 'primary' | 'secondary'
}

export function reviewWorkspaceEntry(input: {
  phase: ScreenplayV2ReviewPhase
  counts: { pending: number }
  failedEpisodes?: ReadonlyArray<{ episodeNumber: number }>
  hardChecks?: ReadonlyArray<{ code: string; message: string }>
}): ReviewWorkspaceEntry | null {
  if (reviewRequiresRerun(input)) return null
  if (input.phase === 'awaitingReview') return null
  if (input.phase === 'adjudicating') {
    return {
      label: `处理审阅意见（${input.counts.pending}）`,
      emphasis: 'primary',
    }
  }
  if (input.phase === 'readyToRevise') {
    return { label: '查看审阅决定', emphasis: 'secondary' }
  }
  if (input.phase === 'readyToFinalize') {
    return { label: '确认定稿', emphasis: 'primary' }
  }
  return { label: '查看定稿记录', emphasis: 'secondary' }
}

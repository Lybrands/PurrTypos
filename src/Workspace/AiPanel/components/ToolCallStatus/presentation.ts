import type { ToolCallLabelOutcome } from '../../hooks/chat.types.ts'

export type ToolRowPhase = 'done' | 'running' | 'pending'

export interface ToolCallRow {
  index: number
  label: string
  outcome: ToolCallLabelOutcome
  phase: ToolRowPhase
  text: string
  durationMs?: number
  startedAt?: number
}

export interface ToolCallRowsInput {
  labels: string[]
  labelOutcomes?: ToolCallLabelOutcome[]
  cachedFlags?: boolean[]
  completedToolCount: number
  itemDurationsMs?: Array<number | null>
  activeItemStartedAt?: number
}

function rowPhase(
  index: number,
  completedToolCount: number,
  labelCount: number,
): ToolRowPhase {
  if (index < completedToolCount) return 'done'
  if (index === completedToolCount && completedToolCount < labelCount) {
    return 'running'
  }
  return 'pending'
}

function rowText(
  label: string,
  outcome: ToolCallLabelOutcome,
  phase: ToolRowPhase,
): string {
  if (outcome === 'context_error') {
    return `失败：${label}— 信息有误（当前书籍章节目录中无对应章节或工具参数无效）`
  }
  if (phase === 'done') return `已完成 ${label}`
  if (phase === 'running') return `正在执行 ${label}`
  return `待执行 ${label}`
}

export function buildToolCallRows({
  labels,
  labelOutcomes,
  cachedFlags,
  completedToolCount,
  itemDurationsMs,
  activeItemStartedAt,
}: ToolCallRowsInput): ToolCallRow[] {
  const done = Math.min(Math.max(0, completedToolCount), labels.length)
  return labels.flatMap((label, index) => {
    if (cachedFlags?.[index]) return []
    const outcome = labelOutcomes?.[index] ?? 'ok'
    const phase = rowPhase(index, done, labels.length)
    return [{
      index,
      label,
      outcome,
      phase,
      text: rowText(label, outcome, phase),
      ...(itemDurationsMs?.[index] != null
        ? { durationMs: itemDurationsMs[index] as number }
        : {}),
      ...(phase === 'running' && activeItemStartedAt != null
        ? { startedAt: activeItemStartedAt }
        : {}),
    }]
  })
}

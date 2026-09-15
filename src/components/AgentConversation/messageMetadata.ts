import type { AiModelConfig } from '../../types'

export function parseAgentMessageTime(value?: string): Date | null {
  if (!value?.trim()) return null
  const raw = value.trim()
  const normalized = raw.replace(' ', 'T')
  const timestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(normalized)
    ? `${normalized}Z`
    : normalized
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? null : date
}

const DAY_MS = 24 * 60 * 60 * 1000

function localCalendarDay(date: Date): number {
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
}

export function formatAgentMessageTime(
  value?: string,
  now: Date = new Date(),
): string {
  const date = parseAgentMessageTime(value)
  if (!date) return ''
  const time = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
  const daysAgo = (localCalendarDay(now) - localCalendarDay(date)) / DAY_MS

  if (daysAgo === 0) return time
  if (daysAgo === 1) return `昨天 ${time}`
  if (daysAgo === 2) return `前天 ${time}`

  const dateLabel = date.getFullYear() === now.getFullYear()
    ? `${date.getMonth() + 1}月${date.getDate()}日`
    : `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
  return `${dateLabel} ${time}`
}

export function buildAgentModelLabels(
  models: Array<Pick<AiModelConfig, 'id' | 'name' | 'nickname'>>,
): Record<string, string> {
  return Object.fromEntries(models.flatMap((model) => {
    const label = model.nickname?.trim() || model.name
    return [[model.id, label], [model.name, label]]
  }))
}

export function resolveAgentModelLabel(
  model?: string,
  labels: Readonly<Record<string, string>> = {},
): string {
  const value = model?.trim() || ''
  return value ? labels[value] || value : ''
}

import type { AiModelConfig } from '../../types'

export function parseAgentMessageTime(value?: string): Date | null {
  if (!value?.trim()) return null
  const raw = value.trim()
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
    ? `${raw.replace(' ', 'T')}Z`
    : raw
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatAgentMessageTime(value?: string): string {
  const date = parseAgentMessageTime(value)
  if (!date) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
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

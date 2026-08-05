import React from 'react'
import AgentMessageCopyButton from './MessageCopyButton'
import AgentMessageEditButton from './MessageEditButton'
import './UserMessageMeta.scss'

export interface AgentUserMessageMetaProps {
  content?: string
  sentAt?: string
  onEdit?: () => void
}

function parseMessageTime(value?: string): Date | null {
  if (!value?.trim()) return null
  const raw = value.trim()
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)
    ? `${raw.replace(' ', 'T')}Z`
    : raw
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function sameLocalDay(left: Date, right: Date): boolean {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate()
}

export function formatAgentMessageTime(value?: string, now = new Date()): string {
  const date = parseMessageTime(value)
  if (!date) return ''
  const time = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
  if (sameLocalDay(date, now)) return time
  const day = new Intl.DateTimeFormat('zh-CN', {
    year: date.getFullYear() === now.getFullYear() ? undefined : 'numeric',
    month: 'numeric',
    day: 'numeric',
  }).format(date)
  return `${day} ${time}`
}

/** 用户消息悬浮后显示的统一元信息区域。 */
export default function AgentUserMessageMeta({
  content,
  sentAt,
  onEdit,
}: AgentUserMessageMetaProps) {
  const displayTime = formatAgentMessageTime(sentAt)
  const hasCopyContent = Boolean(content?.trim())
  if (!displayTime && !hasCopyContent && !onEdit) return null

  const date = parseMessageTime(sentAt)
  return (
    <div className="agent-user-message-meta">
      {displayTime ? (
        <time
          className="agent-user-message-meta__time"
          dateTime={date?.toISOString()}
          title={date?.toLocaleString('zh-CN', { hour12: false })}
        >
          {displayTime}
        </time>
      ) : null}
      {hasCopyContent ? <AgentMessageCopyButton content={content || ''} /> : null}
      {onEdit ? <AgentMessageEditButton onClick={onEdit} /> : null}
    </div>
  )
}

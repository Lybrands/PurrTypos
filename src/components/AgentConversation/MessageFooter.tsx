import React from 'react'
import {
  formatAgentMessageTime,
  parseAgentMessageTime,
  resolveAgentModelLabel,
} from './messageMetadata'
import './MessageFooter.scss'

export interface AgentMessageFooterProps {
  side: 'user' | 'assistant'
  sentAt?: string
  model?: string
  modelLabels?: Readonly<Record<string, string>>
  actions?: React.ReactNode
  actionsPersistent?: boolean
  metadataVisible?: boolean
}

export default function AgentMessageFooter({
  side,
  sentAt,
  model,
  modelLabels,
  actions,
  actionsPersistent = false,
  metadataVisible = true,
}: AgentMessageFooterProps) {
  const displayTime = metadataVisible ? formatAgentMessageTime(sentAt) : ''
  const displayModel = metadataVisible && side === 'assistant'
    ? resolveAgentModelLabel(model, modelLabels)
    : ''
  if (!displayTime && !displayModel && !actions) return null

  const date = parseAgentMessageTime(sentAt)
  const persistentRegion = displayModel ? (
    <div className="agent-message-footer__persistent">{displayModel}</div>
  ) : null
  const time = displayTime ? (
    <time
      className="agent-message-footer__time"
      dateTime={date?.toISOString()}
    >
      {displayTime}
    </time>
  ) : null
  const actionRegion = actions ? (
    <div className={`agent-message-footer__actions${actionsPersistent ? ' is-persistent' : ''}`}>
      {actions}
    </div>
  ) : null

  return (
    <div className={`agent-message-footer is-${side}`}>
      {persistentRegion}
      {actionRegion}
      {time}
    </div>
  )
}

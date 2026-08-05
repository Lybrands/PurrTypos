import React from 'react'
import AgentUserMessageMeta from './UserMessageMeta'
import './UserMessageBody.scss'

export interface AgentUserMessageBodyProps {
  content: string
  sentAt?: string
  onEdit?: () => void
}

/** 普通用户消息的共享气泡与悬浮操作区。 */
export default function AgentUserMessageBody({
  content,
  sentAt,
  onEdit,
}: AgentUserMessageBodyProps) {
  return (
    <>
      {content ? (
        <div className="bubble-content agent-user-message__content">{content}</div>
      ) : null}
      <AgentUserMessageMeta content={content} sentAt={sentAt} onEdit={onEdit} />
    </>
  )
}

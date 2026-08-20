import React from 'react'
import './UserMessageBody.scss'

export interface AgentUserMessageBodyProps {
  content: string
}

/** 普通用户消息的共享气泡。 */
export default function AgentUserMessageBody({
  content,
}: AgentUserMessageBodyProps) {
  return content ? (
    <div className="bubble-content agent-user-message__content">{content}</div>
  ) : null
}

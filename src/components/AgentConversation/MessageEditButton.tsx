import React from 'react'
import { EditIcon, PurrButton, PurrTooltip } from '@/purr-components'
import './MessageActionButton.scss'

export interface AgentMessageEditButtonProps {
  onClick: () => void
  label?: string
}

/** 对话历史消息统一使用的编辑入口。 */
export default function AgentMessageEditButton({
  onClick,
  label = '编辑提问',
}: AgentMessageEditButtonProps) {
  return (
    <PurrTooltip title={label}>
      <PurrButton
        type="text"
        size="small"
        icon={<EditIcon style={{ fontSize: 12 }} />}
        className="agent-message-action-button"
        aria-label={label}
        onClick={onClick}
      />
    </PurrTooltip>
  )
}

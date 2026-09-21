import React from 'react'
import { LinkIcon, PurrButton, PurrPopover } from '@/purr-components'
import './MessageActionButton.scss'
import './MessageQuoteButton.scss'

export interface AgentMessageQuoteButtonProps {
  quoteLines: ReadonlyArray<string>
}

/** 用户消息底部操作行的「查看引用」入口：点击弹出浮层展示引用内容。 */
export default function AgentMessageQuoteButton({
  quoteLines,
}: AgentMessageQuoteButtonProps) {
  const [open, setOpen] = React.useState(false)
  return (
    <PurrPopover
      trigger="click"
      placement="topRight"
      open={open}
      onOpenChange={setOpen}
      maxHeight={220}
      content={(
        <div className="agent-message-quote-popover">{quoteLines.join('\n')}</div>
      )}
    >
      <PurrButton
        type="text"
        size="small"
        icon={<LinkIcon style={{ fontSize: 12 }} />}
        className={'agent-message-action-button' + (open ? ' is-active' : '')}
        aria-label="查看引用"
      />
    </PurrPopover>
  )
}

import React from 'react'
import { CheckIcon, CopyIcon, PurrButton, PurrTooltip } from '@/purr-components'
import './MessageActionButton.scss'

export interface AgentMessageCopyButtonProps {
  content: string
  label?: string
}

/** 对话历史消息统一使用的复制入口。 */
export default function AgentMessageCopyButton({
  content,
  label = '复制消息',
}: AgentMessageCopyButtonProps) {
  const [copied, setCopied] = React.useState(false)
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  React.useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  const copyMessage = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 1400)
    } catch {
      setCopied(false)
    }
  }, [content])

  const feedbackLabel = copied ? '已复制' : label
  return (
    <PurrTooltip title={feedbackLabel}>
      <PurrButton
        type="text"
        size="small"
        icon={copied
          ? <CheckIcon style={{ fontSize: 12 }} />
          : <CopyIcon style={{ fontSize: 12 }} />}
        className={`agent-message-action-button${copied ? ' is-copied' : ''}`}
        aria-label={feedbackLabel}
        onClick={() => void copyMessage()}
      />
    </PurrTooltip>
  )
}

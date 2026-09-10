import React from 'react'
import {
  CheckIcon,
  CopyIcon,
  PurrButton,
  PurrDropdown,
  PurrTooltip,
} from '@/purr-components'
import './MessageActionButton.scss'
import { markdownToPlainText } from '../../utils/markdown'

export interface AgentMessageCopyButtonProps {
  content: string
  markdownContent?: string
  plainTextFromMarkdown?: boolean
  label?: string
}

/** 对话历史消息统一使用的复制入口。 */
export default function AgentMessageCopyButton({
  content,
  markdownContent,
  plainTextFromMarkdown = false,
  label = '复制消息',
}: AgentMessageCopyButtonProps) {
  const [copiedFormat, setCopiedFormat] = React.useState<'plain' | 'markdown' | null>(null)
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  React.useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  const copyMessage = React.useCallback(async (
    value: string,
    format: 'plain' | 'markdown',
  ) => {
    try {
      await navigator.clipboard.writeText(format === 'plain' && plainTextFromMarkdown ? markdownToPlainText(value) : value)
      setCopiedFormat(format)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopiedFormat(null), 1400)
    } catch {
      setCopiedFormat(null)
    }
  }, [plainTextFromMarkdown])

  const copied = copiedFormat != null
  const feedbackLabel = copiedFormat
    ? markdownContent
      ? `已复制${copiedFormat === 'plain' ? '纯文本' : ' Markdown'}`
      : '已复制'
    : markdownContent
      ? '复制纯文本 · 右键选择格式'
      : label
  const button = (
    <PurrTooltip title={feedbackLabel}>
      <PurrButton
        type="text"
        size="small"
        icon={copied
          ? <CheckIcon style={{ fontSize: 12 }} />
          : <CopyIcon style={{ fontSize: 12 }} />}
        className={`agent-message-action-button${copied ? ' is-copied' : ''}`}
        aria-label={label}
        onClick={() => void copyMessage(content, 'plain')}
      />
    </PurrTooltip>
  )
  if (!markdownContent) return button

  return (
    <PurrDropdown
      trigger={['contextMenu']}
      menu={{
        items: [
          {
            key: 'copy-plain',
            label: '复制纯文本',
            icon: <CopyIcon />,
            onClick: () => void copyMessage(content, 'plain'),
          },
          {
            key: 'copy-markdown',
            label: '复制 Markdown',
            icon: <CopyIcon />,
            onClick: () => void copyMessage(markdownContent, 'markdown'),
          },
        ],
      }}
    >
      {button}
    </PurrDropdown>
  )
}

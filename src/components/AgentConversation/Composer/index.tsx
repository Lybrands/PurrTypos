import React from 'react'
import { PurrInput } from '@/purr-components'
import './index.scss'

export interface AgentComposerProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  placeholder?: string
  ariaLabel?: string
  disabled?: boolean
  submitDisabled?: boolean
  autoSize?: { minRows?: number; maxRows?: number }
  supplementaryContent?: React.ReactNode
  floatingContent?: React.ReactNode
  footer: React.ReactNode
  className?: string
}

/**
 * Agent 对话的共享输入器。
 *
 * 组件只管理输入卡片、自动增高与键盘提交；模型、上下文、停止和发送等
 * 业务操作由 footer/floatingContent 插槽传入，以便不同 Agent 复用同一形态。
 */
export default function AgentComposer({
  value,
  onChange,
  onSubmit,
  placeholder = '想写点什么',
  ariaLabel = '输入对话内容',
  disabled = false,
  submitDisabled = false,
  autoSize = { minRows: 1, maxRows: 5 },
  supplementaryContent,
  floatingContent,
  footer,
  className,
}: AgentComposerProps) {
  return (
    <div
      className={[
        'agent-composer',
        floatingContent ? 'agent-composer--with-floating' : '',
        disabled ? 'is-disabled' : '',
        className,
      ].filter(Boolean).join(' ')}
    >
      {floatingContent ? (
        <div className="agent-composer__floating">
          {floatingContent}
        </div>
      ) : null}
      <PurrInput.TextArea
        className="agent-composer__input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        autoSize={autoSize}
        disabled={disabled}
        onKeyDown={(event) => {
          if (
            event.key !== 'Enter'
            || event.shiftKey
            || event.nativeEvent.isComposing
            || submitDisabled
          ) return
          event.preventDefault()
          onSubmit()
        }}
      />
      {supplementaryContent}
      <div className="agent-composer__footer">
        {footer}
      </div>
    </div>
  )
}

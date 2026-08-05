import React from 'react'
import {
  PurrButton,
  PurrInput,
  type PurrTextAreaRef,
} from '@/purr-components'
import './MessageEditor.scss'

export interface AgentMessageEditorProps {
  initialContent: string
  onSubmit: (content: string) => void
  onCancel: () => void
  onDraftChange?: (content: string) => void
  beforeEditor?: React.ReactNode
  renderFooter?: (actions: React.ReactNode) => React.ReactNode
  textareaRef?: React.RefObject<PurrTextAreaRef | null>
  submitLabel?: string
  placeholder?: string
}

/**
 * 历史用户消息的共享编辑器。业务页面可以插入上下文栏并包装底部操作区，
 * 但文本域、按钮、尺寸和键盘行为始终由这里统一管理。
 */
export default function AgentMessageEditor({
  initialContent,
  onSubmit,
  onCancel,
  onDraftChange,
  beforeEditor,
  renderFooter,
  textareaRef,
  submitLabel = '发送',
  placeholder = '编辑内容，发送将从此处重新对话…',
}: AgentMessageEditorProps) {
  const [draft, setDraft] = React.useState(initialContent)
  const trimmedDraft = draft.trim()

  const submit = React.useCallback(() => {
    if (!trimmedDraft) return
    onSubmit(trimmedDraft)
  }, [onSubmit, trimmedDraft])

  const actions = (
    <div className="agent-message-editor__actions">
      <PurrButton type="text" size="small" onClick={onCancel}>
        取消
      </PurrButton>
      <PurrButton
        type="primary"
        size="small"
        disabled={!trimmedDraft}
        onClick={submit}
      >
        {submitLabel}
      </PurrButton>
    </div>
  )

  return (
    <div className="agent-message-editor">
      {beforeEditor}
      <PurrInput.TextArea
        ref={textareaRef as React.RefObject<PurrTextAreaRef> | undefined}
        className="agent-message-editor__textarea"
        value={draft}
        placeholder={placeholder}
        autoSize={{ minRows: 2, maxRows: 8 }}
        autoFocus
        aria-label="编辑历史提问"
        onChange={(event) => {
          const nextDraft = event.target.value
          setDraft(nextDraft)
          onDraftChange?.(nextDraft)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.preventDefault()
            onCancel()
          } else if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            submit()
          }
        }}
      />
      {renderFooter ? renderFooter(actions) : actions}
    </div>
  )
}

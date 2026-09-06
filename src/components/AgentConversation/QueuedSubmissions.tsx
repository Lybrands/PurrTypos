import React from 'react'
import { PurrButton, PurrInput } from '@/purr-components'
import type { AgentQueuedSubmission } from '../../agent-runtime/contracts'
import type { AgentConversationController } from './controller'
import { isComposerSubmitDisabled } from './composerPolicy'

function QueueItem({ controller, item, index }: {
  controller: AgentConversationController; item: AgentQueuedSubmission; index: number
}) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState('')
  const release = React.useRef<(() => void) | undefined>(undefined)
  const update = controller.actions.updateQueuedSubmission
  const disabled = controller.capabilities.inputDisabled || controller.conversation.initializing
  React.useEffect(() => () => { release.current?.() }, [])
  const cancel = () => {
    release.current?.()
    release.current = undefined
    setEditing(false)
  }
  const save = () => {
    if (disabled || !draft.trim()) return
    if (update?.(item.id, { content: draft, editing: false })) {
      release.current = undefined
      setEditing(false)
    }
  }
  return <div className="agent-conversation-panel__queue-item" data-queue-id={item.id}>
    <span>待发送 {index + 1}</span>
    {editing ? <div className="agent-conversation-panel__queue-editor">
      <PurrInput.TextArea value={draft} aria-label={`编辑待发送消息 ${index + 1}`}
        autoSize={{ minRows: 2, maxRows: 6 }} disabled={disabled}
        onChange={event => setDraft(event.target.value)}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return
          if (event.key === 'Escape') { event.preventDefault(); cancel() }
          if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); save() }
        }} />
      <div className="agent-conversation-panel__queue-actions">
        <PurrButton size="small" disabled={disabled || !draft.trim()} onClick={save}>保存</PurrButton>
        <PurrButton size="small" onClick={cancel}>取消</PurrButton>
        <span role="status">编辑期间，本会话的队列暂停发送</span>
      </div>
    </div> : <span className="agent-conversation-panel__queue-content" title={item.content}>{item.content}</span>}
    {update ? <div className="agent-conversation-panel__queue-actions">
      {!editing ? <PurrButton type="text" size="small" disabled={disabled}
        aria-label={`编辑待发送消息 ${index + 1}`} onClick={() => {
          if (disabled || !update(item.id, { editing: true })) return
          release.current = () => { update(item.id, { editing: false }) }
          setDraft(item.content)
          setEditing(true)
        }}>编辑</PurrButton> : null}
      <PurrButton type="text" size="small" danger disabled={disabled}
        aria-label={`删除待发送消息 ${index + 1}`} onClick={() => {
          if (!disabled && update(item.id, null)) release.current = undefined
        }}>删除</PurrButton>
    </div> : null}
  </div>
}

export default function QueuedSubmissions({ controller }: { controller: AgentConversationController }) {
  const queued = controller.conversation.queuedSubmissions
  if (!queued.length) return null
  return <div className="agent-conversation-panel__queue" aria-label="待发送消息">
    {controller.conversation.queuePaused ? <div role="status">
      发送未成功，队列已暂停
      {controller.actions.retryQueued ? <PurrButton type="text" size="small"
        disabled={isComposerSubmitDisabled(controller, queued[0].content)}
        onClick={() => void controller.actions.retryQueued?.()}>重试发送</PurrButton> : null}
      {controller.actions.clearQueued ? <PurrButton type="text" size="small"
        onClick={() => void controller.actions.clearQueued?.()}>清空队列</PurrButton> : null}
    </div> : null}
    {queued.map((item, index) => <QueueItem key={`${controller.conversation.identity}:${item.id}`}
      controller={controller} item={item} index={index} />)}
  </div>
}

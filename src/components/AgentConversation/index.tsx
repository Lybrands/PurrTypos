import React from 'react'
import { LoadingIcon, RobotIcon } from '@/purr-components'
import type { ChatMessage } from '../../agent-runtime'
import AssistantMessageBody from '../../Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody'
import ErrorReportNotice from '../../Workspace/AiPanel/components/ChatMessageList/ErrorReportNotice'
import AgentConversationTurnIndex, {
  buildAgentConversationTurnIndex,
  type AgentConversationTurnIndexItem,
} from '../AgentConversationTurnIndex'
import AgentMessageEditor from './MessageEditor'
import AgentUserMessageBody from './UserMessageBody'
import { assistantMessageVisible } from './messageVisibility'
import {
  createScrollFollowState,
  detachScrollFollow,
  observeScrollBottom,
} from './scrollFollowPolicy'
import './index.scss'

export interface AgentConversationProps {
  messages: ChatMessage[]
  loading: boolean
  /** 历史消息及其持久化执行过程仍在恢复时，避免先绘制不完整内容。 */
  initializing?: boolean
  emptyTitle?: string
  emptyDescription?: string
  /** 将业务操作附着到产生它的助手消息，而不是整个会话末尾。 */
  afterAssistantMessage?: (message: ChatMessage, index: number) => React.ReactNode
  /** 消息附件状态变化时，触发仍在跟随输出的会话继续滚动。 */
  messageAttachmentsVersion?: string | number
  /** 编辑历史提问后，从该轮重新开始对话。 */
  onEditMessage?: (messageIndex: number, content: string) => void | Promise<void>
}

export default function AgentConversation({
  messages,
  loading,
  initializing = false,
  emptyTitle = '等待开始对话',
  emptyDescription = '发送任务后，这里会展示 Agent 的执行过程与结果。',
  afterAssistantMessage,
  messageAttachmentsVersion,
  onEditMessage,
}: AgentConversationProps) {
  const viewportRef = React.useRef<HTMLDivElement | null>(null)
  const scrollFollowStateRef = React.useRef(createScrollFollowState())
  const turnIndexItems = React.useMemo(
    () => buildAgentConversationTurnIndex(messages),
    [messages],
  )
  const [activeTurnIndex, setActiveTurnIndex] = React.useState(0)
  const [editingMessageIndex, setEditingMessageIndex] = React.useState<number | null>(null)

  const cancelMessageEdit = React.useCallback(() => {
    setEditingMessageIndex(null)
  }, [])

  const submitMessageEdit = React.useCallback((content: string) => {
    if (editingMessageIndex == null || !onEditMessage) return
    const messageIndex = editingMessageIndex
    cancelMessageEdit()
    void onEditMessage(messageIndex, content)
  }, [cancelMessageEdit, editingMessageIndex, onEditMessage])

  React.useEffect(() => {
    if (loading) cancelMessageEdit()
  }, [cancelMessageEdit, loading])

  const scrollToBottom = React.useCallback(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    viewport.scrollTop = viewport.scrollHeight
  }, [])

  const viewportIsAtBottom = React.useCallback(() => {
    const viewport = viewportRef.current
    if (!viewport) return true
    return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= 40
  }, [])

  const detachFromOutput = React.useCallback((isAtBottom?: boolean) => {
    scrollFollowStateRef.current = detachScrollFollow(
      scrollFollowStateRef.current,
      isAtBottom ?? viewportIsAtBottom(),
    )
  }, [viewportIsAtBottom])

  React.useLayoutEffect(() => {
    if (initializing) {
      scrollFollowStateRef.current = createScrollFollowState()
      setActiveTurnIndex(0)
      return
    }
    if (!scrollFollowStateRef.current.userDetached) {
      scrollToBottom()
      setActiveTurnIndex(Math.max(0, turnIndexItems.length - 1))
    }
  }, [initializing, loading, messageAttachmentsVersion, messages, scrollToBottom, turnIndexItems.length])

  React.useEffect(() => {
    setActiveTurnIndex((current) => Math.min(
      current,
      Math.max(0, turnIndexItems.length - 1),
    ))
  }, [turnIndexItems.length])

  const updateActiveTurn = React.useCallback((viewport: HTMLDivElement) => {
    const nodes = viewport.querySelectorAll<HTMLElement>('[data-agent-turn-index]')
    if (nodes.length === 0) return
    const threshold = viewport.getBoundingClientRect().top + 48
    let active = 0
    nodes.forEach((node, index) => {
      if (node.getBoundingClientRect().top <= threshold) active = index
    })
    setActiveTurnIndex(active)
  }, [])

  const selectTurn = React.useCallback((item: AgentConversationTurnIndexItem) => {
    const viewport = viewportRef.current
    const target = viewport?.querySelector<HTMLElement>(
      `[data-agent-turn-index="${item.dataIndex}"]`,
    )
    if (!viewport || !target) return
    detachFromOutput(false)
    setActiveTurnIndex(turnIndexItems.findIndex((turn) => turn.dataIndex === item.dataIndex))
    const viewportRect = viewport.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    viewport.scrollTo({
      top: viewport.scrollTop + targetRect.top - viewportRect.top - 12,
      behavior: 'smooth',
    })
  }, [detachFromOutput, turnIndexItems])

  return (
    <div className="agent-conversation-shell">
      <div
        ref={viewportRef}
        className="agent-conversation"
        onWheel={(event) => {
          if (event.deltaY < 0) detachFromOutput()
        }}
        onTouchMove={() => detachFromOutput()}
        onPointerDown={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect()
          if (event.clientX >= bounds.right - 16) detachFromOutput()
        }}
        onScroll={(event) => {
          const target = event.currentTarget
          const atBottom = (
            target.scrollHeight - target.scrollTop - target.clientHeight <= 40
          )
          scrollFollowStateRef.current = observeScrollBottom(
            scrollFollowStateRef.current,
            atBottom,
          )
          updateActiveTurn(target)
        }}
      >
      {initializing ? (
        <div className="agent-conversation__empty agent-conversation__initializing" role="status">
          <span><LoadingIcon spin /></span>
          <strong>正在恢复对话</strong>
        </div>
      ) : messages.length === 0 ? (
        <div className="agent-conversation__empty">
          <span><RobotIcon /></span>
          <strong>{emptyTitle}</strong>
          <p>{emptyDescription}</p>
        </div>
      ) : (
        <div className="agent-conversation__turns">
          {messages.map((message, index) => {
            const isLast = index === messages.length - 1
            if (message.role === 'user') {
              const editing = editingMessageIndex === index
              return (
                <article
                  className={`agent-conversation__message is-user${editing ? ' is-editing' : ''}`}
                  data-agent-turn-index={index}
                  key={`message-${index}`}
                >
                  {editing ? (
                    <AgentMessageEditor
                      key={`edit-${index}`}
                      initialContent={message.content || ''}
                      onSubmit={submitMessageEdit}
                      onCancel={cancelMessageEdit}
                    />
                  ) : (
                    <AgentUserMessageBody
                      content={message.content}
                      sentAt={message.sentAt}
                      onEdit={onEditMessage && !loading
                        ? () => setEditingMessageIndex(index)
                        : undefined}
                    />
                  )}
                </article>
              )
            }
            if (message.role !== 'assistant') return null
            const hasVisibleContent = Boolean(
              message.content
              || message.commentary
              || message.toolCallSegments?.length
              || message.delegations?.length
              || message.contextCompaction
            )
            const hasStatus = Boolean(
              message.isError || message.termination || message.error,
            )
            const attachment = afterAssistantMessage?.(message, index)
            if (!assistantMessageVisible({
              hasVisibleContent,
              hasAttachment: Boolean(attachment),
              hasStatus,
              isLast,
              loading,
            })) return null
            return (
              <article className="agent-conversation__message is-assistant" key={`message-${index}`}>
                {message.isError ? (
                  <ErrorReportNotice
                    message={message.error || '本轮执行失败'}
                    report={message.errorReport}
                  />
                ) : hasVisibleContent || hasStatus || (isLast && loading) ? (
                  <AssistantMessageBody
                    index={index}
                    message={message}
                    loading={loading}
                    isLastAssistant={isLast}
                    showPlaceholder={Boolean(
                      isLast
                      && loading
                      && !message.content
                      && !message.commentary
                      && !message.toolCallSegments?.length
                    )}
                    setScrolledUpByReason={(value) => {
                      if (value) detachFromOutput()
                    }}
                  />
                ) : null}
                {attachment ? (
                  <div className="agent-conversation__artifact">
                    {attachment}
                  </div>
                ) : null}
              </article>
            )
          })}
        </div>
      )}
      </div>
      <AgentConversationTurnIndex
        items={initializing ? [] : turnIndexItems}
        activeIndex={activeTurnIndex}
        onSelect={selectTurn}
      />
    </div>
  )
}

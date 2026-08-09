import React from 'react'
import { RobotIcon } from '@/purr-components'
import type { ChatMessage } from '../../agent-runtime'
import AssistantMessageBody from '../../Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody'
import ErrorReportNotice from '../../Workspace/AiPanel/components/ChatMessageList/ErrorReportNotice'
import AgentConversationTurnIndex, {
  buildAgentConversationTurnIndex,
  type AgentConversationTurnIndexItem,
} from '../AgentConversationTurnIndex'
import AgentMessageEditor from './MessageEditor'
import AgentUserMessageBody from './UserMessageBody'
import {
  createScrollFollowState,
  detachScrollFollow,
  observeScrollBottom,
} from './scrollFollowPolicy'
import './index.scss'

export interface AgentConversationProps {
  messages: ChatMessage[]
  loading: boolean
  emptyTitle?: string
  emptyDescription?: string
  afterMessages?: React.ReactNode
  /** 供业务产物以 Portal 形式附着到最后一轮回复之后。 */
  afterMessagesHostRef?: React.Ref<HTMLDivElement>
  /** Portal 产物挂载或状态变化时，触发对话跟随到底部。 */
  afterMessagesVersion?: string | number
  /** 编辑历史提问后，从该轮重新开始对话。 */
  onEditMessage?: (messageIndex: number, content: string) => void | Promise<void>
}

export default function AgentConversation({
  messages,
  loading,
  emptyTitle = '等待开始对话',
  emptyDescription = '发送任务后，这里会展示 Agent 的执行过程与结果。',
  afterMessages,
  afterMessagesHostRef,
  afterMessagesVersion,
  onEditMessage,
}: AgentConversationProps) {
  const viewportRef = React.useRef<HTMLDivElement | null>(null)
  const portalHostRef = React.useRef<HTMLDivElement | null>(null)
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

  React.useEffect(() => {
    if (!scrollFollowStateRef.current.userDetached) {
      scrollToBottom()
      setActiveTurnIndex(Math.max(0, turnIndexItems.length - 1))
    }
  }, [afterMessages, afterMessagesVersion, loading, messages, scrollToBottom, turnIndexItems.length])

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

  const setPortalHost = React.useCallback((node: HTMLDivElement | null) => {
    portalHostRef.current = node
    if (typeof afterMessagesHostRef === 'function') {
      afterMessagesHostRef(node)
    } else if (afterMessagesHostRef) {
      (afterMessagesHostRef as React.MutableRefObject<HTMLDivElement | null>).current = node
    }
  }, [afterMessagesHostRef])

  React.useEffect(() => {
    const host = portalHostRef.current
    if (!host) return undefined
    let frame = 0
    const followPortalOutput = () => {
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(() => {
        if (!scrollFollowStateRef.current.userDetached) scrollToBottom()
      })
    }
    const observer = new MutationObserver(followPortalOutput)
    observer.observe(host, {
      childList: true,
      subtree: true,
      characterData: true,
    })
    followPortalOutput()
    return () => {
      observer.disconnect()
      window.cancelAnimationFrame(frame)
    }
  }, [afterMessagesVersion, scrollToBottom])

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
      {messages.length === 0 && !afterMessages ? (
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
              message.isError
              || message.content
              || message.thinking
              || message.toolCallSegments?.length
              || message.delegations?.length
              || message.contextCompaction
              || message.termination
              || message.error,
            )
            if (!hasVisibleContent && !(isLast && loading)) return null
            return (
              <article className="agent-conversation__message is-assistant" key={`message-${index}`}>
                {message.isError ? (
                  <ErrorReportNotice
                    message={message.content || '本轮执行失败'}
                    report={message.errorReport}
                  />
                ) : hasVisibleContent || (isLast && loading) ? (
                  <AssistantMessageBody
                    index={index}
                    message={message}
                    loading={loading}
                    isLastAssistant={isLast}
                    showPlaceholder={Boolean(
                      isLast
                      && loading
                      && !message.content
                      && !message.thinking
                      && !message.toolCallSegments?.length
                    )}
                    setScrolledUpByReason={(value) => {
                      if (value) detachFromOutput()
                    }}
                  />
                ) : null}
              </article>
            )
          })}
          {afterMessages}
          {afterMessagesHostRef ? (
            <div
              ref={setPortalHost}
              className="agent-conversation__after-messages-host"
            />
          ) : null}
        </div>
      )}
      </div>
      <AgentConversationTurnIndex
        items={turnIndexItems}
        activeIndex={activeTurnIndex}
        onSelect={selectTurn}
      />
    </div>
  )
}

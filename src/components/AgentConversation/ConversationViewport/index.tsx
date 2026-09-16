import React from 'react'
import {
  AlignBottomIcon,
  LoadingIcon,
  PurrButton,
  PurrTooltip,
  RobotIcon,
} from '@/purr-components'
import {
  Virtuoso,
  type ListProps,
  type ScrollerProps,
  type VirtuosoHandle,
} from 'react-virtuoso'
import type { AgentConversationMessage } from '../../../agent-runtime'
import AssistantOutput from '../AssistantOutput'
import { hasRenderableErrorMessage } from '../AssistantOutput/errorNoticeMessage'
import { buildAssistantCopyView } from '../assistantCopy'
import AgentMessageCopyButton from '../MessageCopyButton'
import AgentMessageEditButton from '../MessageEditButton'
import AgentMessageEditor from '../MessageEditor'
import AgentMessageFooter from '../MessageFooter'
import AgentUserMessageBody from '../UserMessageBody'
import {
  assistantMessageMetadataVisible,
  assistantMessageVisible,
  latestAssistantMessageIndex,
} from '../messageVisibility'
import {
  advanceLiveTurnCursor,
  agentConversationMessageKey,
  createScrollFollowState,
  detachScrollFollow,
  observeScrollBottom,
  type LiveTurnCursor,
  type ScrollFollowState,
} from '../scrollFollowPolicy'
import AgentConversationTurnIndex, {
  buildAgentConversationTurnIndex,
  type AgentConversationTurnIndexItem,
} from '../TurnIndex'
import { bindScrollFollowIntent } from '../scrollFollowEvents'
import {
  cancelViewportFrame,
  createViewportEditTarget,
  resolveViewportEditTarget,
  type ViewportEditTarget,
} from '../viewportSession'
import './index.scss'
import type { SubAgentReader } from '../DelegationStatus'
import type { ToolLabelContext } from '../AssistantOutput/timeline'

export interface AgentConversationProps {
  sessionIdentity: string
  messages: AgentConversationMessage[]
  loading: boolean
  initializing?: boolean
  emptyTitle?: string
  emptyDescription?: string
  afterAssistantMessage?: (
    message: AgentConversationMessage,
    index: number,
  ) => React.ReactNode
  afterAssistantMessageActions?: (
    message: AgentConversationMessage,
    index: number,
  ) => React.ReactNode
  modelLabels?: Readonly<Record<string, string>>
  messageAttachmentsVersion?: string | number
  onEditMessage?: (messageIndex: number, content: string) => void | Promise<void>
  onStructuredAnswer?: (answer: string) => void
  onResolveToolApproval: (
    approvalId: string,
    approved: boolean,
  ) => Promise<{ success: boolean; error?: string }>
  onSubmitErrorReport?: (
    reportId: string,
  ) => Promise<{ success: boolean; error?: string }>
  subAgentReader?: SubAgentReader
  toolLabelContext?: ToolLabelContext
}

const VirtuosoList = React.forwardRef<HTMLDivElement, ListProps>(
  ({ style, children, ...rest }, ref) => (
    <div
      ref={ref}
      style={style}
      className="agent-conversation__turns"
      {...rest}
    >
      {children}
    </div>
  ),
)
VirtuosoList.displayName = 'AgentConversationVirtuosoList'

const VirtuosoScroller = React.forwardRef<HTMLDivElement, ScrollerProps>(
  ({ style, children, tabIndex, ...rest }, ref) => (
    <div
      ref={ref}
      style={style}
      className="agent-conversation"
      tabIndex={tabIndex ?? 0}
      aria-label="对话消息"
      {...rest}
    >
      {children}
    </div>
  ),
)
VirtuosoScroller.displayName = 'AgentConversationVirtuosoScroller'

const VIRTUOSO_COMPONENTS = {
  List: VirtuosoList,
  Scroller: VirtuosoScroller,
}

const useClientLayoutEffect = typeof window === 'undefined'
  ? React.useEffect
  : React.useLayoutEffect

function findTurnAtDataIndex(
  turns: AgentConversationTurnIndexItem[],
  dataIndex: number,
): number {
  let activeIndex = 0
  for (let index = 0; index < turns.length; index += 1) {
    if (turns[index].dataIndex > dataIndex) break
    activeIndex = index
  }
  return activeIndex
}

function hasVisibleAssistantContent(message: AgentConversationMessage): boolean {
  return Boolean(
    message.content
    || message.streamingContent
    || message.commentary
    || message.toolCallSegments?.length
    || message.delegations?.length
    || message.contextCompaction
    || message.canonicalOutput?.finalText
    || message.canonicalOutput?.commentaryBlocks.length
    || message.canonicalOutput?.operationOrder.length
    || message.toolApprovals?.length
    || (message.durationMs != null && message.durationMs > 0),
  )
}

export default function ConversationViewport({
  sessionIdentity,
  messages,
  loading,
  initializing = false,
  emptyTitle = '等待开始对话',
  emptyDescription = '发送任务后，这里会展示 Agent 的执行过程与结果。',
  afterAssistantMessage,
  afterAssistantMessageActions,
  modelLabels,
  messageAttachmentsVersion,
  onEditMessage,
  onStructuredAnswer,
  onResolveToolApproval,
  onSubmitErrorReport,
  subAgentReader,
  toolLabelContext,
}: AgentConversationProps) {
  const virtuosoRef = React.useRef<VirtuosoHandle>(null)
  const scrollerCleanupRef = React.useRef<(() => void) | null>(null)
  const scrollFollowStateRef = React.useRef<ScrollFollowState>(createScrollFollowState())
  const liveTurnCursorRef = React.useRef<LiveTurnCursor>()
  const liveTurnPinFrameRef = React.useRef<number | null>(null)
  const liveTurnPinPendingRef = React.useRef(false)
  const isAtBottomRef = React.useRef(true)
  const [userDetached, setUserDetached] = React.useState(false)
  const [isAtBottom, setIsAtBottom] = React.useState(true)
  const [activeTurnIndex, setActiveTurnIndex] = React.useState(0)
  const [editingTarget, setEditingTarget] = React.useState<ViewportEditTarget | null>(null)
  const turnIndexItems = React.useMemo(
    () => buildAgentConversationTurnIndex(messages),
    [messages],
  )
  const latestAssistantIndex = React.useMemo(
    () => latestAssistantMessageIndex(messages),
    [messages],
  )

  const applyScrollFollowState = React.useCallback((state: ScrollFollowState) => {
    scrollFollowStateRef.current = state
    setUserDetached(state.userDetached)
  }, [])

  const detachFromOutput = React.useCallback((atBottom = isAtBottomRef.current) => {
    applyScrollFollowState(detachScrollFollow(scrollFollowStateRef.current, atBottom))
  }, [applyScrollFollowState])

  const handleAtBottomStateChange = React.useCallback((atBottom: boolean) => {
    isAtBottomRef.current = atBottom
    setIsAtBottom(atBottom)
    applyScrollFollowState(observeScrollBottom(scrollFollowStateRef.current, atBottom))
  }, [applyScrollFollowState])

  const scrollToBottom = React.useCallback(() => {
    const lastIndex = messages.length - 1
    if (lastIndex >= 0) {
      virtuosoRef.current?.scrollToIndex({
        index: lastIndex,
        align: 'end',
        behavior: 'auto',
      })
    }
    isAtBottomRef.current = true
    setIsAtBottom(true)
    applyScrollFollowState(createScrollFollowState())
  }, [applyScrollFollowState, messages.length])

  const handleScrollerRef = React.useCallback((ref: HTMLElement | Window | null) => {
    scrollerCleanupRef.current?.()
    scrollerCleanupRef.current = null
    if (!ref || ref instanceof Window) return

    scrollerCleanupRef.current = bindScrollFollowIntent(ref, detachFromOutput)
  }, [detachFromOutput])

  React.useEffect(() => () => {
    scrollerCleanupRef.current?.()
    liveTurnPinFrameRef.current = cancelViewportFrame(
      liveTurnPinFrameRef.current,
      cancelAnimationFrame,
    )
  }, [])

  React.useEffect(() => {
    if (loading) setEditingTarget(null)
  }, [loading])

  useClientLayoutEffect(() => {
    setEditingTarget(null)
    liveTurnCursorRef.current = undefined
    liveTurnPinPendingRef.current = false
    liveTurnPinFrameRef.current = cancelViewportFrame(
      liveTurnPinFrameRef.current,
      cancelAnimationFrame,
    )
    setActiveTurnIndex(0)
    isAtBottomRef.current = true
    setIsAtBottom(true)
    applyScrollFollowState(createScrollFollowState())
  }, [applyScrollFollowState, sessionIdentity])

  React.useEffect(() => {
    if (!initializing) return
    liveTurnCursorRef.current = undefined
    setActiveTurnIndex(0)
    isAtBottomRef.current = true
    setIsAtBottom(true)
    applyScrollFollowState(createScrollFollowState())
  }, [applyScrollFollowState, initializing])

  React.useEffect(() => {
    if (initializing) {
      if (liveTurnPinFrameRef.current != null) {
        cancelAnimationFrame(liveTurnPinFrameRef.current)
        liveTurnPinFrameRef.current = null
      }
      liveTurnPinPendingRef.current = false
      liveTurnCursorRef.current = undefined
      return
    }
    const observation = advanceLiveTurnCursor(liveTurnCursorRef.current, messages)
    liveTurnCursorRef.current = observation.cursor
    if (observation.anchorIndex == null) return

    isAtBottomRef.current = true
    setIsAtBottom(true)
    applyScrollFollowState(createScrollFollowState())
    liveTurnPinPendingRef.current = true
    if (liveTurnPinFrameRef.current != null) {
      cancelAnimationFrame(liveTurnPinFrameRef.current)
    }
    const anchorIndex = observation.anchorIndex
    liveTurnPinFrameRef.current = requestAnimationFrame(() => {
      virtuosoRef.current?.scrollToIndex({
        index: anchorIndex,
        align: 'start',
        behavior: 'auto',
      })
      liveTurnPinFrameRef.current = null
      liveTurnPinPendingRef.current = false
    })
  }, [applyScrollFollowState, initializing, messages])

  React.useEffect(() => {
    setActiveTurnIndex((current) => (
      isAtBottomRef.current
        ? Math.max(0, turnIndexItems.length - 1)
        : Math.min(current, Math.max(0, turnIndexItems.length - 1))
    ))
  }, [turnIndexItems.length])

  React.useEffect(() => {
    if (initializing || userDetached || messages.length === 0) return
    if (liveTurnPinPendingRef.current) return
    const frame = requestAnimationFrame(() => {
      if (scrollFollowStateRef.current.userDetached) return
      virtuosoRef.current?.scrollToIndex({
        index: messages.length - 1,
        align: 'end',
        behavior: 'auto',
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [initializing, messageAttachmentsVersion, messages, sessionIdentity, userDetached])

  const handleVisibleRangeChange = React.useCallback((
    { startIndex }: { startIndex: number; endIndex: number },
  ) => {
    if (turnIndexItems.length > 0) {
      setActiveTurnIndex(findTurnAtDataIndex(turnIndexItems, startIndex))
    }
  }, [turnIndexItems])

  const handleSelectTurn = React.useCallback((item: AgentConversationTurnIndexItem) => {
    detachFromOutput(false)
    setActiveTurnIndex(findTurnAtDataIndex(turnIndexItems, item.dataIndex))
    virtuosoRef.current?.scrollToIndex({
      index: item.dataIndex,
      align: 'start',
      behavior: 'smooth',
    })
  }, [detachFromOutput, turnIndexItems])

  const cancelMessageEdit = React.useCallback(() => setEditingTarget(null), [])
  const submitMessageEdit = React.useCallback((content: string) => {
    if (!onEditMessage) return
    const index = resolveViewportEditTarget(editingTarget, sessionIdentity, messages)
    if (index == null) return
    setEditingTarget(null)
    void onEditMessage(index, content)
  }, [editingTarget, messages, onEditMessage, sessionIdentity])

  const handleOutputDetach = React.useCallback((value: boolean) => {
    if (value) detachFromOutput()
  }, [detachFromOutput])

  const renderMessage = React.useCallback((index: number, message: AgentConversationMessage) => {
    const isLast = index === messages.length - 1
    if (message.role === 'user') {
      const messageKey = agentConversationMessageKey(index, message)
      const editing = editingTarget?.sessionIdentity === sessionIdentity
        && editingTarget.messageKey === messageKey
      const canEdit = Boolean(onEditMessage && !loading)
      const canCopy = Boolean(message.content.trim())
      const startEditing = canEdit
        ? () => setEditingTarget(createViewportEditTarget(sessionIdentity, index, message))
        : undefined
      return (
        <article
          className={`agent-conversation__message is-user${editing ? ' is-editing' : ''}`}
          data-agent-turn-index={index}
          tabIndex={!editing && (canCopy || canEdit) ? 0 : undefined}
        >
          {editing ? (
            <AgentMessageEditor
              initialContent={message.content || ''}
              onSubmit={submitMessageEdit}
              onCancel={cancelMessageEdit}
            />
          ) : (
            <>
              <AgentUserMessageBody content={message.content} />
              <AgentMessageFooter
                side="user"
                sentAt={message.sentAt}
                actions={canCopy || startEditing ? (
                  <>
                    {canCopy ? <AgentMessageCopyButton content={message.content} /> : null}
                    {startEditing ? <AgentMessageEditButton onClick={startEditing} /> : null}
                  </>
                ) : null}
              />
            </>
          )}
        </article>
      )
    }
    if (message.role !== 'assistant') return null

    const hasVisibleContent = hasVisibleAssistantContent(message)
    const hasStatus = Boolean(hasRenderableErrorMessage(message) || message.termination)
    const attachment = afterAssistantMessage?.(message, index)
    const extraActions = afterAssistantMessageActions?.(message, index)
    const isLastAssistant = isLast && !message.isError
    const showPlaceholder = Boolean(
      isLastAssistant && loading && !hasVisibleContent && !hasStatus,
    )
    const copyView = buildAssistantCopyView({
      message,
      isLastAssistant,
      loading,
      showPlaceholder,
      deferPlainText: true,
    })
    if (!assistantMessageVisible({
      hasVisibleContent,
      hasAttachment: Boolean(attachment),
      hasStatus,
      isLast,
      loading,
    })) return null

    return (
      <article
        className="agent-conversation__message is-assistant"
        tabIndex={extraActions || copyView.visible ? 0 : undefined}
      >
        {hasVisibleContent || hasStatus || (isLast && loading) ? (
          <AssistantOutput
            index={index}
            message={message}
            loading={loading}
            isLastAssistant={isLastAssistant}
            showPlaceholder={showPlaceholder}
            setScrolledUpByReason={handleOutputDetach}
            onStructuredAnswer={onStructuredAnswer}
            onResolveToolApproval={onResolveToolApproval}
            onSubmitErrorReport={onSubmitErrorReport}
            subAgentReader={subAgentReader}
            toolLabelContext={toolLabelContext}
          />
        ) : null}
        {attachment ? (
          <div className="agent-conversation__artifact">{attachment}</div>
        ) : null}
        <AgentMessageFooter
          side="assistant"
          sentAt={message.sentAt}
          model={message.model}
          modelLabels={modelLabels}
          metadataVisible={assistantMessageMetadataVisible({ isLast, loading })}
          actionsPersistent={index === latestAssistantIndex}
          actions={extraActions || copyView.visible ? (
            <>
              {extraActions}
              {copyView.visible ? (
                <AgentMessageCopyButton
                  plainTextFromMarkdown
                  content={copyView.plainText}
                  markdownContent={copyView.markdown}
                  label="复制回复纯文本"
                />
              ) : null}
            </>
          ) : null}
        />
      </article>
    )
  }, [
    afterAssistantMessage,
    afterAssistantMessageActions,
    cancelMessageEdit,
    handleOutputDetach,
    editingTarget,
    latestAssistantIndex,
    loading,
    messages.length,
    modelLabels,
    onEditMessage,
    onResolveToolApproval,
    onStructuredAnswer,
    onSubmitErrorReport,
    submitMessageEdit,
    sessionIdentity,
  ])

  return (
    <div className="agent-conversation-shell">
      {initializing ? (
        <div className="agent-conversation agent-conversation__empty agent-conversation__initializing" role="status">
          <span><LoadingIcon spin /></span>
          <strong>正在恢复对话</strong>
        </div>
      ) : messages.length === 0 ? (
        <div className="agent-conversation agent-conversation__empty">
          <span><RobotIcon /></span>
          <strong>{emptyTitle}</strong>
          <p>{emptyDescription}</p>
        </div>
      ) : (
        <Virtuoso
          ref={virtuosoRef}
          data={messages}
          initialTopMostItemIndex={{ index: messages.length - 1, align: 'end' }}
          increaseViewportBy={{ top: 400, bottom: 400 }}
          alignToBottom={!userDetached}
          followOutput={userDetached ? false : 'auto'}
          scrollerRef={handleScrollerRef}
          rangeChanged={handleVisibleRangeChange}
          atBottomThreshold={40}
          atBottomStateChange={handleAtBottomStateChange}
          computeItemKey={agentConversationMessageKey}
          components={VIRTUOSO_COMPONENTS}
          itemContent={renderMessage}
        />
      )}
      <AgentConversationTurnIndex
        items={initializing ? [] : turnIndexItems}
        activeIndex={activeTurnIndex}
        onSelect={handleSelectTurn}
      />
      {userDetached && !isAtBottom ? (
        <PurrTooltip title="回到底部">
          <PurrButton
            type="primary"
            size="small"
            icon={<AlignBottomIcon />}
            className="agent-conversation__scroll-to-bottom"
            onClick={scrollToBottom}
            aria-label="回到底部"
          />
        </PurrTooltip>
      ) : null}
    </div>
  )
}

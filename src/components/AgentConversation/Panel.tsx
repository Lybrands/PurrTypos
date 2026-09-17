import React from 'react'
import {
  ArrowUpIcon,
  PanelToggleIcon,
  PurrButton,
  PurrSelect,
  PurrTooltip,
  RefreshIcon,
  StopCircleIcon,
} from '@/purr-components'
import Composer from './Composer'
import ContextUsageIndicator from './Composer/ContextUsageIndicator'
import ModelPicker from './Composer/ModelPicker'
import ConversationIndex from './ConversationIndex'
import SessionHistory from './ConversationIndex/SessionHistory'
import ConversationViewport from './ConversationViewport'
import type { ToolLabelContext } from './AssistantOutput/timeline'
import TaskProgress from './TaskProgress'
import { SubAgentOverview, type SubAgentReader } from './DelegationStatus'
import { collapseSubAgentDelegations } from './DelegationStatus/presentation'
import type { AgentSessionId } from '../../agent-runtime'
import type { AgentConversationController } from './controller'
import type { AgentConversationExtensions } from './extensions'
import { buildAgentModelLabels } from './messageMetadata'
import { buildAgentConversationPanelView } from './panelView'
import { isComposerSubmitDisabled, isEmptyConversationPresentation } from './composerPolicy'
import QueuedSubmissions from './QueuedSubmissions'
import { prepareAgentCompletionNotifications } from '../../platform/agentNotifications'
import { useAgentCompletionNotification } from './useAgentCompletionNotification'
import './Panel.scss'
import {
  AGENT_OPERATION_MODE_OPTIONS,
  getAgentOperationMode,
  setAgentOperationMode,
  subscribeAgentOperationMode,
  type AgentOperationMode,
} from '../../agentOperationMode'

export interface AgentConversationPanelProps {
  controller: AgentConversationController
  extensions?: AgentConversationExtensions
  indexOpen?: boolean
  onIndexOpenChange?(open: boolean): void
  className?: string
  subAgentReader?: SubAgentReader
  /** 解析工具行文案所需的页面内目录（章节/大纲标题等） */
  toolLabelContext?: ToolLabelContext
  /** 零会话空态文案覆盖（例如写作范围未选章节时改为引导选章节） */
  emptyStateTitle?: string
  emptyStateDescription?: string
}

function ComposerFooter({
  controller,
  extensions,
  queueLabel,
  submitLabel,
  resumeDisabled,
}: {
  controller: AgentConversationController
  extensions?: AgentConversationExtensions
  queueLabel: string
  submitLabel: string
  resumeDisabled: boolean
}) {
  const { capabilities, composer, conversation, actions } = controller
  // A paused workflow has its own resume and domain-level cancellation
  // controls. Keeping the live-generation stop icon here suggests that a
  // Provider call is still in flight when no call can be stopped.
  const showStop = conversation.running || conversation.stopping
  const submitDisabled = isComposerSubmitDisabled(controller)
  const [operationMode, setOperationModeState] = React.useState(getAgentOperationMode)
  React.useEffect(() => subscribeAgentOperationMode(setOperationModeState), [])

  return (
    <div className="agent-conversation-panel__footer">
      <div className="agent-conversation-panel__footer-leading">
        {extensions?.renderComposerLeading?.()}
        {composer.modelConfigs.length > 0 ? (
          <ModelPicker
            modelConfigs={composer.modelConfigs}
            selectedModelId={composer.selectedModel?.id ?? ''}
            onModelChange={composer.selectModel}
            onUpdateModelConfig={composer.updateModel}
            disabled={capabilities.inputDisabled}
          />
        ) : (
          <PurrButton
            type="text"
            size="small"
            disabled={capabilities.inputDisabled}
            onClick={composer.openModelSettings}
          >
            配置模型
          </PurrButton>
        )}
        <PurrSelect<AgentOperationMode>
          className="agent-conversation-panel__operation-mode"
          variant="borderless"
          size="small"
          aria-label="操作类型"
          value={operationMode}
          options={AGENT_OPERATION_MODE_OPTIONS}
          disabled={capabilities.inputDisabled}
          onChange={(value) => {
            const next = value as AgentOperationMode
            setOperationModeState(next)
            setAgentOperationMode(next)
          }}
        />
      </div>
      <div className="agent-conversation-panel__footer-actions">
        {queueLabel ? <span role="status">{queueLabel}</span> : null}
        <ContextUsageIndicator
          conversations={conversation.messages}
          selectedModelConfig={composer.selectedModel}
        />
        {conversation.paused && actions.resume ? (
          <PurrButton
            type="default"
            size="small"
            icon={<RefreshIcon size={15} />}
            loading={conversation.resuming}
            disabled={resumeDisabled}
            onClick={() => {
              prepareAgentCompletionNotifications()
              void actions.resume?.()
            }}
          >
            {conversation.resumeLabel || '继续执行'}
          </PurrButton>
        ) : null}
        {showStop ? (
          <PurrTooltip title={conversation.stopping ? '正在停止' : '停止生成'}>
            <PurrButton
              type="text"
              shape="circle"
              className="agent-composer__stop"
              icon={<StopCircleIcon size={18} />}
              onClick={() => void actions.abort()}
              disabled={conversation.initializing || conversation.activeSessionId == null
                || conversation.abortDisabled || conversation.stopping}
              aria-label="停止生成"
            />
          </PurrTooltip>
        ) : null}
        <PurrTooltip title={
          submitDisabled && composer.value.trim() && composer.disabledHint
            ? `${composer.disabledHint}后即可发送`
            : `${submitLabel} (Enter)`
        }>
          <PurrButton
            type="primary"
            shape="circle"
            className="agent-composer__send"
            icon={<ArrowUpIcon style={{ fontSize: 16 }} />}
            disabled={submitDisabled}
            onClick={() => {
              if (isComposerSubmitDisabled(controller)) return
              prepareAgentCompletionNotifications()
              void actions.send()
            }}
            aria-label={submitLabel}
          />
        </PurrTooltip>
      </div>
    </div>
  )
}

export default function AgentConversationPanel({
  controller,
  extensions,
  indexOpen,
  onIndexOpenChange,
  className,
  subAgentReader,
  toolLabelContext,
  emptyStateTitle,
  emptyStateDescription,
}: AgentConversationPanelProps) {
  useAgentCompletionNotification(controller)
  const [uncontrolledIndexOpen, setUncontrolledIndexOpen] = React.useState(true)
  const [editingSessionId, setEditingSessionId] = React.useState<AgentSessionId | null>(null)
  const [editingSessionTitle, setEditingSessionTitle] = React.useState('')
  const resolvedIndexOpen = indexOpen ?? uncontrolledIndexOpen
  const view = buildAgentConversationPanelView({
    running: controller.conversation.running,
    queuedCount: controller.conversation.queuedSubmissions.length,
    taskPlan: controller.composer.taskPlan,
    submitMode: controller.capabilities.submitMode,
    selectedModel: controller.composer.selectedModel,
    inputDisabled: controller.capabilities.inputDisabled,
    resuming: controller.conversation.resuming,
    stopping: controller.conversation.stopping,
  })
  const modelLabels = React.useMemo(() => buildAgentModelLabels([
    ...controller.composer.modelConfigs,
    ...(controller.composer.selectedModel ? [controller.composer.selectedModel] : []),
  ]), [controller.composer.modelConfigs, controller.composer.selectedModel])
  const currentAgentMessage = React.useMemo(() => (
    [...controller.conversation.messages]
      .reverse()
      .find((message) => message.role === 'assistant' && message.delegations?.length)
  ), [controller.conversation.messages])
  const currentSubAgents = React.useMemo(
    () => collapseSubAgentDelegations(currentAgentMessage?.delegations ?? []),
    [currentAgentMessage?.delegations],
  )
  // 子 Agent 胶囊只在运行中出现在输入框上方；对话结束后收进「已完成」
  // 标题行右侧的入口（见 AssistantOutput 的 ExecutionLog headerExtra）。
  const showSubAgentOverview = controller.conversation.running && currentSubAgents.length > 0

  const setIndexOpen = React.useCallback((open: boolean) => {
    if (indexOpen == null) setUncontrolledIndexOpen(open)
    onIndexOpenChange?.(open)
  }, [indexOpen, onIndexOpenChange])

  const saveSessionTitle = React.useCallback(() => {
    if (editingSessionId == null) return
    const title = editingSessionTitle.trim()
    setEditingSessionId(null)
    if (title) void controller.actions.renameSession(editingSessionId, title)
  }, [controller.actions, editingSessionId, editingSessionTitle])

  const history = controller.conversation.history
    && controller.actions.loadSessionHistory
    ? (
      <SessionHistory
        controller={controller}
      />
    )
    : null

  // 空态呈现：仅当本范围没有任何会话时隐藏列表展示引导；只要存在会话
  // （哪怕是一个尚无消息的空会话）都正常呈现对话列表。
  const emptyPresentation = isEmptyConversationPresentation(controller.conversation)
  const sessionsEmpty = emptyPresentation

  return (
    <div className={[
      'agent-conversation-panel',
      resolvedIndexOpen && !sessionsEmpty ? 'is-index-open' : 'is-index-closed',
      className,
    ].filter(Boolean).join(' ')}>
      {resolvedIndexOpen && !sessionsEmpty ? (
        <ConversationIndex
          sessions={controller.conversation.sessions}
          activeSessionId={controller.conversation.activeSessionId}
          sessionActivities={controller.conversation.activities}
          editingSessionId={editingSessionId}
          editingTitle={editingSessionTitle}
          isCurrentSessionEmpty={!controller.conversation.initializing && controller.conversation.messages.length === 0}
          context={extensions?.renderSessionContext?.()}
          extraActions={history}
          onActiveSessionChange={(id) => void controller.actions.selectSession(id)}
          onEditingSessionIdChange={setEditingSessionId}
          onEditingTitleChange={setEditingSessionTitle}
          onSaveTitle={saveSessionTitle}
          onNewSession={() => void controller.actions.createSession()}
          onCloseSession={(session) => void controller.actions.closeSession(session.id)}
          onReorderSessions={controller.actions.reorderSessions
            ? (orderedIds) => void controller.actions.reorderSessions?.(orderedIds)
            : undefined}
          onToggleSessionPinned={controller.actions.toggleSessionPinned
            ? (id, pinned) => void controller.actions.toggleSessionPinned?.(id, pinned)
            : undefined}
          onCollapse={() => setIndexOpen(false)}
        />
      ) : sessionsEmpty ? null : (
        <PurrTooltip title="展开对话列表" placement="right">
          <PurrButton
            type="text"
            className="agent-conversation-panel__index-reopen"
            icon={<PanelToggleIcon side="left" state="collapsed" />}
            onClick={() => setIndexOpen(true)}
            aria-label="展开对话列表"
          />
        </PurrTooltip>
      )}
      <main className="agent-conversation-panel__main">
        <ConversationViewport
          emptyTitle={sessionsEmpty ? (emptyStateTitle ?? '开启你的第一段对话') : undefined}
          emptyDescription={sessionsEmpty
            ? (emptyStateDescription ?? '在下方输入并发送，发送后展开对话列表开始对话')
            : undefined}
          sessionIdentity={controller.conversation.identity}
          messages={controller.conversation.messages}
          loading={controller.conversation.running}
          initializing={controller.conversation.initializing}
          messageAttachmentsVersion={controller.conversation.attachmentsVersion}
          onEditMessage={controller.actions.editMessage}
          onStructuredAnswer={(answer) => {
            if (!isComposerSubmitDisabled(controller, answer)) void controller.actions.send(answer)
          }}
          onResolveToolApproval={controller.actions.resolveToolApproval}
          onSubmitErrorReport={controller.actions.onSubmitErrorReport}
          subAgentReader={subAgentReader}
          toolLabelContext={toolLabelContext}
          afterAssistantMessage={extensions?.renderAssistantAttachment}
          afterAssistantMessageActions={extensions?.renderAssistantActions}
          modelLabels={modelLabels}
        />
        <Composer
          value={controller.composer.value}
          onChange={controller.composer.setValue}
          onSubmit={() => {
            if (isComposerSubmitDisabled(controller)) return
            prepareAgentCompletionNotifications()
            void controller.actions.send()
          }}
          placeholder={controller.composer.placeholder}
          ariaLabel={controller.composer.ariaLabel}
          disabled={controller.capabilities.inputDisabled}
          submitDisabled={isComposerSubmitDisabled(controller)}
          floatingContent={(
            (view.showTaskProgress && controller.composer.taskPlan)
            || showSubAgentOverview
          ) ? (
            <div className="agent-composer__floating-content">
              {view.showTaskProgress && controller.composer.taskPlan ? (
                <TaskProgress plan={controller.composer.taskPlan} placement="topLeft" />
              ) : null}
              {showSubAgentOverview ? (
                <SubAgentOverview
                  items={currentSubAgents}
                  activities={currentAgentMessage?.subAgentActivities}
                  placement="topLeft"
                  reader={subAgentReader}
                />
              ) : null}
            </div>
          ) : null}
          supplementaryContent={<QueuedSubmissions controller={controller} />}
          commands={extensions?.composerCommands}
          actionMenu={extensions?.composerActionMenu}
          footer={(
            <ComposerFooter
              controller={controller}
              extensions={extensions}
              queueLabel={view.queueLabel}
              submitLabel={view.submitLabel}
              resumeDisabled={view.resumeDisabled}
            />
          )}
        />
      </main>
    </div>
  )
}

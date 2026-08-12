import React from 'react'
import {
  ArrowUpIcon,
  PanelToggleIcon,
  PurrButton,
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
import TaskProgress from './TaskProgress'
import type { AgentSessionId } from '../../agent-runtime'
import type { AgentConversationController } from './controller'
import type { AgentConversationExtensions } from './extensions'
import { buildAgentConversationPanelView } from './panelView'
import './Panel.scss'

export interface AgentConversationPanelProps {
  controller: AgentConversationController
  extensions?: AgentConversationExtensions
  indexOpen?: boolean
  onIndexOpenChange?(open: boolean): void
  className?: string
}

function QueuedSubmissions({
  controller,
}: {
  controller: AgentConversationController
}) {
  const queued = controller.conversation.queuedSubmissions
  if (queued.length === 0) return null
  return (
    <div className="agent-conversation-panel__queue" aria-label="待发送消息">
      {queued.slice(0, 3).map((submission, index) => (
        <div className="agent-conversation-panel__queue-item" key={submission.id}>
          <span>待发送 {index + 1}</span>
          <span title={submission.content}>{submission.content}</span>
        </div>
      ))}
      {queued.length > 3 ? (
        <div className="agent-conversation-panel__queue-more">
          另有 {queued.length - 3} 条消息排队
        </div>
      ) : null}
    </div>
  )
}

function ComposerFooter({
  controller,
  extensions,
  queueLabel,
  submitLabel,
}: {
  controller: AgentConversationController
  extensions?: AgentConversationExtensions
  queueLabel: string
  submitLabel: string
}) {
  const { capabilities, composer, conversation, actions } = controller
  const showStop = conversation.running
    || conversation.stopping
    || conversation.paused
    || conversation.resuming
  const submitDisabled = capabilities.inputDisabled
    || composer.submitDisabled
    || conversation.paused
    || conversation.resuming

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
            disabled={capabilities.inputDisabled || conversation.resuming}
            onClick={() => void actions.resume?.()}
          >
            继续执行
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
              disabled={conversation.stopping || conversation.resuming}
              aria-label="停止生成"
            />
          </PurrTooltip>
        ) : null}
        <PurrTooltip title={`${submitLabel} (Enter)`}>
          <PurrButton
            type="primary"
            shape="circle"
            className="agent-composer__send"
            icon={<ArrowUpIcon style={{ fontSize: 16 }} />}
            disabled={submitDisabled}
            onClick={() => void actions.send()}
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
}: AgentConversationPanelProps) {
  const [uncontrolledIndexOpen, setUncontrolledIndexOpen] = React.useState(true)
  const [editingSessionId, setEditingSessionId] = React.useState<AgentSessionId | null>(null)
  const [editingSessionTitle, setEditingSessionTitle] = React.useState('')
  const resolvedIndexOpen = indexOpen ?? uncontrolledIndexOpen
  const view = buildAgentConversationPanelView({
    running: controller.conversation.running,
    queuedCount: controller.conversation.queuedSubmissions.length,
    taskPlan: controller.composer.taskPlan,
    submitMode: controller.capabilities.submitMode,
  })

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
        disabled={controller.capabilities.sessionNavigationDisabled}
      />
    )
    : null

  return (
    <div className={[
      'agent-conversation-panel',
      resolvedIndexOpen ? 'is-index-open' : 'is-index-closed',
      className,
    ].filter(Boolean).join(' ')}>
      {resolvedIndexOpen ? (
        <ConversationIndex
          sessions={controller.conversation.sessions}
          activeSessionId={controller.conversation.activeSessionId}
          sessionActivities={controller.conversation.activities}
          editingSessionId={editingSessionId}
          editingTitle={editingSessionTitle}
          isCurrentSessionEmpty={controller.conversation.messages.length === 0}
          disabled={controller.capabilities.sessionNavigationDisabled}
          context={extensions?.renderSessionContext?.()}
          extraActions={history}
          onActiveSessionChange={(id) => void controller.actions.selectSession(id)}
          onEditingSessionIdChange={setEditingSessionId}
          onEditingTitleChange={setEditingSessionTitle}
          onSaveTitle={saveSessionTitle}
          onNewSession={() => void controller.actions.createSession()}
          onCloseSession={(session) => void controller.actions.closeSession(session.id)}
          onCollapse={() => setIndexOpen(false)}
        />
      ) : (
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
          messages={controller.conversation.messages}
          loading={controller.conversation.running}
          initializing={controller.conversation.initializing}
          messageAttachmentsVersion={controller.conversation.attachmentsVersion}
          onEditMessage={controller.actions.editMessage}
          onStructuredAnswer={(answer) => void controller.actions.send(answer)}
          onResolveToolApproval={controller.actions.resolveToolApproval}
          onSubmitErrorReport={controller.actions.onSubmitErrorReport}
          afterAssistantMessage={extensions?.renderAssistantAttachment}
        />
        <Composer
          value={controller.composer.value}
          onChange={controller.composer.setValue}
          onSubmit={() => void controller.actions.send()}
          placeholder={controller.composer.placeholder}
          ariaLabel={controller.composer.ariaLabel}
          disabled={controller.capabilities.inputDisabled}
          submitDisabled={Boolean(
            controller.composer.submitDisabled
            || controller.conversation.paused
            || controller.conversation.resuming
          )}
          floatingContent={view.showTaskProgress && controller.composer.taskPlan ? (
            <TaskProgress plan={controller.composer.taskPlan} placement="topLeft" />
          ) : null}
          supplementaryContent={<QueuedSubmissions controller={controller} />}
          footer={(
            <ComposerFooter
              controller={controller}
              extensions={extensions}
              queueLabel={view.queueLabel}
              submitLabel={view.submitLabel}
            />
          )}
        />
      </main>
    </div>
  )
}

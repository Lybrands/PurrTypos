import React from 'react'
import type {
  AgentConversationActivity,
  AgentConversationMessage,
  AiTaskPlan,
} from '../agent-runtime/contracts.ts'
import { getAgentConversationCapabilities } from '../agent-runtime/conversationCapabilities.ts'
import type {
  AgentConversationController,
} from '../components/AgentConversation/controller.ts'
import { toAgentConversationSession } from '../components/AgentConversation/sessionView.ts'
import type {
  AiModelConfig,
  AiSession,
  ScreenplayConversationRuntimeInput,
  ScreenplayProject,
  ScreenplayStageCommand,
} from '../types.ts'

export interface ScreenplayQueuedSubmission {
  editing?: boolean
  id: string
  projectId: string
  sessionId: number
  content: string
  runtime: ScreenplayConversationRuntimeInput
  stageCommand?: ScreenplayStageCommand
}

export interface ScreenplayConversationBindings {
  project: ScreenplayProject
  sessions: AiSession[]
  historySessions?: AiSession[]
  historyLoading?: boolean
  historyError?: string
  deletingHistorySessionIds?: number[]
  activeSessionId: number | null
  conversationIdentity?: string
  messages: AgentConversationMessage[]
  activities: Record<string, AgentConversationActivity>
  queuedSubmissions: ScreenplayQueuedSubmission[]
  prompt: string
  setPrompt(value: string): void
  initializing: boolean
  running: boolean
  stopping: boolean
  paused: boolean
  resuming: boolean
  attachmentsVersion?: string | number
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  setSelectedModelId(id: string): void
  updateModel?: AgentConversationController['composer']['updateModel']
  openModelSettings(): void
  taskPlan?: AiTaskPlan
  actions: Pick<
    AgentConversationController['actions'],
    | 'selectSession'
    | 'createSession'
    | 'closeSession'
    | 'renameSession'
    | 'loadSessionHistory'
    | 'openHistorySession'
    | 'deleteSession'
    | 'send'
    | 'updateQueuedSubmission'
    | 'abort'
    | 'resume'
    | 'editMessage'
    | 'resolveToolApproval'
    | 'onSubmitErrorReport'
  >
}

export function createScreenplayConversationController(
  bindings: ScreenplayConversationBindings,
): AgentConversationController {
  const selectedModel = bindings.modelConfigs.find(
    (model) => model.id === bindings.selectedModelId,
  ) ?? null
  return {
    capabilities: getAgentConversationCapabilities({
      running: bindings.running,
      readOnly: bindings.project.status === 'archived',
      // 发送路径（runAgent）要求已有会话；零会话只出现在加载间隙
      // （initializing 已禁发），不声明自动建会话能力。
      sessionlessSend: false,
    }),
    conversation: {
      identity: bindings.conversationIdentity
        ?? `screenplay-session:${String(bindings.activeSessionId ?? 'none')}`,
      sessions: bindings.sessions.map((session) => (
        toAgentConversationSession(session, session.create_time)
      )),
      activeSessionId: bindings.activeSessionId,
      messages: bindings.messages,
      activities: bindings.activities,
      queuedSubmissions: bindings.queuedSubmissions.map((submission) => ({
        id: submission.id,
        sessionId: submission.sessionId,
        content: submission.content,
      })),
      initializing: bindings.initializing,
      running: bindings.running,
      stopping: bindings.stopping,
      paused: bindings.paused,
      resuming: bindings.resuming,
      attachmentsVersion: bindings.attachmentsVersion,
      history: {
        sessions: (bindings.historySessions ?? []).map((session) => (
          toAgentConversationSession(session, session.create_time)
        )),
        loading: bindings.historyLoading ?? false,
        error: bindings.historyError,
        deletingSessionIds: bindings.deletingHistorySessionIds,
      },
    },
    composer: {
      value: bindings.prompt,
      setValue: bindings.setPrompt,
      placeholder: '输入希望 Agent 完成的任务',
      ariaLabel: '输入希望剧本 Agent 完成的任务',
      ready: !bindings.initializing && bindings.activeSessionId != null,
      submitDisabled: bindings.initializing || bindings.activeSessionId == null || !bindings.prompt.trim() || !selectedModel,
      selectedModel,
      modelConfigs: bindings.modelConfigs,
      selectModel: bindings.setSelectedModelId,
      updateModel: bindings.updateModel,
      openModelSettings: bindings.openModelSettings,
      taskPlan: bindings.taskPlan,
    },
    actions: bindings.actions,
  }
}

export function useScreenplayConversationController(
  bindings: ScreenplayConversationBindings | null,
): AgentConversationController | null {
  return React.useMemo(
    () => bindings ? createScreenplayConversationController(bindings) : null,
    [bindings],
  )
}

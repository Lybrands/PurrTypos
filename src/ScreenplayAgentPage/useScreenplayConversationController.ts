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
  activeSessionId: number | null
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
    | 'send'
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
      sessionLoading: bindings.initializing,
    }),
    conversation: {
      identity: `screenplay-session:${String(bindings.activeSessionId ?? 'none')}`,
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
    },
    composer: {
      value: bindings.prompt,
      setValue: bindings.setPrompt,
      placeholder: '输入希望 Agent 完成的任务',
      ariaLabel: '输入希望剧本 Agent 完成的任务',
      submitDisabled: !bindings.prompt.trim() || !selectedModel,
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

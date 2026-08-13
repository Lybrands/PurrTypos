import type {
  AgentConversationActivity,
  AgentConversationCapabilities,
  AgentConversationMessage,
  AgentQueuedSubmission,
  AgentSessionId,
  AiTaskPlan,
} from '../../agent-runtime'
import type { AiModelConfig } from '../../types'

export interface AgentConversationSession {
  id: AgentSessionId
  title: string
  createdAt?: string
}

export interface AgentConversationController {
  capabilities: AgentConversationCapabilities
  conversation: {
    identity: string
    sessions: AgentConversationSession[]
    activeSessionId: AgentSessionId | null
    messages: AgentConversationMessage[]
    activities: Record<string, AgentConversationActivity>
    queuedSubmissions: AgentQueuedSubmission[]
    initializing: boolean
    running: boolean
    stopping: boolean
    paused: boolean
    resuming: boolean
    attachmentsVersion?: string | number
    history?: {
      sessions: AgentConversationSession[]
      loading: boolean
      error?: string
      deletingSessionIds?: AgentSessionId[]
      deleteDisabledSessionIds?: AgentSessionId[]
    }
  }
  composer: {
    value: string
    setValue(value: string): void
    placeholder: string
    ariaLabel: string
    submitDisabled: boolean
    selectedModel: AiModelConfig | null
    modelConfigs: AiModelConfig[]
    selectModel(id: string): void
    updateModel?: (
      id: string,
      patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
    ) => void
    openModelSettings(): void
    taskPlan?: AiTaskPlan
  }
  actions: {
    selectSession(id: AgentSessionId): void | Promise<void>
    createSession(): void | Promise<void>
    closeSession(id: AgentSessionId): void | Promise<void>
    renameSession(id: AgentSessionId, title: string): void | Promise<void>
    loadSessionHistory?(): void | Promise<void>
    openHistorySession?(id: AgentSessionId): void | Promise<void>
    deleteSession?(id: AgentSessionId): void | Promise<void>
    send(content?: string): void | Promise<void>
    abort(): void | Promise<void>
    resume?(): void | Promise<void>
    editMessage(index: number, content: string): void | Promise<void>
    resolveToolApproval(
      approvalId: string,
      approved: boolean,
    ): Promise<{ success: boolean; error?: string }>
    onSubmitErrorReport?(
      reportId: string,
    ): Promise<{ success: boolean; error?: string }>
  }
}

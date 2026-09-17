import type {
  AgentConversationActivity,
  AgentConversationCapabilities,
  AgentConversationMessage,
  AgentQueuedSubmission,
  AgentSessionId,
  AiTaskPlan,
} from '../../agent-runtime'
import type { AiModelConfig } from '../../types'
import type { ModelRuntimeConfigPatch } from './Composer/ModelPicker'
import type { QueuedSubmissionEdit } from '../../agent-runtime/queuedSubmission'

export interface AgentConversationSession {
  id: AgentSessionId
  title: string
  createdAt?: string
  /** 置顶：会话列表中排在置顶分组 */
  pinned?: boolean
  /** 手动排序值（拖拽后整列表重排写入）；null = 未参与手动排序 */
  sortOrder?: number | null
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
    queuePaused?: boolean
    initializing: boolean
    running: boolean
    stopping: boolean
    abortDisabled?: boolean
    paused: boolean
    resuming: boolean
    /** Product-specific wording for the shared paused-workflow action. */
    resumeLabel?: string
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
    ready?: boolean
    /** 有输入但被禁发时向用户说明原因（例如「请先选择一个章节」） */
    disabledHint?: string
    selectedModel: AiModelConfig | null
    modelConfigs: AiModelConfig[]
    selectModel(id: string): void
    updateModel?: (
      id: string,
      patch: ModelRuntimeConfigPatch,
    ) => void
    openModelSettings(): void
    taskPlan?: AiTaskPlan
  }
  actions: {
    selectSession(id: AgentSessionId): void | Promise<void>
    createSession(): void | Promise<void>
    closeSession(id: AgentSessionId): void | Promise<void>
    renameSession(id: AgentSessionId, title: string): void | Promise<void>
    /** 会话列表拖拽排序：按展示顺序回传全部会话 ID（未接线则列表不支持拖拽） */
    reorderSessions?(orderedIds: AgentSessionId[]): void | Promise<void>
    /** 会话置顶/取消置顶（未接线则不显示置顶按钮） */
    toggleSessionPinned?(id: AgentSessionId, pinned: boolean): void | Promise<void>
    loadSessionHistory?(): void | Promise<void>
    openHistorySession?(id: AgentSessionId): void | Promise<void>
    deleteSession?(id: AgentSessionId): void | Promise<void>
    send(content?: string): void | Promise<void>
    retryQueued?(): void | Promise<void>
    clearQueued?(): void | Promise<void>
    updateQueuedSubmission?(id: string, patch: QueuedSubmissionEdit | null): boolean
    abort(): void | Promise<void>
    resume?(): void | Promise<void>
    /** Regenerate at this user message using the preceding history. Do not append as a follow-up. */
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

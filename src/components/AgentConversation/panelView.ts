import type { AgentSubmitMode, AiTaskPlan } from '../../agent-runtime'
import { getTaskPlanCountLabel } from '../../agent-runtime/taskPlan.ts'

export interface AgentConversationPanelViewInput {
  running: boolean
  queuedCount: number
  taskPlan?: AiTaskPlan
  submitMode: AgentSubmitMode
}

export interface AgentConversationPanelView {
  submitLabel: string
  queueLabel: string
  showTaskProgress: boolean
  taskCountLabel: string
}

export function buildAgentConversationPanelView({
  queuedCount,
  taskPlan,
  submitMode,
}: AgentConversationPanelViewInput): AgentConversationPanelView {
  const showTaskProgress = Boolean(
    taskPlan
    && ['planned', 'running', 'paused'].includes(taskPlan.status),
  )
  return {
    submitLabel: submitMode === 'queue' ? '加入发送队列' : '发送',
    queueLabel: queuedCount > 0 ? `排队 ${queuedCount}` : '',
    showTaskProgress,
    taskCountLabel: taskPlan ? getTaskPlanCountLabel(taskPlan) : '',
  }
}

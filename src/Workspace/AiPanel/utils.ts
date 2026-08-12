import {
  CHAT_AGENT_MODES,
  type Conversation,
  type EntityId,
  type ChatAgentMode,
} from '../../types.ts'
import { AI_MODEL_PREFS_KEY_PREFIX } from './constants.ts'
import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import { KNOWN_TOOL_CALL_LABELS } from '../../components/AgentConversation/toolCallLabels.ts'

function normalizeStoredToolCallLabel(label: string): string {
  return KNOWN_TOOL_CALL_LABELS[
    label as keyof typeof KNOWN_TOOL_CALL_LABELS
  ] || label
}

function parseJsonObject<T>(value: string | null | undefined): T | undefined {
  if (!value) return undefined
  try {
    const parsed = JSON.parse(value) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as T
      : undefined
  } catch {
    return undefined
  }
}

const isChatAgentMode = (v: unknown): v is ChatAgentMode =>
  typeof v === 'string' && (CHAT_AGENT_MODES as readonly string[]).includes(v)

// ─── 模型偏好（localStorage） ───

export function getPrefsKey(bookId: EntityId | null): string {
  return `${AI_MODEL_PREFS_KEY_PREFIX}${bookId ?? '_default'}`
}

/** 若传入 validModelIds，则只接受该列表内的 model 作为有效选中项；空数组时无可用模型，默认 model 为空 */
export function loadModelPrefs(
  bookId: EntityId | null,
  validModelIds?: string[],
): {
  model: string
  chatAgentMode: ChatAgentMode
} {
  const defaultModel = validModelIds?.length ? validModelIds[0] : ''
  const isValid = (id: string) =>
    Array.isArray(validModelIds) && validModelIds.length > 0 && validModelIds.includes(id)
  const defaultChatAgentMode: ChatAgentMode = 'agent'
  try {
    const raw = localStorage.getItem(getPrefsKey(bookId))
    if (raw) {
      const p = JSON.parse(raw) as {
        model?: string
        chatAgentMode?: unknown
      }
      const model = typeof p.model === 'string' && isValid(p.model) ? p.model : defaultModel
      let chatAgentMode: ChatAgentMode
      if (isChatAgentMode(p.chatAgentMode)) {
        chatAgentMode = p.chatAgentMode
      } else {
        chatAgentMode = defaultChatAgentMode
      }
      return { model, chatAgentMode }
    }
  } catch {
    // ignore
  }
  return {
    model: defaultModel,
    chatAgentMode: defaultChatAgentMode,
  }
}

export function saveModelPrefs(
  bookId: EntityId | null,
  model: string,
  chatAgentMode: ChatAgentMode
): void {
  try {
    localStorage.setItem(
      getPrefsKey(bookId),
      JSON.stringify({ model, chatAgentMode })
    )
  } catch {
    // ignore
  }
}

/** 将接口返回的 Conversation[] 转为共享 Agent 对话消息。 */
export function parseConversationsFromApi(
  data: Conversation[],
): AgentConversationMessage[] {
  return data
    .map((item) => {
      let commentaryBlocks: string[] | undefined
      if (item.commentary_blocks) {
        try {
          const parsed = JSON.parse(item.commentary_blocks) as unknown
          if (Array.isArray(parsed) && parsed.every((x) => typeof x === 'string')) commentaryBlocks = parsed
        } catch (_) {}
      }
      if (!commentaryBlocks && item.commentary?.trim()) {
        commentaryBlocks = [item.commentary.trim()]
      }
      let taskPlan: AgentConversationMessage['taskPlan']
      if (item.task_plan) {
        try {
          const parsed = JSON.parse(item.task_plan) as unknown
          if (
            parsed &&
            typeof parsed === 'object' &&
            typeof (parsed as { title?: unknown }).title === 'string' &&
            typeof (parsed as { status?: unknown }).status === 'string' &&
            Array.isArray((parsed as { steps?: unknown }).steps)
          ) {
            taskPlan = parsed as AgentConversationMessage['taskPlan']
          }
        } catch (_) {}
      }
      let commentaryDurationsMs: number[] | undefined
      if (item.commentary_durations_ms) {
        try {
          const parsed = JSON.parse(item.commentary_durations_ms) as unknown
          if (
            Array.isArray(parsed) &&
            parsed.every((x) => typeof x === 'number' && Number.isFinite(x))
          ) {
            commentaryDurationsMs = parsed
          }
        } catch (_) {}
      }
      let assistantMsg: AgentConversationMessage = {
        role: 'assistant',
        content: item.response,
        conversationId: item.id,
        agentRunId: item.agent_run_id || undefined,
        longTaskId: item.long_task_id || undefined,
        model: item.model || undefined,
        durationMs: typeof item.duration_ms === 'number' ? item.duration_ms : undefined,
        commentary: item.commentary || undefined,
        commentaryBlocks,
        commentaryDurationsMs,
        taskPlan,
        contextCompaction: parseJsonObject<
          NonNullable<AgentConversationMessage['contextCompaction']>
        >(item.context_compaction),
        contextBudget: parseJsonObject<
          NonNullable<AgentConversationMessage['contextBudget']>
        >(item.context_budget),
      }
      const agentProcess = parseJsonObject<{
        delegations?: AgentConversationMessage['delegations']
        subAgentActivities?: AgentConversationMessage['subAgentActivities']
      }>(item.agent_process)
      if (Array.isArray(agentProcess?.delegations)) {
        assistantMsg.delegations = agentProcess.delegations
      }
      if (Array.isArray(agentProcess?.subAgentActivities)) {
        assistantMsg.subAgentActivities = agentProcess.subAgentActivities
      }
      const rawSegments = item.tool_call_segments
      if (rawSegments) {
        try {
          const parsedSegments = JSON.parse(rawSegments) as AgentConversationMessage['toolCallSegments']
          const segments = Array.isArray(parsedSegments)
            ? parsedSegments.map((segment) => ({
                ...segment,
                labels: Array.isArray(segment.labels)
                  ? segment.labels.map((label) => normalizeStoredToolCallLabel(String(label)))
                  : [],
              }))
            : []
          if (Array.isArray(segments) && segments.length > 0) {
            assistantMsg = { ...assistantMsg, toolCallSegments: segments }
          }
        } catch (_) {}
      }
      return [
        {
          role: 'user' as const,
          content: item.prompt,
          sentAt: item.create_time || undefined,
        },
        assistantMsg,
      ]
    })
    .flat()
}

import {
  CHAT_AGENT_MODES,
  type Conversation,
  type EntityId,
  type ChatAgentMode,
} from '../../types'
import type { AiModelConfig } from '../../types'
import { AI_MODEL_PREFS_KEY_PREFIX } from './constants'
import type { ChatMessage } from './hooks'

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

/** 根据 modelKey 显示模型名称；可传入自定义配置列表优先匹配，有昵称时显示昵称 */
export function formatModelName(modelKey?: string, modelConfigs?: AiModelConfig[]): string {
  if (!modelKey) return '未知模型'
  if (modelConfigs?.length) {
    const c = modelConfigs.find((m) => m.id === modelKey)
    if (c) return (c.nickname?.trim() || c.name) || '未命名'
  }
  return modelKey
}

/** 将接口返回的 Conversation[] 转为 ChatMessage[] */
export function parseConversationsFromApi(data: Conversation[]): ChatMessage[] {
  return data
    .map((item) => {
      let thinkingBlocks: string[] | undefined
      if (item.thinking_blocks) {
        try {
          const parsed = JSON.parse(item.thinking_blocks) as unknown
          if (Array.isArray(parsed) && parsed.every((x) => typeof x === 'string')) thinkingBlocks = parsed
        } catch (_) {}
      }
      if (!thinkingBlocks && item.thinking?.trim()) thinkingBlocks = [item.thinking.trim()]
      let taskPlan: ChatMessage['taskPlan']
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
            taskPlan = parsed as ChatMessage['taskPlan']
          }
        } catch (_) {}
      }
      let thinkingDurationsMs: number[] | undefined
      if (item.thinking_durations_ms) {
        try {
          const parsed = JSON.parse(item.thinking_durations_ms) as unknown
          if (
            Array.isArray(parsed) &&
            parsed.every((x) => typeof x === 'number' && Number.isFinite(x))
          ) {
            thinkingDurationsMs = parsed
          }
        } catch (_) {}
      }
      let assistantMsg: ChatMessage = {
        role: 'assistant',
        content: item.response,
        conversationId: item.id,
        model: item.model || undefined,
        durationMs: typeof item.duration_ms === 'number' ? item.duration_ms : undefined,
        thinking: item.thinking || undefined,
        thinkingBlocks,
        thinkingDurationsMs,
        taskPlan,
        contextCompaction: parseJsonObject<
          NonNullable<ChatMessage['contextCompaction']>
        >(item.context_compaction),
        contextBudget: parseJsonObject<
          NonNullable<ChatMessage['contextBudget']>
        >(item.context_budget),
      }
      const rawSegments = item.tool_call_segments
      if (rawSegments) {
        try {
          const segments = JSON.parse(rawSegments) as { textBefore: string; labels: string[] }[]
          if (Array.isArray(segments) && segments.length > 0) {
            const textBeforeJoined = segments.map((s) => s.textBefore || '').join('')
            const resp = item.response ?? ''
            if (
              resp.length > 0 &&
              textBeforeJoined.length > 0 &&
              !resp.startsWith(textBeforeJoined)
            ) {
              // 存库与片段前缀不一致时若仍走分段渲染，会丢尾文；回退为纯正文以免空白
              assistantMsg = {
                ...assistantMsg,
                content: resp,
              }
            } else {
              const contentAfterToolCalls = resp.startsWith(textBeforeJoined)
                ? resp.slice(textBeforeJoined.length)
                : ''
              assistantMsg = {
                ...assistantMsg,
                content: resp,
                toolCallSegments: segments,
                contentAfterToolCalls: contentAfterToolCalls || undefined,
              }
            }
          }
        } catch (_) {}
      }
      return [
        { role: 'user' as const, content: item.prompt },
        assistantMsg,
      ]
    })
    .flat()
}

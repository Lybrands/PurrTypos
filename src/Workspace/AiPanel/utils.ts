import { CHAT_AGENT_MODES, type Chapter, type Conversation, type EntityId, type ChatAgentMode } from '../../types'
import {
  extractTextFromLexical as extractTextFromLexicalImpl,
  formatChaptersAsText as formatChaptersAsTextImpl,
} from '../utils'
import type { AiModelConfig } from '../../types'
import { AI_MODEL_PREFS_KEY_PREFIX } from './constants'
import type { ChatMessage } from './hooks'

const isChatAgentMode = (v: unknown): v is ChatAgentMode =>
  typeof v === 'string' && (CHAT_AGENT_MODES as readonly string[]).includes(v)

/** @deprecated 请从 Workspace/utils 导入 */
export const extractTextFromLexical = extractTextFromLexicalImpl

/** @deprecated 请从 Workspace/utils 导入；需要 Chapter 类型时从 types 导入 */
export function formatChaptersAsText(chapters: Chapter[]): string {
  return formatChaptersAsTextImpl(chapters)
}

// ─── 模型偏好（localStorage） ───

export function getPrefsKey(bookId: EntityId | null): string {
  return `${AI_MODEL_PREFS_KEY_PREFIX}${bookId ?? '_default'}`
}

/** 若传入 validModelIds，则只接受该列表内的 model 作为有效选中项；空数组时无可用模型，默认 model 为空 */
export function loadModelPrefs(
  bookId: EntityId | null,
  validModelIds?: string[],
  settingsDefaultAgentMode: 'legacy' | 'subagent' = 'legacy'
): { model: string; chatAgentMode: ChatAgentMode; thinkingEnabled: boolean } {
  const defaultModel = validModelIds?.length ? validModelIds[0] : ''
  const isValid = (id: string) =>
    Array.isArray(validModelIds) && validModelIds.length > 0 && validModelIds.includes(id)
  const defaultChatAgentMode: ChatAgentMode =
    settingsDefaultAgentMode === 'subagent' ? 'expert' : 'agent'
  try {
    const raw = localStorage.getItem(getPrefsKey(bookId))
    if (raw) {
      const p = JSON.parse(raw) as {
        model?: string
        agentEnabled?: boolean
        thinkingEnabled?: boolean
        chatAgentMode?: unknown
      }
      const model = typeof p.model === 'string' && isValid(p.model) ? p.model : defaultModel
      const thinkingEnabled = typeof p.thinkingEnabled === 'boolean' ? p.thinkingEnabled : false
      let chatAgentMode: ChatAgentMode
      if (isChatAgentMode(p.chatAgentMode)) {
        chatAgentMode = p.chatAgentMode
      } else if (p.chatAgentMode === 'legacy') {
        chatAgentMode = 'agent'
      } else if (p.chatAgentMode === 'subagent') {
        chatAgentMode = 'expert'
      } else if (p.agentEnabled === false) {
        chatAgentMode = 'ask'
      } else {
        chatAgentMode = defaultChatAgentMode
      }
      return { model, chatAgentMode, thinkingEnabled }
    }
  } catch {
    // ignore
  }
  return { model: defaultModel, chatAgentMode: defaultChatAgentMode, thinkingEnabled: false }
}

export function saveModelPrefs(
  bookId: EntityId | null,
  model: string,
  chatAgentMode: ChatAgentMode,
  thinkingEnabled: boolean
): void {
  try {
    localStorage.setItem(
      getPrefsKey(bookId),
      JSON.stringify({ model, chatAgentMode, thinkingEnabled })
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
      let assistantMsg: ChatMessage = {
        role: 'assistant',
        content: item.response,
        model: item.model || undefined,
        thinking: item.thinking || undefined,
        thinkingBlocks,
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

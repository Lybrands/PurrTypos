import type { AgentConversationMessage } from './contracts.ts'

export const EMPTY_RESPONSE_MESSAGE =
  '本轮处理已结束，但模型没有生成可展示的答复。请重试或更换模型。'

export function buildHistoryConverter(): (
  m: AgentConversationMessage | { role: 'user' | 'assistant'; content: string },
) => { role: string; content: string } | null {
  return (m) => {
    if (('isError' in m && m.isError) || m.role === 'system') return null
    const text = String(m.content ?? '').trim()
    if (!text) return null
    return { role: m.role, content: text }
  }
}

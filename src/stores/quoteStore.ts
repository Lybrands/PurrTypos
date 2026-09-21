import { create } from 'zustand'
import type { UserQuoteInput } from '../components/AgentConversation/userQuote'

/**
 * 正文选区「引用」状态：编辑器「引用」动作累积（去重），
 * 输入框上方胶囊展示，发送时拼进消息、切会话清空。
 * 替代原 `ai-panel-quote-selection` CustomEvent 与 AiPanel 本地 state——
 * 悬停预览编辑器与停靠编辑器、AI 面板共享同一份数据。
 */

interface QuoteState {
  quotes: UserQuoteInput[]
  addQuote(quote: string, chapterTitle?: string): void
  removeAt(index: number): void
  removeAll(): void
}

export const useQuoteStore = create<QuoteState>((set) => ({
  quotes: [],
  addQuote: (quote, chapterTitle) => {
    const trimmed = quote.trim()
    if (!trimmed) return
    set((state) => {
      if (
        state.quotes.some(
          (q) => q.quote === trimmed && q.chapterTitle === chapterTitle,
        )
      ) {
        return state
      }
      return { quotes: [...state.quotes, { quote: trimmed, chapterTitle }] }
    })
  },
  removeAt: (index) =>
    set((state) => ({ quotes: state.quotes.filter((_, i) => i !== index) })),
  removeAll: () => set({ quotes: [] }),
}))

/** 非组件调用入口（编辑器选区「引用」按钮） */
export function addSelectionQuote(quote: string, chapterTitle?: string): void {
  useQuoteStore.getState().addQuote(quote, chapterTitle)
}

export function __resetQuoteStoreForTests(): void {
  useQuoteStore.setState({ quotes: [] })
}

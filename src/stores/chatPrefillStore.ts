import { create } from 'zustand'

/**
 * 「与 AI 讨论设定」预填请求：大纲页三处入口发起，AiPanel 消费。
 * 替代原 `open-setting-chat` CustomEvent；seq 单调递增触发消费端 effect。
 */

interface ChatPrefillState {
  seq: number
  prefill: string
  /** 发起一次全局（设定 scope）对话预填 */
  requestGlobalChat(prefill: string): void
}

export const useChatPrefillStore = create<ChatPrefillState>((set) => ({
  seq: 0,
  prefill: '',
  requestGlobalChat: (prefill) =>
    set((state) => ({ seq: state.seq + 1, prefill })),
}))

/** 非组件调用入口 */
export function requestGlobalChatPrefill(prefill: string): void {
  useChatPrefillStore.getState().requestGlobalChat(prefill)
}

export function __resetChatPrefillStoreForTests(): void {
  useChatPrefillStore.setState({ seq: 0, prefill: '' })
}

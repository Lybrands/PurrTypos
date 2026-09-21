import { create } from 'zustand'

/**
 * 编辑器命令桥：命令面板 / 全局快捷键 → 当前挂载的 EditorPanel。
 * EditorPanel 挂载时注册 handler、卸载注销（悬停预览与停靠两处挂载，
 * 后注册者生效，卸载自动回落）；命令侧不关心编辑器是否在场。
 * 替代原 editor-open-diff-history / editor-reformat / editor-copy-title /
 * editor-copy-content 四个无载荷 CustomEvent。
 */

export type EditorCommandKey =
  | 'openDiffHistory'
  | 'reformat'
  | 'copyTitle'
  | 'copyContent'

export type EditorCommandHandlers = Record<EditorCommandKey, () => void>

interface EditorCommandState {
  handlers: EditorCommandHandlers | null
  register(handlers: EditorCommandHandlers): () => void
  run(command: EditorCommandKey): void
}

export const useEditorCommandStore = create<EditorCommandState>((set, get) => ({
  handlers: null,
  register: (handlers) => {
    set({ handlers })
    return () => {
      // 仅当仍是本次注册时注销，避免后注册者被先卸载者顶掉
      if (get().handlers === handlers) set({ handlers: null })
    }
  },
  run: (command) => {
    get().handlers?.[command]()
  },
}))

/** 非组件调用入口（命令面板 / 快捷键） */
export function runEditorCommand(command: EditorCommandKey): void {
  useEditorCommandStore.getState().run(command)
}

export function __resetEditorCommandStoreForTests(): void {
  useEditorCommandStore.setState({ handlers: null })
}

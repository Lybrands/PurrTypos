export type ShortcutKey =
  | 'mod'
  | 'shift'
  | 'plus'
  | 'minus'
  | 'zero'
  | 'enter'
  | 'tab'
  | 'escape'
  | 'backslash'
  | 'K'
  | 'F'
  | 'H'

export interface KeyboardShortcut {
  label: string
  detail?: string
  keys: ShortcutKey[]
}

export interface KeyboardShortcutGroup {
  title: string
  shortcuts: KeyboardShortcut[]
}

export const KEYBOARD_SHORTCUT_GROUPS: KeyboardShortcutGroup[] = [
  {
    title: '界面',
    shortcuts: [
      { label: '增大字体', keys: ['mod', 'plus'] },
      { label: '减小字体', keys: ['mod', 'minus'] },
      { label: '恢复默认字体', keys: ['mod', 'zero'] },
    ],
  },
  {
    title: '工作台',
    shortcuts: [
      { label: '打开命令面板', keys: ['mod', 'K'] },
      { label: '搜索当前作品', keys: ['mod', 'F'] },
      { label: '打开章节修改历史', keys: ['mod', 'shift', 'H'] },
    ],
  },
  {
    title: '编辑与对话',
    shortcuts: [
      { label: '唤起行内 AI', detail: '正文编辑器', keys: ['backslash'] },
      { label: '接受 AI 续写', detail: '出现续写建议时', keys: ['tab'] },
      { label: '提交行内改写', detail: '行内改写面板', keys: ['mod', 'enter'] },
      { label: '应用行内改写', detail: '行内改写面板', keys: ['mod', 'shift', 'enter'] },
      { label: '发送消息', detail: 'AI 对话输入框', keys: ['enter'] },
      { label: '输入换行', detail: 'AI 对话输入框', keys: ['shift', 'enter'] },
      { label: '关闭当前浮层', keys: ['escape'] },
    ],
  },
]

export function isApplePlatform(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform) || /Mac OS X/.test(navigator.userAgent)
}

export function getShortcutKeyLabel(key: ShortcutKey, apple = isApplePlatform()): string {
  const labels: Record<ShortcutKey, string> = {
    mod: apple ? '⌘' : 'Ctrl',
    shift: apple ? '⇧' : 'Shift',
    plus: '+',
    minus: '−',
    zero: '0',
    enter: apple ? '↩' : 'Enter',
    tab: 'Tab',
    escape: 'Esc',
    backslash: '\\',
    K: 'K',
    F: 'F',
    H: 'H',
  }
  return labels[key]
}

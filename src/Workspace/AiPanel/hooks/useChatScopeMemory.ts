import React from 'react'
import type { EntityId } from '../../../types'
import type { ChatSessionScope } from './useAiSessions'

/**
 * 对话作用域（章节/全局）按书记忆。
 *
 * - 用户手动切换（分段控件 / 从设定面板打开全局对话）时写入 localStorage，
 *   再次进入该书工作台时恢复上次的手动选择；
 * - 无章节时的「自动全局」不落盘：它跟随章节状态推导，不参与恢复。
 */

const CHAT_SCOPE_STORAGE_KEY = 'purrtypos_chat_scope_by_book'

export function loadChatScopeMap(): Record<string, ChatSessionScope> {
  try {
    const raw = localStorage.getItem(CHAT_SCOPE_STORAGE_KEY)
    const obj = raw ? JSON.parse(raw) : {}
    return obj && typeof obj === 'object' ? obj : {}
  } catch {
    return {}
  }
}

export function rememberChatScope(bookId: EntityId, scope: ChatSessionScope) {
  try {
    const map = loadChatScopeMap()
    map[String(bookId)] = scope
    localStorage.setItem(CHAT_SCOPE_STORAGE_KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
}

export function useChatScopeMemory(
  bookId: EntityId | null | undefined,
  chapterId: EntityId | null | undefined,
) {
  const [chatScope, setChatScope] = React.useState<ChatSessionScope>(() => (
    bookId == null
      ? 'chapter'
      : (loadChatScopeMap()[String(bookId)] ?? 'chapter')
  ))
  // 全局定位是「自动」还是「用户手动」的标记：自动切到全局后，一旦章节
  // 恢复（上次章节/回退/新建）就切回章节范围；用户手动切换则以用户为准。
  const autoGlobalScopeRef = React.useRef(false)

  /** bookId 变化时：恢复该书上次手动选择的作用域 */
  React.useEffect(() => {
    if (bookId == null) return
    autoGlobalScopeRef.current = false
    setChatScope(
      loadChatScopeMap()[String(bookId)] === 'setting' ? 'setting' : 'chapter',
    )
  }, [bookId])

  React.useEffect(() => {
    if (chapterId == null) {
      if (chatScope === 'chapter') {
        autoGlobalScopeRef.current = true
        setChatScope('setting')
      }
    } else if (autoGlobalScopeRef.current && chatScope === 'setting') {
      autoGlobalScopeRef.current = false
      setChatScope('chapter')
    }
  }, [chatScope, chapterId])

  /** 用户手动切换作用域：记忆选择，再次进入工作台时恢复 */
  const handleChatScopeChange = React.useCallback((scope: ChatSessionScope) => {
    autoGlobalScopeRef.current = false
    setChatScope(scope)
    if (bookId != null) rememberChatScope(bookId, scope)
  }, [bookId])

  /** 从设定面板等入口打开全局对话：同为用户手动选择，一并记忆 */
  const openGlobalChat = React.useCallback(() => {
    handleChatScopeChange('setting')
  }, [handleChatScopeChange])

  return { chatScope, handleChatScopeChange, openGlobalChat }
}

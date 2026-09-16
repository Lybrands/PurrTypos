import type { AgentConversationController } from './controller.ts'

export function isComposerSubmitDisabled(controller: AgentConversationController, content = controller.composer.value): boolean {
  const { capabilities, conversation, composer } = controller
  // 无会话时默认允许发送（发送路径负责自动创建会话，如写作面板的
  // useChatSubmit ensureSession）；无法自动建会话的面板显式关闭 sessionlessSend。
  return capabilities.inputDisabled || conversation.initializing || conversation.stopping
    || conversation.paused || conversation.resuming
    || (conversation.activeSessionId == null && !capabilities.sessionlessSend)
    || composer.ready === false || !composer.selectedModel || !content.trim()
    || (content === composer.value && composer.submitDisabled)
}

/**
 * 引导式空态（隐藏对话列表 + 「开启你的第一段对话」）的判定：
 * 仅当本范围没有任何对话（会话数为 0，如默认会话创建在途、或唯一会话
 * 被关闭后的间隙）且不在加载/运行中。只要存在会话——哪怕只有一个
 * 尚无消息的空会话——都正常呈现对话列表。
 */
export function isEmptyConversationPresentation(conversation: {
  sessions: unknown[]
  running?: boolean
  initializing?: boolean
}): boolean {
  if (conversation.initializing) return false
  if (conversation.running) return false
  return conversation.sessions.length === 0
}

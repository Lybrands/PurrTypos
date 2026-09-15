export interface AgentCompletionNotification {
  runId?: string
  conversationTitle?: string
}

function isBackground(): boolean {
  return document.visibilityState === 'hidden' || !document.hasFocus()
}

export function prepareAgentCompletionNotifications(): void {
  if (window.purrDesktop?.showNotification) return
  if (typeof Notification === 'undefined' || Notification.permission !== 'default') return
  void Notification.requestPermission().catch(() => undefined)
}

export async function notifyAgentCompletion({
  runId,
  conversationTitle,
}: AgentCompletionNotification): Promise<void> {
  if (!isBackground()) return
  const title = 'Agent 任务已完成'
  const normalizedConversationTitle = String(conversationTitle || '').trim()
  const body = normalizedConversationTitle
    ? `「${normalizedConversationTitle}」已完成，可以返回查看结果。`
    : '任务已完成，可以返回查看结果。'

  if (window.purrDesktop?.showNotification) {
    await window.purrDesktop.showNotification({ title, body, tag: runId }).catch(() => undefined)
    return
  }
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  const notification = new Notification(title, {
    body,
    tag: runId ? `purrtypos-agent-complete:${runId}` : undefined,
  })
  notification.onclick = () => {
    window.focus()
    notification.close()
  }
}

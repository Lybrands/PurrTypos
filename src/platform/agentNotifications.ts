export interface AgentCompletionNotification {
  runId?: string
  conversationTitle?: string
}

function isBackground(): boolean {
  return document.visibilityState === 'hidden' || !document.hasFocus()
}

/** 通知标题展示长度上限，超长内容截断加省略号。 */
const NOTIFICATION_TITLE_LIMIT = 60

function clampTitle(value: string): string {
  const normalized = value.trim()
  return normalized.length > NOTIFICATION_TITLE_LIMIT
    ? `${normalized.slice(0, NOTIFICATION_TITLE_LIMIT - 1)}…`
    : normalized
}

/** 对话标题作为通知标题内容，正文只保留完成提示。 */
export function buildAgentCompletionContent({
  conversationTitle,
}: AgentCompletionNotification): { title: string; body: string } {
  const normalizedConversationTitle = String(conversationTitle || '').trim()
  return {
    title: normalizedConversationTitle
      ? clampTitle(normalizedConversationTitle)
      : 'Agent 任务已完成',
    body: '任务已完成，可以返回查看结果。',
  }
}

export function prepareAgentCompletionNotifications(): void {
  if (window.purrDesktop?.showNotification) return
  if (typeof Notification === 'undefined' || Notification.permission !== 'default') return
  void Notification.requestPermission().catch(() => undefined)
}

export async function notifyAgentCompletion(
  notification: AgentCompletionNotification,
): Promise<void> {
  if (!isBackground()) return
  const { title, body } = buildAgentCompletionContent(notification)

  if (window.purrDesktop?.showNotification) {
    await window.purrDesktop
      .showNotification({ title, body, tag: notification.runId })
      .catch(() => undefined)
    return
  }
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  const webNotification = new Notification(title, {
    body,
    icon: '/PurrTypos.png',
    tag: notification.runId ? `purrtypos-agent-complete:${notification.runId}` : undefined,
  })
  webNotification.onclick = () => {
    window.focus()
    webNotification.close()
  }
}

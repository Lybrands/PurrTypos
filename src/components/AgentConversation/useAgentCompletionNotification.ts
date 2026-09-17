import React from 'react'
import type { AgentConversationController } from './controller'
import { isUntitledSessionTitle } from './sessionTitle'
import { notifyAgentCompletion } from '../../platform/agentNotifications'

interface CompletionWatchState {
  identity: string
  armed: boolean
  awaitingTerminal: boolean
  assistantKey: string
  assistantContent: string
}

function latestAssistant(controller: AgentConversationController) {
  return [...controller.conversation.messages]
    .reverse()
    .find((message) => message.role === 'assistant')
}

function assistantKey(message: ReturnType<typeof latestAssistant>): string {
  if (!message) return ''
  return message.agentRunId
    || message.clientTurnId
    || message.sentAt
    || ''
}

/** 会话已命名则用其标题；未命名（「新对话」）时回退到触发本轮任务的用户消息。 */
function resolveCompletionTitle(controller: AgentConversationController): string | undefined {
  const conversation = controller.conversation
  const sessionTitle = conversation.sessions.find(
    (session) => session.id === conversation.activeSessionId,
  )?.title
  if (sessionTitle && !isUntitledSessionTitle(sessionTitle)) return sessionTitle

  const latestUserText = [...conversation.messages]
    .reverse()
    .find((message) => message.role === 'user')?.content
    .trim()
  return latestUserText || undefined
}

export function useAgentCompletionNotification(
  controller: AgentConversationController,
): void {
  const watch = React.useRef<CompletionWatchState>({
    identity: controller.conversation.identity,
    armed: false,
    awaitingTerminal: false,
    assistantKey: '',
    assistantContent: '',
  })

  React.useEffect(() => {
    const conversation = controller.conversation
    if (watch.current.identity !== conversation.identity) {
      watch.current = {
        identity: conversation.identity,
        armed: conversation.running,
        awaitingTerminal: false,
        assistantKey: assistantKey(latestAssistant(controller)),
        assistantContent: latestAssistant(controller)?.content ?? '',
      }
      return
    }
    if (conversation.running) {
      if (!watch.current.armed) {
        const assistant = latestAssistant(controller)
        watch.current.assistantKey = assistantKey(assistant)
        watch.current.assistantContent = assistant?.content ?? ''
      }
      watch.current.armed = true
      watch.current.awaitingTerminal = false
      return
    }
    if (watch.current.armed) {
      watch.current.armed = false
      watch.current.awaitingTerminal = true
    }
    if (!watch.current.awaitingTerminal) return

    const activeActivity = conversation.activeSessionId == null
      ? undefined
      : conversation.activities[String(conversation.activeSessionId)]
        ?? conversation.activities[conversation.activeSessionId]
    const assistant = latestAssistant(controller)
    const rejected = conversation.paused
      || conversation.stopping
      || conversation.resuming
      || activeActivity?.state === 'failed'
      || activeActivity?.state === 'canceled'
      || activeActivity?.state === 'paused'
      || assistant?.isError
      || Boolean(assistant?.termination)
    if (rejected) {
      watch.current.awaitingTerminal = false
      return
    }
    const assistantContent = assistant?.content ?? ''
    const assistantAdvanced = Boolean(assistantContent.trim()) && (
      assistantKey(assistant) !== watch.current.assistantKey
      || assistantContent !== watch.current.assistantContent
    )
    const completed = activeActivity?.state === 'completed' || assistantAdvanced
    if (!completed) return

    watch.current.awaitingTerminal = false
    void notifyAgentCompletion({
      runId: assistant?.agentRunId,
      conversationTitle: resolveCompletionTitle(controller),
    })
  }, [
    controller,
    controller.conversation.activities,
    controller.conversation.activeSessionId,
    controller.conversation.identity,
    controller.conversation.messages,
    controller.conversation.paused,
    controller.conversation.resuming,
    controller.conversation.running,
    controller.conversation.sessions,
    controller.conversation.stopping,
  ])
}

import React from 'react'
import type { AgentConversationController } from './controller'
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
    const sessionTitle = conversation.sessions.find(
      (session) => session.id === conversation.activeSessionId,
    )?.title
    void notifyAgentCompletion({
      runId: assistant?.agentRunId,
      conversationTitle: sessionTitle,
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

import type { AgentConversationController } from './controller.ts'

export function isComposerSubmitDisabled(controller: AgentConversationController, content = controller.composer.value): boolean {
  const { capabilities, conversation, composer } = controller
  return capabilities.inputDisabled || conversation.initializing || conversation.stopping
    || conversation.paused || conversation.resuming || conversation.activeSessionId == null
    || composer.ready === false || !composer.selectedModel || !content.trim()
    || (content === composer.value && composer.submitDisabled)
}

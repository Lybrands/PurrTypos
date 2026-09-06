import type { AgentConversationController } from '../components/AgentConversation/controller.ts'
import { getAgentConversationCapabilities } from '../agent-runtime/conversationCapabilities.ts'

export function createAnalysisConversationController(
  bindings: Omit<AgentConversationController, 'capabilities'>,
): AgentConversationController {
  return {
    ...bindings,
    capabilities: getAgentConversationCapabilities({
      running: bindings.conversation.running,
      readOnly: false,
      sessionLoading: bindings.conversation.initializing,
    }),
  }
}

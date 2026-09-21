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
      // 发送路径（sendAnalysisMessage）要求已有会话；零会话只出现在加载
      // 间隙（initializing 已禁发），loadSessions 会自动补建默认会话。
      sessionlessSend: false,
    }),
  }
}

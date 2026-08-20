import type React from 'react'
import type { AgentConversationMessage } from '../../agent-runtime'

export interface AgentConversationExtensions {
  renderSessionContext?(): React.ReactNode
  renderComposerLeading?(): React.ReactNode
  renderAssistantAttachment?(
    message: AgentConversationMessage,
    index: number,
  ): React.ReactNode
  renderAssistantActions?(
    message: AgentConversationMessage,
    index: number,
  ): React.ReactNode
}

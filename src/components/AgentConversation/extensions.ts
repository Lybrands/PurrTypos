import type React from 'react'
import type { AgentConversationMessage } from '../../agent-runtime'

export interface AgentComposerCommand {
  id: string
  label: string
  description?: string
  keywords?: string[]
  active?: boolean
  disabled?: boolean
  onSelect(): void
}

export interface AgentConversationExtensions {
  renderSessionContext?(): React.ReactNode
  renderComposerLeading?(): React.ReactNode
  composerCommands?: AgentComposerCommand[]
  renderAssistantAttachment?(
    message: AgentConversationMessage,
    index: number,
  ): React.ReactNode
  renderAssistantActions?(
    message: AgentConversationMessage,
    index: number,
  ): React.ReactNode
}

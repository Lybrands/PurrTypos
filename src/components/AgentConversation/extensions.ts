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

export interface AgentComposerActionMenu {
  triggers: string[]
  title?: string
  buttonLabel?: string
  render(context: { close(): void }): React.ReactNode
}

export interface AgentConversationExtensions {
  composerActionMenu?: AgentComposerActionMenu
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

import type { AgentConversationMessage } from '../../agent-runtime'
import { markdownToPlainText } from '../../utils/markdown.ts'

export interface AssistantCopyView {
  visible: boolean
  markdown: string
  plainText: string
}

export function getAssistantRenderableMarkdown(
  message: Pick<AgentConversationMessage, 'content' | 'canonicalOutput'>,
): string {
  const canonicalFinal = message.canonicalOutput?.finalText
  return canonicalFinal?.trim() ? canonicalFinal : message.content || ''
}

export function buildAssistantCopyView({
  message,
  isLastAssistant,
  loading,
  showPlaceholder,
  deferPlainText = false,
}: {
  message: AgentConversationMessage
  isLastAssistant: boolean
  loading: boolean
  showPlaceholder: boolean
  deferPlainText?: boolean
}): AssistantCopyView {
  const markdown = getAssistantRenderableMarkdown(message).trim()
  const plainText = deferPlainText ? markdown : markdownToPlainText(markdown)
  return {
    visible: Boolean(
      message.role === 'assistant'
      && !message.isError
      && !showPlaceholder
      && !(isLastAssistant && loading)
      && plainText,
    ),
    markdown,
    plainText,
  }
}

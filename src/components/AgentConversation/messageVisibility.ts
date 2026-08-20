export function assistantMessageVisible(input: {
  hasVisibleContent: boolean
  hasAttachment: boolean
  hasStatus?: boolean
  isLast: boolean
  loading: boolean
}): boolean {
  return input.hasVisibleContent
    || input.hasAttachment
    || Boolean(input.hasStatus)
    || (input.isLast && input.loading)
}

export function latestAssistantMessageIndex(
  messages: ReadonlyArray<{ role: string }>,
): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') return index
  }
  return -1
}

export function assistantMessageMetadataVisible(input: {
  isLast: boolean
  loading: boolean
}): boolean {
  return !input.isLast || !input.loading
}

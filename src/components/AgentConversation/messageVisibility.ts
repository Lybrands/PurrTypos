export function assistantMessageVisible(input: {
  hasVisibleContent: boolean
  hasAttachment: boolean
  isLast: boolean
  loading: boolean
}): boolean {
  return input.hasVisibleContent
    || input.hasAttachment
    || (input.isLast && input.loading)
}

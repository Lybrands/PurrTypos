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

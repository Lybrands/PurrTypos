export function canRetryHttpRequest(
  method: string | undefined,
  responseStatus?: number,
): boolean {
  const normalizedMethod = String(method || 'GET').toUpperCase()
  if (normalizedMethod !== 'GET' && normalizedMethod !== 'HEAD') return false
  if (responseStatus == null) return true
  return responseStatus === 408
    || responseStatus === 425
    || responseStatus === 429
    || responseStatus >= 500
}

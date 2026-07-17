export function formatWords(wordCount: number): string {
  if (wordCount >= 10000) {
    return `${(wordCount / 10000).toFixed(wordCount >= 1000000 ? 0 : 1)} 万`
  }
  return String(wordCount)
}

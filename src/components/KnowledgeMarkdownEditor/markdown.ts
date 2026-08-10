const MARKDOWN_LIKE_PATTERN =
  /^#+\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+|\n\s*[-*+]\s|\n#+\s|^>\s|^\s*\|.+\|/m

export function looksLikeMarkdown(text: string): boolean {
  return Boolean(text.trim()) && MARKDOWN_LIKE_PATTERN.test(text)
}

export function appendImportedMarkdown(
  currentMarkdown: string,
  importedMarkdown: string,
): string {
  return currentMarkdown.trim()
    ? `${currentMarkdown}\n\n${importedMarkdown}`
    : importedMarkdown
}

import type { Editor } from '@tiptap/core'
import { Extension } from '@tiptap/core'
import { markdownToHtml } from '../../utils/markdown'

const MARKDOWN_LIKE_PATTERN =
  /^#+\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+|\n\s*[-*+]\s|\n#+\s|^>\s|^\s*\|.+\|/m

/** Tab indents list items and inserts a literal tab everywhere else. */
export const LiteralTab = Extension.create({
  name: 'literalTab',
  addKeyboardShortcuts() {
    return {
      Tab: () => {
        if (this.editor.commands.sinkListItem('listItem')) return true
        this.editor.commands.insertContent('\t')
        return true
      },
    }
  },
})

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

export function handleMarkdownPaste(
  editor: Editor | null,
  event: ClipboardEvent,
): boolean {
  const text = event.clipboardData?.getData('text/plain') ?? ''
  if (!looksLikeMarkdown(text)) return false

  event.preventDefault()
  editor?.commands.insertContent(markdownToHtml(text))
  return true
}

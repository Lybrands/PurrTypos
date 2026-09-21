/**
 * 用户消息里的块引用约定：行首连续的 `> ` 行渲染成引用块。
 * 约定与发送侧（AiPanel 的 ai-panel-quote-selection）一致，
 * 随消息字符串持久化（Conversation.prompt），编辑/重发/历史回放天然兼容。
 *
 * 多条引用拼成一个连续引用块（之间用 `>` 空行分隔），
 * 保证 splitUserQuote 能整体拆出、气泡渲染不漏。
 */

export interface UserQuoteInput {
  quote: string
  chapterTitle?: string
}

export interface UserQuoteSplit {
  quoteLines: string[]
  body: string
}

/** 把以 `> ` 开头的连续前缀行拆出来；无引用块时 quoteLines 为空 */
export function splitUserQuote(content: string): UserQuoteSplit {
  const lines = content.split('\n')
  const quoteLines: string[] = []
  let i = 0
  while (i < lines.length) {
    const match = lines[i].match(/^>[ \t]?(.*)$/)
    if (!match) break
    quoteLines.push(match[1])
    i++
  }
  if (quoteLines.length === 0) return { quoteLines: [], body: content }
  return {
    quoteLines,
    body: lines.slice(i).join('\n').replace(/^\n+/, ''),
  }
}

function quoteBlockLines({ quote, chapterTitle }: UserQuoteInput): string[] {
  const title = chapterTitle?.trim()
  const header = title ? `引用《${title}》：` : '引用：'
  const lines = quote.trim().split('\n').map((line) => line)
  return [header, ...lines]
}

/**
 * 把一条或多条选中文本组装成块引用预填文本。
 * 多条之间以 `>` 空行分隔，整体仍是一个连续引用块。
 */
export function buildUserQuotesPrefill(quotes: UserQuoteInput[]): string {
  const valid = quotes.filter((q) => q.quote.trim())
  if (valid.length === 0) return ''
  const lines: string[] = []
  valid.forEach((input, index) => {
    if (index > 0) lines.push('')
    lines.push(...quoteBlockLines(input))
  })
  const quoted = lines.map((line) => `> ${line}`.trimEnd()).join('\n')
  return `${quoted}\n\n`
}

/** 单条引用的便捷封装 */
export function buildUserQuotePrefill(quote: string, chapterTitle?: string): string {
  return buildUserQuotesPrefill([{ quote, chapterTitle }])
}

import { marked } from 'marked'
import TurndownService from 'turndown'
// @ts-expect-error no types
import { gfm } from 'turndown-plugin-gfm'

/** 空段落占位符：用于保留连续空白行，避免 Turndown 的 join() 把多个空段落合并成一个 */
const BLANK_PLACEHOLDER = '\u200B'

/** 单元格文本转 GFM 表格单元格：转义管道符并 trim */
function cellText(text: string): string {
  return String(text ?? '')
    .replace(/\|/g, '\\|')
    .replace(/\n/g, ' ')
    .trim()
}

/** 从 DOM 表格节点生成 GFM 表格 Markdown（无表头时用首行作表头，避免被 turndown-plugin-gfm 当成 keep 输出成 HTML） */
function tableToMarkdown(table: HTMLTableElement): string {
  const rows: string[][] = []
  const trs = table.querySelectorAll('tr')
  for (let i = 0; i < trs.length; i++) {
    const cells: string[] = []
    const cellsNodes = trs[i].querySelectorAll('th, td')
    for (let j = 0; j < cellsNodes.length; j++) {
      cells.push(cellText(cellsNodes[j].textContent || ''))
    }
    if (cells.length) rows.push(cells)
  }
  if (rows.length === 0) return ''
  const colCount = Math.max(...rows.map((r) => r.length))
  const separator = '|' + Array(colCount).fill('---').join('|') + '|'
  const lines = [
    '| ' + rows[0].join(' | ') + ' |',
    separator,
    ...rows.slice(1).map((r) => '| ' + r.join(' | ') + ' |'),
  ]
  return '\n\n' + lines.join('\n') + '\n\n'
}

const turndown = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
  // 每个空块（如 <p></p>）输出占位，避免多个空白行被合并
  blankReplacement(content: string, node: HTMLElement) {
    const blockTags = /^(P|DIV|H[1-6]|LI|TR|BLOCKQUOTE|PRE|HR|TABLE|THEAD|TBODY|TFOOT|TH|TD)$/i
    const isBlock = blockTags.test(node.nodeName)
    return isBlock ? `\n\n${BLANK_PLACEHOLDER}\n\n` : ''
  },
})
turndown.use(gfm)
// 无表头（首行非 th）的表格会被 GFM 插件 keep 成 HTML，这里统一把「表格」都转成 GFM，避免保存后展示成代码
turndown.addRule('tableAlwaysMarkdown', {
  filter: (node) => node.nodeName === 'TABLE',
  replacement: (_content, node) => tableToMarkdown(node as HTMLTableElement),
})

/** 将「仅含空白占位符」的段落还原为真正空段落，避免编辑时需退格两次才能删掉空白行 */
function normalizeBlankPlaceholderInHtml(html: string): string {
  // 匹配仅含零宽空格（字符或实体）及空白的 <p>，还原为 <p></p>
  return html.replace(/<p>\s*(\u200B|&#8203;|&#x200B;)\s*<\/p>/gi, '<p></p>')
}

/**
 * Markdown 转 HTML（供 TipTap 等编辑器使用）
 */
export function markdownToHtml(md: string): string {
  if (!md?.trim()) return '<p></p>'
  const html = marked.parse(md.trim(), { async: false, gfm: true })
  return normalizeBlankPlaceholderInHtml(html || '<p></p>')
}

/**
 * HTML 转 Markdown（从 TipTap getHTML() 等得到的内容）
 */
export function htmlToMarkdown(html: string): string {
  if (!html?.trim()) return ''
  return turndown.turndown(html.trim())
}

/** 无 DOM 环境下的保底转换；浏览器端优先使用下方的结构化 HTML 遍历。 */
function markdownToPlainTextFallback(markdown: string): string {
  return markdown
    .replace(/```[^\n]*\n([\s\S]*?)```/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)/gm, '')
    .replace(/[*_~]+/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * Markdown 转可粘贴的纯文本。
 *
 * 保留段落、列表、代码和表格的可读结构，但移除 Markdown 标记与链接地址。
 * 主要用于复制 AI 最终回答，不包含思考和工具调用过程。
 */
export function markdownToPlainText(markdown: string): string {
  if (!markdown?.trim()) return ''
  if (typeof DOMParser === 'undefined') {
    return markdownToPlainTextFallback(markdown)
  }

  const html = marked.parse(markdown.trim(), { async: false, gfm: true })
  const documentNode = new DOMParser().parseFromString(html || '', 'text/html')
  const blockTags = new Set([
    'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DIV', 'DL', 'FIELDSET',
    'FIGCAPTION', 'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5',
    'H6', 'HEADER', 'HR', 'MAIN', 'NAV', 'P', 'PRE', 'SECTION',
  ])

  const readNode = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue ?? ''
    if (node.nodeType !== Node.ELEMENT_NODE) return ''

    const element = node as HTMLElement
    const tag = element.tagName
    if (tag === 'SCRIPT' || tag === 'STYLE') return ''
    if (tag === 'BR') return '\n'
    if (tag === 'IMG') return element.getAttribute('alt') ?? ''

    const children = Array.from(element.childNodes).map(readNode).join('')
    if (tag === 'LI') return `• ${children.trim()}\n`
    if (tag === 'TR') {
      const cells = Array.from(element.children)
        .filter((child) => child.tagName === 'TH' || child.tagName === 'TD')
        .map((child) => Array.from(child.childNodes).map(readNode).join('').trim())
      return `${cells.join('\t')}\n`
    }
    if (tag === 'THEAD' || tag === 'TBODY' || tag === 'TFOOT' || tag === 'TABLE') {
      return `${children}\n`
    }
    if (tag === 'UL' || tag === 'OL') return `${children}\n`
    if (blockTags.has(tag)) return `${children}\n\n`
    return children
  }

  return Array.from(documentNode.body.childNodes)
    .map(readNode)
    .join('')
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

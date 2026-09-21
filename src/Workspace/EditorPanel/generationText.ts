/**
 * AI 生成文本 → 正文纯文本。正文按纯文本段落存储（见 editorText.ts），
 * 模型即便被要求"只输出纯文本"仍常带 Markdown 装饰；替换/插入前统一剥离，
 * 保证预览与落盘一致。
 */

/** 剥离常见 Markdown 装饰，收敛空行；保留文字本身与单换行分段 */
export function normalizeGeneratedPlainText(input: string): string {
  let text = input.replace(/\r\n?/g, '\n')

  // 围栏代码块：去掉围栏行，保留内部内容
  text = text.replace(/^```[^\n]*\n?/gm, '')
  text = text.replace(/^```[ \t]*$/gm, '')

  const lines = text.split('\n')
  const out: string[] = []
  for (let line of lines) {
    // 引用与标题前缀
    line = line.replace(/^[ \t]*(>+)[ \t]?/, '')
    line = line.replace(/^[ \t]{0,3}#{1,6}[ \t]+/, '')
    // 列表标记（无序 / 有序）
    line = line.replace(/^[ \t]*[-*+][ \t]+/, '')
    line = line.replace(/^[ \t]*\d{1,3}[.、)][ \t]+/, '')
    // 行内装饰：加粗/斜体/删除线/行内代码（单下划线斜体易误伤中文，不处理）
    line = line.replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, '$2')
    line = line.replace(/\*(?=\S)([^*\n]+?)(?<=\S)\*/g, '$1')
    line = line.replace(/~~(?=\S)(.+?)(?<=\S)~~/g, '$1')
    line = line.replace(/`([^`\n]+)`/g, '$1')
    // 段首行内标题残留（如「**标题**：」已由加粗规则处理）
    out.push(line.replace(/[ \t]+$/, ''))
  }

  return out
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

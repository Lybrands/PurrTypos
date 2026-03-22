/**
 * 工具路由 — 向量检索用 query 文本（固定结构，便于向量化）
 *
 * 规则：
 * - 必须包含「当前用户提问」。
 * - 向量匹配固定采用最近 **3 轮**对话历史（每轮：用户 + 如有则助手）+【当前提问】。
 * - 格式简洁、标签化，适合 embedding。
 */

/** 向量匹配用历史轮数（固定 3，与意图/Ollama 共用同一拼接文本） */
const VECTOR_HISTORY_ROUNDS = 3

/** 单条 user/assistant 内容截断长度，控制向量输入体量 */
const TURN_CONTENT_MAX = 500

function trimForEmbed(s, maxLen = TURN_CONTENT_MAX) {
  const t = String(s || '').replace(/\s+/g, ' ').trim()
  if (!t) return ''
  return t.length <= maxLen ? t : `${t.slice(0, maxLen)}…`
}

/**
 * 从对话 messages 生成用于工具路由向量检索的单一文本。
 * @param {Array<{ role?: string, content?: string }>} messages
 * @returns {string} 格式化后的检索文本；无法解析时返回空字符串
 */
function buildToolRouterEmbeddingQuery(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return ''

  const linear = messages.filter(
    (m) => m && (m.role === 'user' || m.role === 'assistant'),
  )
  if (linear.length === 0) return ''

  let lastUserIdx = -1
  for (let i = linear.length - 1; i >= 0; i--) {
    if (linear[i].role === 'user') {
      lastUserIdx = i
      break
    }
  }
  if (lastUserIdx < 0) return ''

  const currentQuestion = trimForEmbed(linear[lastUserIdx].content)
  if (!currentQuestion) return ''

  const before = linear.slice(0, lastUserIdx)
  /** @type {{ user: string, assistant: string }[]} */
  const rounds = []
  for (let i = 0; i < before.length; i++) {
    const m = before[i]
    if (m.role !== 'user') continue
    const u = trimForEmbed(m.content)
    const next = before[i + 1]
    const a =
      next && next.role === 'assistant' ? trimForEmbed(next.content) : ''
    rounds.push({ user: u, assistant: a })
    if (next && next.role === 'assistant') i += 1
  }

  const tailRounds = rounds.slice(-VECTOR_HISTORY_ROUNDS)

  const lines = []
  lines.push('【对话历史】')
  if (tailRounds.length === 0) {
    lines.push('（无）')
  } else {
    tailRounds.forEach((r, idx) => {
      lines.push(`第${idx + 1}轮`)
      lines.push(`用户：${r.user || '（空）'}`)
      lines.push(`助手：${r.assistant || '（无）'}`)
      lines.push('')
    })
  }
  lines.push('【当前提问】')
  lines.push(currentQuestion)

  return lines.join('\n').trim()
}

module.exports = {
  buildToolRouterEmbeddingQuery,
  TURN_CONTENT_MAX,
  VECTOR_HISTORY_ROUNDS,
}

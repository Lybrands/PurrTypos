/**
 * 会话 Tab 标题生成约定（与主对话模型一致：用用户当前选的 model，见 ipc ai-generate-title）
 * - 非流式
 * - temperature 固定 1
 * - 禁用思考（Anthropic thinking: disabled；OpenAI 路径不传 reasoning/thinking）
 * - 输出规范为最多 SESSION_TITLE_MAX_CHARS 个 Unicode 字符（10 字）
 */

const SESSION_TITLE_MAX_CHARS = 10
const SESSION_TITLE_TEMPERATURE = 1

const SESSION_TITLE_SYSTEM_PROMPT =
  '你是标题生成器。根据下面用户与助手的一轮对话摘录，生成一个简短对话标题。要求：严格不超过10个字（含标点）；只输出标题本身，不要引号、不要序号、不要解释。'

/**
 * 清洗模型输出并截断为最多 SESSION_TITLE_MAX_CHARS 字
 * @param {string} raw
 * @returns {string}
 */
function normalizeSessionTitle(raw) {
  let s = String(raw || '')
    .replace(/\r?\n/g, ' ')
    .trim()
  s = s.replace(/^[\s"'「『【]+|[\s"'」』】]+$/g, '').trim()
  const first = s.split(/[。！？!?][\s]*|[\n\r]/)[0]
  s = (first != null ? first : s).trim()
  if (!s) return ''
  return Array.from(s).slice(0, SESSION_TITLE_MAX_CHARS).join('')
}

module.exports = {
  SESSION_TITLE_MAX_CHARS,
  SESSION_TITLE_TEMPERATURE,
  SESSION_TITLE_SYSTEM_PROMPT,
  normalizeSessionTitle,
}

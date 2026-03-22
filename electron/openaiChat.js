/**
 * OpenAI 兼容 API 适配（可配置 baseURL）。
 * 不包含模型名称或参数，全部由调用方通过 options 传入。
 */

const OpenAI = require('openai')
const {
  SESSION_TITLE_SYSTEM_PROMPT,
  normalizeSessionTitle,
} = require('./sessionTitle')

function createClient(apiKey, baseURL) {
  const url = typeof baseURL === 'string' ? baseURL.trim() : ''
  if (!url) {
    throw new Error('请填写接口地址')
  }
  return new OpenAI({
    apiKey,
    baseURL: url.replace(/\/+$/, ''),
  })
}

// ─── 非流式对话（带 tools 时用，保证拿到完整 tool_calls 含 id）────────────────
/**
 * @returns {Promise<{ message: object, model: string }>}
 */
async function chatNoStream(apiKey, messages, options = {}, signal) {
  const { model, temperature, thinking, tools, max_tokens, baseURL } = options
  const client = createClient(apiKey, baseURL)
  const params = { model, messages, stream: false }
  if (temperature !== undefined && temperature !== null) params.temperature = temperature
  if (thinking) params.thinking = thinking
  if (max_tokens) params.max_tokens = max_tokens
  if (tools && tools.length > 0) params.tools = tools
  const res = await client.chat.completions.create(params, { signal })
  const message = res.choices?.[0]?.message || {}
  return { message, model: res.model || model }
}

// ─── 流式对话 ────────────────────────────────────────────────────
/**
 * @param {string} apiKey
 * @param {Array} messages
 * @param {{ model: string, temperature?: number, thinking?: object, tools?: array, max_tokens?: number }} options
 * @returns {Promise<{ stream: AsyncIterable, model: string }>}
 */
async function chatStream(apiKey, messages, options = {}, signal) {
  const { model, temperature, thinking, tools, max_tokens, baseURL } = options
  const toolNames = tools?.length ? tools.map((t) => t.function?.name).filter(Boolean).join(', ') : ''
  const tempLog =
    temperature !== undefined && temperature !== null ? `temperature=${temperature}` : 'temperature=(omit)'
  console.log(`[OpenAI] ${model} ${tempLog}${thinking ? ` thinking=${thinking.type}` : ''}${toolNames ? ` tools=${toolNames}` : ''}`)

  const client = createClient(apiKey, baseURL)
  const params = { model, messages, stream: true }
  if (temperature !== undefined && temperature !== null) params.temperature = temperature
  if (thinking) params.thinking = thinking
  if (max_tokens) params.max_tokens = max_tokens
  if (tools && tools.length > 0) params.tools = tools
  const stream = await client.chat.completions.create(params, { signal })
  return { stream, model }
}

// ─── 标题生成（非流式 / temperature=1 / 不用思考扩展字段）────────────────
/**
 * @param {string} apiKey
 * @param {string} text
 * @param {{ model: string, baseURL?: string }} options
 * @returns {Promise<string>} 已 normalize，最多 10 字
 */
async function generateTitle(apiKey, text, options = {}) {
  const { model, baseURL } = options
  const client = createClient(apiKey, baseURL)
  const res = await client.chat.completions.create({
    model,
    messages: [
      { role: 'system', content: SESSION_TITLE_SYSTEM_PROMPT },
      { role: 'user', content: String(text || '').trim() },
    ],
    max_tokens: 32,
    thinking: { type: 'disabled' },
    stream: false,
  })
  const raw = res.choices?.[0]?.message?.content ?? ''
  console.log('[ai-generate-title][openai] 模型返回原文:', String(raw))
  return normalizeSessionTitle(raw)
}

module.exports = { chatStream, chatNoStream, generateTitle }

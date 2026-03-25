/**
 * Anthropic Messages API 适配：将应用内 OpenAI 形态的消息/流式 chunk
 * 与 Anthropic SDK 互转，供 main.js 统一走现有 ai-chat-chunk 管线。
 */

const Anthropic = require('@anthropic-ai/sdk')
const {
  SESSION_TITLE_TEMPERATURE,
  SESSION_TITLE_SYSTEM_PROMPT,
  normalizeSessionTitle,
} = require('./sessionTitle')

function createClient(apiKey, baseURL) {
  const url = typeof baseURL === 'string' ? baseURL.trim() : ''
  return new Anthropic({
    apiKey: (apiKey || '').trim(),
    baseURL: url ? url.replace(/\/+$/, '') : undefined,
  })
}

/** OpenAI function tools → Anthropic Tool */
function openAiToolsToAnthropic(tools) {
  if (!Array.isArray(tools) || tools.length === 0) return undefined
  return tools.map((t) => {
    const fn = t.function || {}
    const raw = fn.parameters && typeof fn.parameters === 'object' ? fn.parameters : {}
    const input_schema =
      raw.type === 'object'
        ? { type: 'object', ...raw }
        : { type: 'object', properties: {}, required: [], ...raw }
    if (!input_schema.type) input_schema.type = 'object'
    return {
      name: String(fn.name || '').trim(),
      description: fn.description || undefined,
      input_schema,
    }
  })
}

function safeJsonParseArgs(s) {
  const str = typeof s === 'string' ? s : ''
  if (!str.trim()) return {}
  try {
    return JSON.parse(str)
  } catch {
    return {}
  }
}

/**
 * 将对话历史转为 Anthropic messages + system（无 system role）。
 */
function openAiMessagesToAnthropic(messages) {
  const systemParts = []
  const out = []
  let i = 0
  const list = Array.isArray(messages) ? messages : []

  while (i < list.length) {
    const m = list[i]
    if (!m || typeof m !== 'object') {
      i += 1
      continue
    }
    const role = m.role

    if (role === 'system' || role === 'developer') {
      const c = String(m.content ?? '').trim()
      if (c) systemParts.push(c)
      i += 1
      continue
    }

    if (role === 'user') {
      const c = String(m.content ?? '')
      if (c) out.push({ role: 'user', content: c })
      i += 1
      continue
    }

    if (role === 'assistant') {
      const blocks = []
      const think = m.reasoning_content != null ? String(m.reasoning_content).trim() : ''
      const text = String(m.content ?? '')
      if (think) {
        blocks.push({ type: 'text', text: `（先前思考过程）\n${think}\n\n` })
      }
      if (text) blocks.push({ type: 'text', text })
      if (Array.isArray(m.tool_calls)) {
        for (const tc of m.tool_calls) {
          const id = String(tc?.id || '').trim()
          const name = String(tc?.function?.name || '').trim()
          const input = safeJsonParseArgs(tc?.function?.arguments)
          if (id && name) blocks.push({ type: 'tool_use', id, name, input })
        }
      }
      if (blocks.length) out.push({ role: 'assistant', content: blocks })
      i += 1
      continue
    }

    if (role === 'tool') {
      const toolResults = []
      while (i < list.length && list[i] && list[i].role === 'tool') {
        const tm = list[i]
        toolResults.push({
          type: 'tool_result',
          tool_use_id: String(tm.tool_call_id || '').trim(),
          content: String(tm.content ?? ''),
        })
        i += 1
      }
      if (toolResults.length) out.push({ role: 'user', content: toolResults })
      continue
    }

    i += 1
  }

  const system = systemParts.length ? systemParts.join('\n\n') : undefined
  return { system, messages: out }
}

function openAiChunk(delta, finishReason) {
  return {
    choices: [
      {
        index: 0,
        delta: delta || {},
        ...(finishReason != null ? { finish_reason: finishReason } : {}),
      },
    ],
  }
}

/**
 * @param {string} apiKey
 * @param {Array} openAiMessages
 * @param {{ model: string, temperature?: number, thinking?: { type: string }, tools?: array, max_tokens?: number, baseURL?: string }} options
 * @param {AbortSignal} [signal]
 * @returns {Promise<{ stream: AsyncIterable<unknown>, model: string }>}
 */
async function chatStreamAsOpenAIFormat(apiKey, openAiMessages, options = {}, signal) {
  const { model, temperature, thinking, tools, max_tokens, baseURL } = options
  const client = createClient(apiKey, baseURL)
  const { system, messages } = openAiMessagesToAnthropic(openAiMessages)
  if (!messages.length) {
    throw new Error('消息为空')
  }

  let maxOut = typeof max_tokens === 'number' && max_tokens > 0 ? max_tokens : 8192
  const thinkingOn = thinking && thinking.type === 'enabled'
  let thinkingParam
  if (thinkingOn) {
    let budget = Math.min(32000, Math.max(1024, Math.floor(maxOut / 2)))
    if (budget >= maxOut) maxOut = budget + 2048
    thinkingParam = { type: 'enabled', budget_tokens: budget }
  }

  const anthropicTools = openAiToolsToAnthropic(tools)
  const params = {
    model: String(model || '').trim(),
    max_tokens: maxOut,
    messages,
    stream: true,
    ...(system ? { system } : {}),
    ...(thinkingParam ? { thinking: thinkingParam } : {}),
    ...(anthropicTools && anthropicTools.length ? { tools: anthropicTools } : {}),
  }
  if (temperature !== undefined && temperature !== null) {
    params.temperature = temperature
  }

  const rawStream = await client.messages.create(params, { signal })

  async function* convert() {
    const toolAccum = new Map()
    let lastStopReason = null

    try {
      for await (const ev of rawStream) {
        if (signal?.aborted) break

        if (ev.type === 'content_block_delta') {
          const d = ev.delta
          if (d.type === 'text_delta' && d.text) {
            yield openAiChunk({ content: d.text })
          } else if (thinkingOn && d.type === 'thinking_delta' && d.thinking) {
            yield openAiChunk({ reasoning_content: d.thinking })
          } else if (d.type === 'input_json_delta' && d.partial_json != null) {
            const idx = ev.index
            const cur = toolAccum.get(idx) || { json: '' }
            cur.json += d.partial_json
            toolAccum.set(idx, cur)
          }
        } else if (ev.type === 'content_block_start') {
          const block = ev.content_block
          if (block && block.type === 'tool_use') {
            toolAccum.set(ev.index, {
              id: block.id,
              name: block.name,
              json: '',
            })
          }
        } else if (ev.type === 'content_block_stop') {
          const idx = ev.index
          const t = toolAccum.get(idx)
          if (t && t.name) {
            const argsStr = t.json && t.json.trim() ? t.json : '{}'
            yield openAiChunk({
              tool_calls: [
                {
                  index: idx,
                  id: String(t.id || ''),
                  type: 'function',
                  function: { name: String(t.name || ''), arguments: argsStr },
                },
              ],
            })
          }
        } else if (ev.type === 'message_delta') {
          const sr = ev.delta?.stop_reason
          if (sr) lastStopReason = sr
        }
      }
    } catch (e) {
      if (signal?.aborted) {
        yield openAiChunk({}, 'stop')
        return
      }
      throw e
    }

    if (lastStopReason === 'tool_use') {
      yield openAiChunk({}, 'tool_calls')
    } else if (lastStopReason === 'max_tokens') {
      yield openAiChunk({}, 'length')
    } else {
      yield openAiChunk({}, 'stop')
    }
  }

  return { stream: convert(), model }
}

/**
 * 非流式对话，返回与 OpenAI chat.completions 兼容的 message 形状（供 subagent 等多轮 tool 循环复用）。
 * @returns {Promise<{ message: object, model: string }>}
 */
async function chatNoStreamAsOpenAIFormat(apiKey, openAiMessages, options = {}, signal) {
  const { model, temperature, thinking, tools, max_tokens, baseURL } = options
  const client = createClient(apiKey, baseURL)
  const { system, messages } = openAiMessagesToAnthropic(openAiMessages)
  if (!messages.length) {
    throw new Error('消息为空')
  }

  let maxOut = typeof max_tokens === 'number' && max_tokens > 0 ? max_tokens : 8192
  const thinkingOn = thinking && thinking.type === 'enabled'
  let thinkingParam
  if (thinkingOn) {
    let budget = Math.min(32000, Math.max(1024, Math.floor(maxOut / 2)))
    if (budget >= maxOut) maxOut = budget + 2048
    thinkingParam = { type: 'enabled', budget_tokens: budget }
  }

  const anthropicTools = openAiToolsToAnthropic(tools)
  const params = {
    model: String(model || '').trim(),
    max_tokens: maxOut,
    messages,
    stream: false,
    ...(system ? { system } : {}),
    ...(thinkingParam ? { thinking: thinkingParam } : {}),
    ...(anthropicTools && anthropicTools.length ? { tools: anthropicTools } : {}),
  }
  if (temperature !== undefined && temperature !== null) {
    params.temperature = temperature
  }

  let msg
  try {
    msg = await client.messages.create(params, { signal })
  } catch (err) {
    const msgErr = (err && err.message) || String(err)
    if (/thinking|not support|unrecogniz|invalid/i.test(msgErr) && params.thinking) {
      const { thinking: _t, ...rest } = params
      msg = await client.messages.create(rest, { signal })
    } else {
      throw err
    }
  }

  const blocks = Array.isArray(msg.content) ? msg.content : []
  const textParts = []
  const thinkingParts = []
  const toolCallsOpenAi = []
  for (const block of blocks) {
    if (!block || typeof block !== 'object') continue
    if (block.type === 'text' && typeof block.text === 'string') {
      textParts.push(block.text)
    } else if (block.type === 'thinking' && typeof block.thinking === 'string') {
      thinkingParts.push(block.thinking)
    } else if (block.type === 'tool_use') {
      const input = block.input && typeof block.input === 'object' ? block.input : {}
      toolCallsOpenAi.push({
        id: String(block.id || ''),
        type: 'function',
        function: {
          name: String(block.name || ''),
          arguments: JSON.stringify(input),
        },
      })
    }
  }

  return {
    message: {
      role: 'assistant',
      content: textParts.join(''),
      ...(thinkingParts.length ? { reasoning_content: thinkingParts.join('\n') } : {}),
      ...(toolCallsOpenAi.length ? { tool_calls: toolCallsOpenAi } : {}),
    },
    model: msg.model || model,
  }
}

/** 从 Messages 响应取出可见文本（兼容仅 text / 部分网关只给 thinking 或非标准块） */
function extractAnthropicTitlePlainText(msg) {
  if (!msg || typeof msg !== 'object') return ''
  if (typeof msg.content === 'string') {
    return msg.content.replace(/[\r\n]+/g, ' ').trim()
  }
  const blocks = Array.isArray(msg.content) ? msg.content : []
  const fromTextBlocks = blocks
    .map((b) => {
      if (!b || typeof b !== 'object') return ''
      if (b.type === 'text' && typeof b.text === 'string') return b.text
      return ''
    })
    .join('')
    .replace(/[\r\n]+/g, ' ')
    .trim()
  if (fromTextBlocks) return fromTextBlocks
  const fallback = blocks
    .map((b) => {
      if (!b || typeof b !== 'object') return ''
      if (b.type === 'thinking' && typeof b.thinking === 'string') return b.thinking
      if (typeof b.text === 'string') return b.text
      if (typeof b.content === 'string') return b.content
      return ''
    })
    .join('')
    .replace(/[\r\n]+/g, ' ')
    .trim()
  return fallback
}

/**
 * 非流式标题：temperature=1、禁止思考、最多 10 字
 */
async function generateTitle(apiKey, text, options = {}) {
  const { model, baseURL } = options
  const client = createClient(apiKey, baseURL)
  const userText = String(text || '').trim().slice(0, 4000)
  const payload = {
    model,
    max_tokens: 512,
    thinking: { type: 'disabled' },
    system: SESSION_TITLE_SYSTEM_PROMPT,
    messages: [{ role: 'user', content: userText }],
  }
  let msg
  try {
    msg = await client.messages.create(payload)
  } catch (err) {
    const msgErr = (err && err.message) || String(err)
    if (/thinking|not support|unrecogniz|invalid/i.test(msgErr) && payload.thinking) {
      const { thinking: _t, ...rest } = payload
      msg = await client.messages.create(rest)
    } else {
      throw err
    }
  }
  const raw = extractAnthropicTitlePlainText(msg)
  console.log('[ai-generate-title][anthropic] 模型返回原文:', raw)
  if (!raw) {
    const blockTypes = Array.isArray(msg?.content)
      ? msg.content.map((b) => (b && typeof b === 'object' ? b.type : typeof b))
      : typeof msg?.content
    console.warn('[ai-generate-title][anthropic] 无可见正文（非抛错）：', {
      stop_reason: msg?.stop_reason,
      blockTypes,
      model,
    })
  }
  return normalizeSessionTitle(raw)
}

module.exports = {
  chatStreamAsOpenAIFormat,
  chatNoStreamAsOpenAIFormat,
  generateTitle,
  openAiMessagesToAnthropic,
}

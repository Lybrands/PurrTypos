/**
 * 工具路由后端：主模型 tools 仅来自 agentToolDefinitions.js（合并策略 A）。
 * SKILL.md（gray-matter）只增强路由：embedding 与 Ollama 意图 prompt，不参与 buildToolSchemas。
 * 对外：setSkillsPath(path)、ensureSkillsLoaded()、getToolsForQuery(query)。
 */

const fs = require('fs')
const path = require('path')
const matter = require('gray-matter')
const { ROUTER_SKILL_ITEMS } = require('./agentToolDefinitions')

const ROUTER_TOP_K = 10
const ROUTER_BODY_PREVIEW_MAX = 400
const ollamaUrl = (process.env.OLLAMA_HOST || 'http://localhost:11434').replace(/\/$/, '')
const intentModel = process.env.TOOL_ROUTER_INTENT_MODEL || 'qwen2.5:3b'
const useIntent = process.env.TOOL_ROUTER_USE_LLM_INTENT !== '0'

/** 与 getEmbedder 一致，用于用户可见的失败提示 */
function embedServiceLabel() {
  const hasOllama = process.env.MEM0_USE_OLLAMA !== '0'
  const ollamaModel = process.env.MEM0_EMBED_MODEL || 'nomic-embed-text'
  if (hasOllama) return `Ollama ${ollamaModel}`
  if (process.env.OPENAI_API_KEY) {
    const m = process.env.MEM0_EMBED_MODEL || 'text-embedding-3-small'
    return `OpenAI ${m}`
  }
  return '嵌入服务'
}

/** 日志用：截断长文本 */
function previewStr(s, max = 200) {
  const t = String(s || '').replace(/\s+/g, ' ').trim()
  if (!t) return '（空）'
  return t.length <= max ? t : `${t.slice(0, max)}…`
}

let cachedEmbedder = null
/** 主模型 API 用技能列表（纯 JS，与 agentToolDefinitions 一致） */
let cachedSkills = []
/** 工具名 -> 路由用语义片段（来自 SKILL.md 或回退 JS description），不用于 API tools */
let routerHints = new Map()
/** 技能根目录（main 启动时设置） */
let skillsDirPath = ''
/** 工具名 -> 向量，懒计算并缓存 */
const toolVectorsCache = new Map()

/**
 * 设置技能目录路径（由 main 在启动时调用，兼容保留）。
 */
function setSkillsPath(dirPath) {
  skillsDirPath = dirPath || ''
}

function plainBodyPreview(md, maxLen) {
  if (!md || !String(md).trim()) return ''
  const t = String(md).replace(/\s+/g, ' ').trim()
  if (t.length <= maxLen) return t
  return t.slice(0, maxLen) + '…'
}

/**
 * 从 <skillsDir>/<toolName>/SKILL.md 解析路由用语义（策略 A：不用于主模型 tools）。
 * @param {string} toolName
 * @param {{ description?: string }} jsItem
 */
function loadSkillMdRouterHint(toolName, jsItem) {
  const fallback = (jsItem && jsItem.description) || ''
  if (!skillsDirPath || !toolName) return fallback
  const mdPath = path.join(skillsDirPath, toolName, 'SKILL.md')
  if (!fs.existsSync(mdPath)) return fallback
  try {
    const raw = fs.readFileSync(mdPath, 'utf8')
    const { data, content } = matter(raw)
    const fromYaml =
      data && (data.description != null || data.short_description != null)
        ? String(data.description || data.short_description || '').trim()
        : ''
    const preview = plainBodyPreview(content, ROUTER_BODY_PREVIEW_MAX)
    if (fromYaml && preview) return `${fromYaml} ${preview}`
    if (fromYaml) return fromYaml
    if (preview) return preview
  } catch (err) {
    console.warn(`[toolRouter] SKILL.md skipped for ${toolName}:`, err.message)
  }
  return fallback
}

/**
 * 从 agentToolDefinitions 载入 API 技能表，并填充 routerHints（仅首次）。
 */
function loadSkillsFromDisk() {
  if (cachedSkills.length > 0) return
  try {
    cachedSkills = ROUTER_SKILL_ITEMS.map((s) => ({ ...s }))
    routerHints = new Map()
    for (const s of cachedSkills) {
      routerHints.set(s.name, loadSkillMdRouterHint(s.name, s))
    }
    toolVectorsCache.clear()
  } catch (err) {
    console.error('[toolRouter] loadSkillsFromDisk failed', err.message)
  }
}

function ensureSkillsLoaded() {
  loadSkillsFromDisk()
}

function getEmbedder() {
  if (cachedEmbedder) return cachedEmbedder
  const hasOllama = process.env.MEM0_USE_OLLAMA !== '0'
  const hasOpenAI = !!process.env.OPENAI_API_KEY
  const url = (process.env.OLLAMA_HOST || 'http://localhost:11434').replace(/\/$/, '')
  const ollamaModel = process.env.MEM0_EMBED_MODEL || 'nomic-embed-text'

  if (hasOllama) {
    cachedEmbedder = {
      embed: async (text) => {
        const res = await fetch(`${url}/api/embeddings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: ollamaModel, prompt: text }),
        })
        if (!res.ok) {
          const err = await res.text()
          throw new Error(`Ollama embed: ${res.status} ${err}`)
        }
        const data = await res.json()
        return data.embedding || []
      },
    }
    return cachedEmbedder
  }

  if (hasOpenAI) {
    const baseURL = process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1'
    const apiKey = process.env.OPENAI_API_KEY
    const model = process.env.MEM0_EMBED_MODEL || 'text-embedding-3-small'
    cachedEmbedder = {
      embed: async (text) => {
        const res = await fetch(`${baseURL.replace(/\/$/, '')}/embeddings`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${apiKey}`,
          },
          body: JSON.stringify({ model, input: text }),
        })
        if (!res.ok) {
          const err = await res.text()
          throw new Error(`OpenAI embed: ${res.status} ${err}`)
        }
        const data = await res.json()
        const emb = data.data && data.data[0] && data.data[0].embedding
        return emb || []
      },
    }
    return cachedEmbedder
  }

  throw new Error(
    '工具路由需要 Embedder：请安装并启动 Ollama（推荐），或设置 OPENAI_API_KEY。'
  )
}

/**
 * 对单条文本做向量化。
 * @param {string} text
 * @returns {Promise<number[]>}
 */
async function embed(text) {
  const t = (text || '').trim()
  if (!t) return []
  const embedder = getEmbedder()
  return embedder.embed(t)
}

/**
 * 意图识别：在候选工具名中筛出与 query 相关的最终列表。
 * @param {string} query
 * @param {string[]} candidateNames
 * @returns {Promise<{ success: boolean, toolNames?: string[], warnings: string[] }>}
 */
async function llmIntentForTools(query, candidateNames) {
  const warnings = []
  if (!candidateNames || candidateNames.length === 0) {
    console.log('[toolRouter][步骤2-意图识别] 候选为空，跳过')
    return { success: true, toolNames: [], warnings }
  }
  if (!useIntent) {
    console.log(
      '[toolRouter][步骤2-意图识别] 已禁用（TOOL_ROUTER_USE_LLM_INTENT=0）；不调用 Ollama，输出=步骤1 向量 Top-K 候选全集',
    )
    console.log('[toolRouter][步骤2-意图识别] 输出工具名:', candidateNames.join(', '))
    return { success: true, toolNames: candidateNames, warnings }
  }

  const maxIntentTools = Math.min(5, candidateNames.length)
  const byName = new Map(cachedSkills.map((s) => [s.name, s]))
  const list = candidateNames
    .map((name) => {
      const hint = (routerHints.get(name) || '').trim()
      const js = byName.get(name)
      const desc = hint || (js && js.description) || ''
      return desc ? `- ${name}: ${desc}` : `- ${name}`
    })
    .join('\n')
  const prompt = `你是一个写作助手的工具选择器。用户说：「${query}」

以下是候选工具及简要说明（仅返回与本轮用户意图最相关的 1～5 个工具名，用英文逗号分隔，不要解释、不要返回说明文字）：
${list}

只返回工具名，多个用英文逗号分隔。例如只返回 1～2 个：getBookContext,searchMemories`

  console.log('[toolRouter][步骤2-意图识别] query 摘要:', previewStr(query))
  console.log('[toolRouter][步骤2-意图识别] 输入候选（来自步骤1 向量 Top-K）:', candidateNames.join(', '))
  console.log('[toolRouter][步骤2-意图识别] Ollama:', `${ollamaUrl}, model=${intentModel}`)

  try {
    const res = await fetch(`${ollamaUrl}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: intentModel,
        prompt,
        stream: false,
        options: { num_predict: 128 },
      }),
    })
    if (!res.ok) {
      const errText = await res.text().catch(() => '')
      console.warn(
        '[toolRouter][步骤2-意图识别] Ollama HTTP 失败:',
        res.status,
        errText ? previewStr(errText, 120) : '',
      )
      const fallback = candidateNames.slice(0, maxIntentTools)
      console.log('[toolRouter][步骤2-意图识别] 回退为候选前', maxIntentTools, '个:', fallback.join(', '))
      warnings.push(`Ollama ${intentModel} 模型调用失败，HTTP ${res.status}`)
      return { success: true, toolNames: fallback, warnings }
    }
    const data = await res.json()
    const text = (data.response || '').trim()
    console.log('[toolRouter][步骤2-意图识别] Ollama 原始输出:', previewStr(text, 300))
    const valid = new Set(candidateNames)
    let filtered = text
      .split(/[,，\s\n]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .filter((n) => valid.has(n))
    if (filtered.length === 0) {
      const lower = text.toLowerCase()
      filtered = candidateNames.filter((name) => lower.includes(name.toLowerCase()))
    }
    if (filtered.length === 0) {
      filtered = candidateNames.slice(0, Math.min(2, candidateNames.length))
      console.log('[toolRouter][步骤2-意图识别] 解析不到合法工具名，回退为候选前 2 个:', filtered.join(', '))
      warnings.push(`Ollama ${intentModel} 模型调用失败，输出无法解析为工具名`)
    }
    const capped = filtered.slice(0, maxIntentTools)
    console.log('[toolRouter][步骤2-意图识别] 解析后工具名（最多', maxIntentTools, '个）:', capped.join(', '))
    return { success: true, toolNames: capped, warnings }
  } catch (err) {
    console.error('[toolRouter][步骤2-意图识别] 请求异常:', err.message)
    const fallback = candidateNames.slice(0, maxIntentTools)
    console.log('[toolRouter][步骤2-意图识别] 异常回退:', fallback.join(', '))
    warnings.push(`Ollama ${intentModel} 模型调用失败，${(err && err.message) || String(err)}`)
    return { success: true, toolNames: fallback, warnings }
  }
}

function cosineSimilarity(a, b) {
  if (!a?.length || !b?.length) return 0
  let dot = 0
  let na = 0
  let nb = 0
  const n = Math.min(a.length, b.length)
  for (let i = 0; i < n; i++) {
    dot += a[i] * b[i]
    na += a[i] * a[i]
    nb += b[i] * b[i]
  }
  const norm = Math.sqrt(na) * Math.sqrt(nb) || 1
  return dot / norm
}

/**
 * 从技能列表里按名字取出并拼成 OpenAI 兼容 tools 数组。
 * @param {Array<{ name: string, description: string, parameters: object }>} skillItems
 * @param {string[]} names
 * @returns {Array<{ type: 'function', function: { name, description, parameters } }>}
 */
function buildToolSchemas(skillItems, names) {
  const byName = new Map(skillItems.map((s) => [s.name, s]))
  const out = []
  for (const name of names) {
    const s = byName.get(name)
    if (!s) continue
    out.push({
      type: 'function',
      function: {
        name: s.name,
        description: s.description || '',
        parameters: s.parameters || { type: 'object', properties: {}, required: [] },
      },
    })
  }
  return out
}

/**
 * 根据用户输入解析出本轮应下发的 tools（向量 top-K + 意图筛选）。
 * @param {string} query
 * @returns {Promise<{ tools: Array<{ type: string, function: object }>, warnings: string[] }>}
 */
async function getToolsForQuery(query) {
  const warnings = []
  ensureSkillsLoaded()
  if (!cachedSkills.length) {
    console.log('[toolRouter][步骤3-最终下发] 无已加载技能，tools=[]')
    return { tools: [], warnings }
  }
  const text = (query || '').trim()
  if (!text) {
    const names = cachedSkills.slice(0, ROUTER_TOP_K).map((s) => s.name)
    console.log('[toolRouter][步骤1-向量匹配] query 为空，跳过向量与意图')
    console.log('[toolRouter][步骤3-最终下发] 默认取技能列表前', ROUTER_TOP_K, '个:', names.join(', '))
    return { tools: buildToolSchemas(cachedSkills, names), warnings }
  }

  try {
    console.log('[toolRouter][步骤1-向量匹配] 检索用文本（向量模型输入，全文）:\n', text || '（空）')
    console.log('[toolRouter][步骤1-向量匹配] query 摘要:', previewStr(text))

    const toolTexts = cachedSkills.map((s) => ({
      name: s.name,
      text: `${s.name}: ${(routerHints.get(s.name) || s.description || '').trim() || s.name}`,
    }))
    const vectors = await Promise.all(
      toolTexts.map(async (t) => {
        let v = toolVectorsCache.get(t.name)
        if (v) return v
        v = await embed(t.text)
        if (v.length) toolVectorsCache.set(t.name, v)
        return v
      })
    )
    const queryVec = await embed(text)
    if (!queryVec.length) {
      const fallback = cachedSkills.slice(0, ROUTER_TOP_K).map((s) => s.name)
      console.warn('[toolRouter][步骤1-向量匹配] query 向量为空，无法算相似度，跳过排序')
      console.log('[toolRouter][步骤2-意图识别] 未执行（无有效 query 向量，直接进入默认列表）')
      console.log('[toolRouter][步骤3-最终下发] 工具名:', fallback.join(', '))
      warnings.push(`${embedServiceLabel()} 模型调用失败，未返回向量`)
      return { tools: buildToolSchemas(cachedSkills, fallback), warnings }
    }
    const withScore = cachedSkills.map((s, i) => ({
      name: s.name,
      score: cosineSimilarity(queryVec, vectors[i] || []),
    }))
    withScore.sort((a, b) => b.score - a.score)
    const rankedLines = withScore
      .slice(0, ROUTER_TOP_K)
      .map((t, idx) => `  ${idx + 1}. ${t.name}  score=${t.score.toFixed(4)}`)
    console.log('[toolRouter][步骤1-向量匹配] 与 query 余弦相似度 Top', ROUTER_TOP_K, '(降序):\n' + rankedLines.join('\n'))
    const top10 = withScore.slice(0, ROUTER_TOP_K).map((t) => t.name)
    console.log('[toolRouter][步骤1-向量匹配] 送入步骤2 的候选工具名:', top10.join(', '))

    const intentRes = await llmIntentForTools(text, top10)
    if (Array.isArray(intentRes.warnings) && intentRes.warnings.length) {
      warnings.push(...intentRes.warnings)
    }
    const finalNames =
      intentRes.toolNames && intentRes.toolNames.length > 0 ? intentRes.toolNames : top10
    if (!(intentRes.toolNames && intentRes.toolNames.length > 0)) {
      console.log('[toolRouter][步骤3-最终下发] 意图步骤无有效输出，回退为步骤1 的 Top-K 顺序列表')
    }
    console.log('[toolRouter][步骤3-最终下发] 本轮发给主模型的工具（共', finalNames.length, '个）:', finalNames.join(', '))
    return { tools: buildToolSchemas(cachedSkills, finalNames), warnings }
  } catch (err) {
    console.error('[toolRouter][步骤1/2] getToolsForQuery 异常:', err.message)
    const fallback = cachedSkills.slice(0, Math.min(2, cachedSkills.length)).map((s) => s.name)
    console.log('[toolRouter][步骤3-最终下发] 异常回退（前 2 个技能）:', fallback.join(', '))
    warnings.push(`${embedServiceLabel()} 模型调用失败，${(err && err.message) || String(err)}`)
    return { tools: buildToolSchemas(cachedSkills, fallback), warnings }
  }
}

module.exports = { setSkillsPath, loadSkillsFromDisk, ensureSkillsLoaded, getToolsForQuery, embed, llmIntentForTools }

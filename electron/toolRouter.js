/**
 * 工具路由与「发给大模型的 tools」真源：
 * - **仅**扫描 `electron/skills/<toolName>/SKILL.md`（目录名 = 工具名 = toolExecutor 分支名）。
 * - frontmatter：仅 `name`、`description`（及可选 `short_description`）；**parameters** 须在正文首个 **\`\`\`json** 代码块（OpenAI JSON Schema）。
 * 对外：setSkillsPath(path)、ensureSkillsLoaded()、getApiSkillItems()、getToolsForQuery(query)。
 */

const fs = require('fs')
const path = require('path')
const matter = require('gray-matter')

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
/** 主模型 API 用技能列表（由各子目录 SKILL.md 解析） */
let cachedSkills = []
/** 工具名 -> 向量检索用语义片段 */
let routerHints = new Map()
/** 技能根目录（main 启动时设置） */
let skillsDirPath = ''
/** 工具名 -> 向量，懒计算并缓存 */
const toolVectorsCache = new Map()

const GLOBAL_OUTLINE_READ_TOOL = 'getGlobalOutline'
const GLOBAL_OUTLINE_EDIT_TOOL = 'editGlobalOutline'
const OUTLINE_LIST_TOOL = 'listOutlines'
const OUTLINE_QUERY_TOOL = 'queryOutline'
const OUTLINE_UPDATE_TOOL = 'updateOutline'

/**
 * 设置技能目录路径（由 main 在启动时调用）。变更时清空缓存以便重新扫描。
 */
function setSkillsPath(dirPath) {
  skillsDirPath = dirPath || ''
  cachedSkills = []
  routerHints = new Map()
  toolVectorsCache.clear()
}

function plainBodyPreview(md, maxLen) {
  if (!md || !String(md).trim()) return ''
  const t = String(md).replace(/\s+/g, ' ').trim()
  if (t.length <= maxLen) return t
  return t.slice(0, maxLen) + '…'
}

function isLikelyParametersSchema(obj) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false
  if (obj.type === 'object' && obj.properties && typeof obj.properties === 'object') return true
  if (obj.properties && typeof obj.properties === 'object') return true
  return false
}

/** 正文里首个可解析为 JSON Schema 的 ```json 代码块 → parameters */
function extractParametersFromBody(content) {
  const src = String(content || '')
  const re = /```(?:json)?\s*([\s\S]*?)```/gi
  let m
  while ((m = re.exec(src)) !== null) {
    const raw = m[1].trim()
    if (!raw) continue
    try {
      const obj = JSON.parse(raw)
      if (isLikelyParametersSchema(obj)) {
        const params = JSON.parse(JSON.stringify(obj))
        if (!params.type) params.type = 'object'
        return params
      }
    } catch (_) {
      /* 尝试下一块 */
    }
  }
  return null
}

/** 向量检索用正文摘要：去掉 JSON 代码块，避免与 description 重复堆叠 */
function bodyTextForEmbedding(description, content) {
  const stripped = String(content || '').replace(/```(?:json)?\s*[\s\S]*?```/gi, ' ')
  const preview = plainBodyPreview(stripped, ROUTER_BODY_PREVIEW_MAX)
  if (!preview) return String(description || '').trim()
  return `${String(description || '').trim()} ${preview}`.trim()
}

const FRONTMATTER_ALLOWED = new Set(['name', 'description', 'short_description'])

function discoverSkillToolNames() {
  if (!skillsDirPath || !fs.existsSync(skillsDirPath)) return []
  const out = []
  for (const ent of fs.readdirSync(skillsDirPath, { withFileTypes: true })) {
    if (!ent.isDirectory()) continue
    const n = ent.name
    if (n.startsWith('.')) continue
    const md = path.join(skillsDirPath, n, 'SKILL.md')
    if (fs.existsSync(md)) out.push(n)
  }
  return out.sort()
}

/**
 * 仅从 SKILL.md 读取。工具名以**目录名**为准（须与 toolExecutor 中 switch 分支一致）。
 * @param {string} canonicalName
 */
function loadSkillMarkdownOnly(canonicalName) {
  const mdPath = path.join(skillsDirPath, canonicalName, 'SKILL.md')
  const raw = fs.readFileSync(mdPath, 'utf8')
  const { data, content } = matter(raw)
  if (data && typeof data === 'object') {
    for (const k of Object.keys(data)) {
      if (!FRONTMATTER_ALLOWED.has(k)) {
        console.warn(
          `[toolRouter] ${canonicalName}/SKILL.md frontmatter 字段「${k}」已忽略（约定仅 name、description；参数请写在正文 \`\`\`json 代码块）`,
        )
      }
    }
  }
  if (data?.name != null && String(data.name).trim() && String(data.name).trim() !== canonicalName) {
    console.warn(
      `[toolRouter] ${canonicalName}/SKILL.md frontmatter name 与目录名不一致，已以目录名「${canonicalName}」为准`,
    )
  }
  const descYaml =
    data && (data.description != null || data.short_description != null)
      ? String(data.description || data.short_description || '').trim()
      : ''
  if (!descYaml) {
    throw new Error('frontmatter 缺少 description / short_description')
  }
  const params = extractParametersFromBody(content)
  if (!params) {
    throw new Error('正文缺少合法的 ```json 代码块（须为 OpenAI 兼容的 parameters JSON Schema）')
  }
  const skill = {
    name: canonicalName,
    description: descYaml,
    parameters: params,
  }
  const routerEmbedText = bodyTextForEmbedding(descYaml, content)
  return { skill, routerEmbedText: String(routerEmbedText).trim() }
}

/**
 * 扫描 skills 目录，仅载入存在 SKILL.md 的工具。
 */
function loadSkillsFromDisk() {
  if (cachedSkills.length > 0) return
  try {
    cachedSkills = []
    routerHints = new Map()
    if (!skillsDirPath) {
      console.warn('[toolRouter] skillsDirPath 未设置，工具列表为空（请 main 调用 setSkillsPath）')
      return
    }
    const names = discoverSkillToolNames()
    for (const toolName of names) {
      try {
        const { skill, routerEmbedText } = loadSkillMarkdownOnly(toolName)
        cachedSkills.push(skill)
        routerHints.set(skill.name, routerEmbedText)
      } catch (err) {
        console.warn(`[toolRouter] 跳过 ${toolName}:`, (err && err.message) || String(err))
      }
    }
    toolVectorsCache.clear()
    if (cachedSkills.length === 0) {
      console.warn('[toolRouter] 未加载到任何 SKILL.md，请检查 electron/skills 目录')
    }
  } catch (err) {
    console.error('[toolRouter] loadSkillsFromDisk failed', err.message)
  }
}

/**
 * 与 getToolsForQuery / buildToolSchemas 使用同一份；须已 setSkillsPath。
 */
function getApiSkillItems() {
  ensureSkillsLoaded()
  return cachedSkills.map((s) => ({ ...s }))
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

以下是候选工具及简要说明（从下列候选中选出与本轮用户意图相关的**全部**工具名，用英文逗号分隔；需要几个选几个，不要人为限制数量。不要解释、不要返回说明文字）：
${list}

只返回工具名，多个用英文逗号分隔。例如：queryOutline,listOutlines,searchMemories 或仅一个：getChapterContent`

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
        options: { num_predict: 256 },
      }),
    })
    if (!res.ok) {
      const errText = await res.text().catch(() => '')
      console.warn(
        '[toolRouter][步骤2-意图识别] Ollama HTTP 失败:',
        res.status,
        errText ? previewStr(errText, 120) : '',
      )
      const fallback = [...candidateNames]
      console.log('[toolRouter][步骤2-意图识别] 回退为候选全集:', fallback.join(', '))
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
      filtered = [...candidateNames]
      console.log('[toolRouter][步骤2-意图识别] 解析不到合法工具名，回退为候选全集:', filtered.join(', '))
      warnings.push(`Ollama ${intentModel} 模型调用失败，输出无法解析为工具名`)
    }
    const seen = new Set()
    const deduped = []
    for (const n of filtered) {
      if (!seen.has(n)) {
        seen.add(n)
        deduped.push(n)
      }
    }
    console.log('[toolRouter][步骤2-意图识别] 解析后工具名（共', deduped.length, '个）:', deduped.join(', '))
    return { success: true, toolNames: deduped, warnings }
  } catch (err) {
    console.error('[toolRouter][步骤2-意图识别] 请求异常:', err.message)
    const fallback = [...candidateNames]
    console.log('[toolRouter][步骤2-意图识别] 异常回退（候选全集）:', fallback.join(', '))
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
 * 基于明确关键词的强规则命中（兜底）：
 * - 提到「总纲」且包含编辑语义 -> 强制下发 editGlobalOutline（并附带 getGlobalOutline 便于先读后写）
 * - 提到「总纲」但无明显编辑语义 -> 强制下发 getGlobalOutline
 * @param {string} query
 * @returns {string[]}
 */
function pickForcedToolsByQuery(query) {
  const text = String(query || '').toLowerCase()
  if (!text) return []
  const hasGlobalOutline = /总纲|全局大纲|global\s*outline/.test(text)
  if (!hasGlobalOutline) return []
  const isEditIntent =
    /编辑|修改|更新|重写|改写|写入|保存|覆盖|调整|完善|补充|rewrite|edit|update|save/.test(text)
  if (isEditIntent) return [GLOBAL_OUTLINE_EDIT_TOOL, GLOBAL_OUTLINE_READ_TOOL]
  return [GLOBAL_OUTLINE_READ_TOOL]
}

/**
 * 工具依赖前置规则：
 * - 当包含 queryOutline / updateOutline 时，自动补上 listOutlines，且置于前面
 * @param {string[]} names
 * @returns {string[]}
 */
function applyToolPrerequisites(names) {
  const list = Array.isArray(names) ? names.filter(Boolean) : []
  if (list.length === 0) return list
  const hasQueryOrUpdate = list.includes(OUTLINE_QUERY_TOOL) || list.includes(OUTLINE_UPDATE_TOOL)
  if (!hasQueryOrUpdate) return list
  return Array.from(new Set([OUTLINE_LIST_TOOL, ...list]))
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
 * @returns {Promise<{ tools: Array<{ type: string, function: object }>, warnings: string[], candidates?: Array<{ name: string, score: number }>, intent?: { likelySkills: string[] } }>}
 */
async function getToolsForQuery(query) {
  const warnings = []
  const candidates = []
  const intent = { likelySkills: [] }
  ensureSkillsLoaded()
  if (!cachedSkills.length) {
    console.log('[toolRouter][步骤3-最终下发] 无已加载技能，tools=[]')
    return { tools: [], warnings, candidates, intent }
  }
  const text = (query || '').trim()
  const forcedTools = pickForcedToolsByQuery(text)
  if (forcedTools.length > 0) {
    console.log('[toolRouter][强规则] 命中总纲关键词，强制候选工具:', forcedTools.join(', '))
  }
  if (!text) {
    const names = cachedSkills.slice(0, ROUTER_TOP_K).map((s) => s.name)
    const finalNames = applyToolPrerequisites(Array.from(new Set([...forcedTools, ...names])))
    console.log('[toolRouter][步骤1-向量匹配] query 为空，跳过向量与意图')
    console.log('[toolRouter][步骤3-最终下发] 默认工具（含强规则）:', finalNames.join(', '))
    intent.likelySkills = finalNames
    return { tools: buildToolSchemas(cachedSkills, finalNames), warnings, candidates, intent }
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
      const finalNames = applyToolPrerequisites(Array.from(new Set([...forcedTools, ...fallback])))
      console.warn('[toolRouter][步骤1-向量匹配] query 向量为空，无法算相似度，跳过排序')
      console.log('[toolRouter][步骤2-意图识别] 未执行（无有效 query 向量，直接进入默认列表）')
      console.log('[toolRouter][步骤3-最终下发] 工具名（含强规则）:', finalNames.join(', '))
      warnings.push(`${embedServiceLabel()} 模型调用失败，未返回向量`)
      intent.likelySkills = finalNames
      return { tools: buildToolSchemas(cachedSkills, finalNames), warnings, candidates, intent }
    }
    const withScore = cachedSkills.map((s, i) => ({
      name: s.name,
      score: cosineSimilarity(queryVec, vectors[i] || []),
    }))
    for (const x of withScore.slice(0, ROUTER_TOP_K)) {
      candidates.push({ name: x.name, score: x.score })
    }
    withScore.sort((a, b) => b.score - a.score)
    const rankedLines = withScore
      .slice(0, ROUTER_TOP_K)
      .map((t, idx) => `  ${idx + 1}. ${t.name}  score=${t.score.toFixed(4)}`)
    console.log('[toolRouter][步骤1-向量匹配] 与 query 余弦相似度 Top', ROUTER_TOP_K, '(降序):\n' + rankedLines.join('\n'))
    const top10Raw = withScore.slice(0, ROUTER_TOP_K).map((t) => t.name)
    const top10 = Array.from(new Set([...forcedTools, ...top10Raw]))
    console.log('[toolRouter][步骤1-向量匹配] 送入步骤2 的候选工具名:', top10.join(', '))

    const intentRes = await llmIntentForTools(text, top10)
    if (Array.isArray(intentRes.warnings) && intentRes.warnings.length) {
      warnings.push(...intentRes.warnings)
    }
    const intentNames = intentRes.toolNames && intentRes.toolNames.length > 0 ? intentRes.toolNames : top10
    intent.likelySkills = intentNames
    const finalNames = applyToolPrerequisites(Array.from(new Set([...forcedTools, ...intentNames])))
    if (!(intentRes.toolNames && intentRes.toolNames.length > 0)) {
      console.log('[toolRouter][步骤3-最终下发] 意图步骤无有效输出，回退为步骤1 的 Top-K 顺序列表')
    }
    console.log('[toolRouter][步骤3-最终下发] 本轮发给主模型的工具（共', finalNames.length, '个）:', finalNames.join(', '))
    return { tools: buildToolSchemas(cachedSkills, finalNames), warnings, candidates, intent }
  } catch (err) {
    console.error('[toolRouter][步骤1/2] getToolsForQuery 异常:', err.message)
    const fallback = cachedSkills.slice(0, Math.min(2, cachedSkills.length)).map((s) => s.name)
    const finalNames = applyToolPrerequisites(Array.from(new Set([...forcedTools, ...fallback])))
    console.log('[toolRouter][步骤3-最终下发] 异常回退（含强规则）:', finalNames.join(', '))
    warnings.push(`${embedServiceLabel()} 模型调用失败，${(err && err.message) || String(err)}`)
    intent.likelySkills = finalNames
    return { tools: buildToolSchemas(cachedSkills, finalNames), warnings, candidates, intent }
  }
}

module.exports = {
  setSkillsPath,
  loadSkillsFromDisk,
  ensureSkillsLoaded,
  getApiSkillItems,
  getToolsForQuery,
  embed,
  llmIntentForTools,
}

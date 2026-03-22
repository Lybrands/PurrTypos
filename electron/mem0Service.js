/**
 * mem0 长期记忆服务
 * 使用 mem0ai 开源版 + 本地 MemoryVectorStore（SQLite 持久化）
 * 支持四层记忆 + 伏笔，metadata 适配 mem0 的 userId + metadata 过滤
 */

const path = require('path')
const fs = require('fs')
const { app } = require('electron')

const MEMORY_LAYERS = ['全局', '大纲', '人物', '章节']
const FORESHADOWING_TYPES = ['悬念', '道具', '线索', '对话']

/** 获取 mem0 数据目录（userData/mem0） */
function getMem0DataDir() {
  const dir = path.join(app.getPath('userData'), 'mem0')
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
  return dir
}

/** 单例 Memory 实例 */
let memoryInstance = null
let initError = null

/**
 * 获取 mem0 Memory 实例（懒加载）
 * Embedder: 优先 Ollama（本地），否则 OpenAI（需 OPENAI_API_KEY）
 */
function getMemory() {
  if (memoryInstance) return memoryInstance
  if (initError) throw initError

  try {
    // 先加载 better-sqlite3，确保使用项目内为 Electron 编译的版本
    require('better-sqlite3')
    const { Memory } = require('mem0ai/oss')
    const dataDir = getMem0DataDir()
    const vectorDbPath = path.join(dataDir, 'vector_store.db')
    const historyDbPath = path.join(dataDir, 'history.db')

    // 优先 Ollama（完全本地），否则 OpenAI
    const hasOllama = process.env.MEM0_USE_OLLAMA !== '0'
    const hasOpenAI = !!process.env.OPENAI_API_KEY

    let embedderConfig
    if (hasOllama) {
      embedderConfig = {
        provider: 'ollama',
        config: {
          model: process.env.MEM0_EMBED_MODEL || 'nomic-embed-text',
          url: process.env.OLLAMA_HOST || 'http://localhost:11434',
          embeddingDims: 768,
        },
      }
    } else if (hasOpenAI) {
      embedderConfig = {
        provider: 'openai',
        config: {
          apiKey: process.env.OPENAI_API_KEY,
          model: process.env.MEM0_EMBED_MODEL || 'text-embedding-3-small',
          baseURL: process.env.OPENAI_BASE_URL,
          embeddingDims: 1536,
        },
      }
    } else {
      throw new Error(
        'mem0 需要 Embedder：请安装并启动 Ollama（推荐），或设置 OPENAI_API_KEY。' +
        '若已安装 Ollama，可设置 MEM0_USE_OLLAMA=0 禁用并改用 OpenAI。'
      )
    }

    const llmConfig = hasOpenAI
      ? { provider: 'openai', config: { apiKey: process.env.OPENAI_API_KEY, model: 'gpt-4.1-nano-2025-04-14', baseURL: process.env.OPENAI_BASE_URL } }
      : { provider: 'ollama', config: { model: process.env.MEM0_LLM_MODEL || 'llama3.2', url: process.env.OLLAMA_HOST || 'http://localhost:11434' } }

    const config = {
      version: 'v1.1',
      embedder: embedderConfig,
      vectorStore: {
        provider: 'memory',
        config: {
          collectionName: 'purrtypos_memories',
          dimension: embedderConfig.config.embeddingDims || 768,
          dbPath: vectorDbPath,
        },
      },
      llm: llmConfig,
      historyDbPath,
      disableHistory: false,
    }

    memoryInstance = new Memory(config)
    return memoryInstance
  } catch (err) {
    initError = err
    throw err
  }
}

/** 按书隔离的 userId */
function userIdForBook(bookId) {
  return `book_${bookId}`
}

/**
 * 添加四层记忆
 * @param {number} bookId
 * @param {string} layer - 全局|大纲|人物|章节
 * @param {string} content
 * @param {number|null} chapterId
 * @param {number|null} characterId
 */
async function addMemory(bookId, layer, content, chapterId, characterId) {
  const mem = getMemory()
  const uid = userIdForBook(bookId)
  const metadata = {
    layer,
    chapter_id: chapterId ?? undefined,
    character_id: characterId ?? undefined,
    kind: 'memory',
  }
  const result = await mem.add(
    [{ role: 'user', content: content.trim() }],
    { userId: uid, metadata, infer: false }
  )
  const first = result?.results?.[0]
  return {
    id: first?.id ?? null,
    layer,
    content: content.trim(),
    chapter_id: chapterId ?? null,
    character_id: characterId ?? null,
  }
}

/**
 * 添加伏笔记忆（metadata 适配 mem0）
 * @param {number} bookId
 * @param {number} chapterId - 埋入章节
 * @param {string} content
 * @param {string} type - 悬念|道具|线索|对话
 * @param {number|null} expectedChapterId
 * @param {string} status - 未回收|已回收（内部用，update 时传入）
 * @param {number|null} resolvedChapterId - 回收章节（内部用）
 */
async function addForeshadowing(bookId, chapterId, content, type, expectedChapterId, status = '未回收', resolvedChapterId = null) {
  const mem = getMemory()
  const uid = userIdForBook(bookId)
  const metadata = {
    layer: '伏笔',
    kind: 'foreshadowing',
    book_id: bookId,
    chapter_id: chapterId,
    type: type || '悬念',
    status: status || '未回收',
    expected_chapter_id: expectedChapterId ?? undefined,
    resolved_chapter_id: resolvedChapterId ?? undefined,
  }
  const result = await mem.add(
    [{ role: 'user', content: content.trim() }],
    { userId: uid, metadata, infer: false }
  )
  const first = result?.results?.[0]
  return {
    id: first?.id ?? null,
    book_id: bookId,
    chapter_id: chapterId,
    content: content.trim(),
    type: type || '悬念',
    status: status || '未回收',
    expected_chapter_id: expectedChapterId ?? null,
    resolved_chapter_id: resolvedChapterId ?? null,
  }
}

/**
 * 更新伏笔（如回收状态）
 * mem0 的 update 只改 content，metadata 变更需 delete + add
 * @param {string} id - mem0 返回的 UUID
 * @param {object} data - { content?, type?, status?, resolved_chapter_id?, expected_chapter_id? }
 */
async function updateForeshadowing(id, data) {
  const mem = getMemory()
  const item = await mem.get(id)
  if (!item) return null

  const meta = item.metadata || {}
  const bookId = meta.book_id ?? 0
  const chapterId = meta.chapter_id ?? 0
  const content = data.content !== undefined ? data.content : item.memory
  const type = data.type !== undefined ? data.type : (meta.type || '悬念')
  const status = data.status !== undefined ? data.status : (meta.status || '未回收')
  const expectedChapterId = data.expected_chapter_id !== undefined ? data.expected_chapter_id : meta.expected_chapter_id
  const resolvedChapterId = data.resolved_chapter_id !== undefined ? data.resolved_chapter_id : meta.resolved_chapter_id

  await mem.delete(id)
  const added = await addForeshadowing(bookId, chapterId, content, type, expectedChapterId, status, resolvedChapterId)
  return added
}

/**
 * 删除记忆
 */
async function deleteMemory(id) {
  const mem = getMemory()
  await mem.delete(id)
}

/**
 * 删除伏笔
 */
async function deleteForeshadowing(id) {
  return deleteMemory(id)
}

/**
 * 按书获取全部记忆（四层）
 */
async function getMemoriesByBook(bookId, layer) {
  const mem = getMemory()
  const uid = userIdForBook(bookId)
  const result = await mem.getAll({ userId: uid, limit: 500 })
  const items = (result?.results || []).filter((r) => {
    const m = r.metadata || {}
    return m.kind === 'memory' && (!layer || m.layer === layer)
  })
  return items.map((r) => ({
    id: r.id,
    book_id: bookId,
    layer: r.metadata?.layer || '全局',
    content: r.memory,
    chapter_id: r.metadata?.chapter_id ?? null,
    character_id: r.metadata?.character_id ?? null,
    create_time: r.createdAt,
  }))
}

/**
 * 按 ID 获取记忆
 */
async function getMemoriesByIds(ids) {
  if (!ids || ids.length === 0) return []
  const mem = getMemory()
  const items = []
  for (const id of ids) {
    const r = await mem.get(id)
    if (r) {
      const meta = r.metadata || {}
      items.push({
        id: r.id,
        layer: meta.layer || '全局',
        content: r.memory,
        chapter_id: meta.chapter_id ?? null,
        character_id: meta.character_id ?? null,
        create_time: r.createdAt,
      })
    }
  }
  return items
}

/**
 * 语义检索记忆（用于注入 prompt）
 * @param {number} bookId
 * @param {string} query
 * @param {object} options - { layers?, chapterId?, limitPerLayer?, limit? }
 */
async function getMemoriesForPrompt(bookId, query, options = {}) {
  const mem = getMemory()
  const uid = userIdForBook(bookId)
  const layers = options.layers || MEMORY_LAYERS
  const limit = options.limit ?? 20
  const limitPerLayer = options.limitPerLayer ?? 5
  const chapterId = options.chapterId

  const result = await mem.search(query || ' ', { userId: uid, limit: limit * 2 })
  const raw = (result?.results || []).filter((r) => {
    const m = r.metadata || {}
    return m.kind === 'memory' && layers.includes(m.layer)
  })

  const byLayer = {}
  for (const layer of layers) byLayer[layer] = []
  for (const r of raw) {
    const layer = r.metadata?.layer
    if (chapterId != null && r.metadata?.chapter_id != null && r.metadata.chapter_id !== chapterId && layer === '章节') continue
    if (byLayer[layer] && byLayer[layer].length < limitPerLayer) {
      byLayer[layer].push({
        id: r.id,
        layer,
        content: r.memory,
        chapter_id: r.metadata?.chapter_id ?? null,
        character_id: r.metadata?.character_id ?? null,
        create_time: r.createdAt,
      })
    }
  }

  const resultList = []
  for (const layer of layers) {
    for (const item of byLayer[layer] || []) {
      resultList.push(item)
      if (resultList.length >= limit) break
    }
    if (resultList.length >= limit) break
  }
  return resultList
}

/**
 * 按书获取伏笔
 */
async function getForeshadowingByBook(bookId, statusFilter) {
  const mem = getMemory()
  const uid = userIdForBook(bookId)
  const result = await mem.getAll({ userId: uid, limit: 500 })
  const items = (result?.results || []).filter((r) => {
    const m = r.metadata || {}
    return m.kind === 'foreshadowing' && (!statusFilter || m.status === statusFilter)
  })
  return items.map((r) => ({
    id: r.id,
    book_id: bookId,
    chapter_id: r.metadata?.chapter_id,
    content: r.memory,
    type: r.metadata?.type || '悬念',
    status: r.metadata?.status || '未回收',
    expected_chapter_id: r.metadata?.expected_chapter_id ?? null,
    resolved_chapter_id: r.metadata?.resolved_chapter_id ?? null,
    create_time: r.createdAt,
    update_time: r.updatedAt,
  }))
}

/**
 * 按 ID 获取伏笔
 */
async function getForeshadowingByIds(ids) {
  if (!ids || ids.length === 0) return []
  const mem = getMemory()
  const items = []
  for (const id of ids) {
    const r = await mem.get(id)
    if (r && r.metadata?.kind === 'foreshadowing') {
      const m = r.metadata
      items.push({
        id: r.id,
        chapter_id: m.chapter_id,
        content: r.memory,
        type: m.type || '悬念',
        status: m.status || '未回收',
        expected_chapter_id: m.expected_chapter_id ?? null,
        resolved_chapter_id: m.resolved_chapter_id ?? null,
        create_time: r.createdAt,
        update_time: r.updatedAt,
      })
    }
  }
  return items
}

/**
 * 语义检索伏笔（用于注入 prompt）
 */
async function getForeshadowingForPrompt(bookId, query, options = {}) {
  const mem = getMemory()
  const uid = userIdForBook(bookId)
  const limit = options.limit ?? 10
  const statusFilter = options.status

  const result = await mem.search(query || ' ', { userId: uid, limit })
  const items = (result?.results || [])
    .filter((r) => {
      const m = r.metadata || {}
      return m.kind === 'foreshadowing' && (!statusFilter || m.status === statusFilter)
    })
    .slice(0, limit)
    .map((r) => ({
      id: r.id,
      chapter_id: r.metadata?.chapter_id,
      content: r.memory,
      type: r.metadata?.type || '悬念',
      status: r.metadata?.status || '未回收',
    }))
  return items
}

/**
 * 更新记忆（mem0 仅支持 content 更新）
 */
async function updateMemory(id, data) {
  const mem = getMemory()
  const item = await mem.get(id)
  if (!item) return null
  if (data.content !== undefined) {
    await mem.update(id, data.content)
  }
  const updated = await mem.get(id)
  const m = updated?.metadata || {}
  return {
    id: updated.id,
    layer: m.layer,
    content: updated.memory,
    chapter_id: m.chapter_id ?? data.chapter_id ?? null,
    character_id: m.character_id ?? data.character_id ?? null,
  }
}

module.exports = {
  getMem0DataDir,
  addMemory,
  addForeshadowing,
  updateMemory,
  updateForeshadowing,
  deleteMemory,
  deleteForeshadowing,
  getMemoriesByBook,
  getMemoriesByIds,
  getMemoriesForPrompt,
  getForeshadowingByBook,
  getForeshadowingByIds,
  getForeshadowingForPrompt,
}

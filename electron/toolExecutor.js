/**
 * 工具执行器：在后端执行所有 Agent 工具调用（书籍、大纲、章节、人物、记忆、伏笔等）
 * 供 ai-chat-stream 在收到 tool_calls 时内部调用，不发给前端
 */

const Database = require('./database')
const mem0Service = require('./mem0Service')
const { collectTextOutlineEntries } = require('./outlineTextForAgent')
const { getWritableChaptersForAgent } = require('./writingChaptersForAgent')

const MEMORY_LAYERS = ['全局', '大纲', '人物', '章节']
const MEMORY_LAYER_ORDERED = ['全局', '大纲', '人物', '章节']
const FORESHADOWING_TYPES = ['悬念', '道具', '线索', '对话']

function getDb() {
  return Database
}

function extractTextFromLexical(json) {
  try {
    const state = typeof json === 'string' ? JSON.parse(json) : json
    const extract = (node) => {
      if (node.type === 'text') return node.text || ''
      if (node.children) return node.children.map(extract).join('')
      return ''
    }
    return extract(state.root || {}).replace(/\n{3,}/g, '\n\n').trim()
  } catch {
    return typeof json === 'string' ? json : ''
  }
}

function extractLatestParagraph(text) {
  const src = String(text || '').replace(/\r\n/g, '\n').trim()
  if (!src) return ''
  const paras = src.split(/\n{2,}/).map((x) => x.trim()).filter(Boolean)
  return paras.length > 0 ? paras[paras.length - 1] : src
}

function formatChaptersAsText(chapters) {
  if (!chapters || chapters.length === 0) return ''
  const buildTree = (parentId, depth) => {
    const items = chapters.filter((c) => (c.parent_id ?? null) === (parentId ?? null))
    return items
      .map((c) => {
        const indent = '  '.repeat(depth)
        const children = buildTree(c.id, depth + 1)
        return children ? `${indent}${c.title}\n${children}` : `${indent}${c.title}`
      })
      .join('\n')
  }
  return buildTree(null, 0)
}

function formatCharactersAsText(characters) {
  if (!characters || characters.length === 0) return ''
  return characters
    .map((c) => {
      const parts = [`【${c.name || '未命名'}】`, `人物ID:${c.id}`]
      if (c.gender) parts.push(`性别：${c.gender}`)
      if (c.age) parts.push(`年龄：${c.age}`)
      if (c.occupation) parts.push(`职业：${c.occupation}`)
      if (c.personality) parts.push(`性格：${c.personality}`)
      if (c.appearance) parts.push(`外貌：${c.appearance}`)
      if (c.origin) parts.push(`来历：${c.origin}`)
      if (c.background) parts.push(`背景：${c.background}`)
      if (c.biography) parts.push(`经历：${c.biography}`)
      if (c.tags) parts.push(`标签：${c.tags}`)
      if (c.remark) parts.push(`备注：${c.remark}`)
      return parts.join('，')
    })
    .join('\n')
}

function loadOutlineWithChapters(outline) {
  const chapters = getDb().getChapters(outline.id)
  return { outline, chapters, chaptersText: formatChaptersAsText(chapters) }
}

function loadAllOutlinesForBook(bookId) {
  const db = getDb()
  const globalRes = db.getGlobalOutline(bookId)
  const volumeRes = db.getVolumeOutlines(bookId)
  const chapterRes = db.getChapterOutlines(bookId)
  const otherRes = db.getOtherOutlines(bookId)
  const writingRes = db.getOrCreateWritingOutline(bookId)

  const globalOutline = globalRes ? loadOutlineWithChapters(globalRes) : null
  const volumeOutlines = (volumeRes || []).map((vol) => ({
    ...vol,
    chapters_detail: (vol.chapters || []).map((ch) => loadOutlineWithChapters(ch)),
  }))
  const chapterOutlines = (chapterRes || []).map(loadOutlineWithChapters)
  const otherOutlines = (otherRes || []).map(loadOutlineWithChapters)
  const writingOutline = writingRes ? loadOutlineWithChapters(writingRes) : null

  return { globalOutline, volumeOutlines, chapterOutlines, otherOutlines, writingOutline }
}

function getAvailableOutlines(bookId) {
  const db = getDb()
  const list = []
  const global = db.getGlobalOutline(bookId)
  if (global) list.push(global)
  const chapter = db.getChapterOutlines(bookId) || []
  const other = db.getOtherOutlines(bookId) || []
  list.push(...chapter, ...other)
  return list
}

function getGlobalOutline(bookId, maxTextLength = 32000) {
  const row = getDb().getOrCreateGlobalOutline(bookId)
  if (!row) return null
  const markdownRaw = row.markdown_content || ''
  const markdown =
    typeof markdownRaw === 'string' && markdownRaw.length > maxTextLength
      ? markdownRaw.slice(0, maxTextLength) + '\n…（已截断）'
      : markdownRaw
  return {
    success: true,
    bookId: String(bookId),
    outlineId: String(row.id),
    title: row.title || '总纲',
    type: row.type || 'global',
    markdown: markdown || '',
    hasMarkdown: Boolean(markdownRaw && String(markdownRaw).trim()),
  }
}

function batchGetOutlineDetails(outlineIds, allOutlines) {
  return outlineIds.map((oid) => {
    const outline = (allOutlines || []).find((o) => o.id === oid) ?? { id: oid, title: '' }
    return loadOutlineWithChapters(outline)
  })
}

function queryOutline(bookId, outlineIds, includeChapters = true, includeText = true, maxTextLength = 32000) {
  const all = getAvailableOutlines(bookId)
  const filterSet =
    Array.isArray(outlineIds) && outlineIds.length > 0
      ? new Set(outlineIds.map((x) => String(x)))
      : null
  const targetOutlines = filterSet ? all.filter((o) => filterSet.has(String(o.id))) : all
  const details = includeChapters
    ? batchGetOutlineDetails(
        targetOutlines.map((o) => String(o.id)),
        all,
      )
    : []
  const byIdDetail = new Map(details.map((d) => [String(d.outline?.id), d]))
  const textEntries = includeText ? collectTextOutlineEntries(bookId, filterSet ? Array.from(filterSet) : undefined) : []
  const byIdText = new Map(textEntries.map((e) => [String(e.id), e.markdown]))

  const outlines = targetOutlines.map((o) => {
    const id = String(o.id)
    const detail = byIdDetail.get(id)
    const markdown = byIdText.get(id)
    const trimmedMarkdown =
      typeof markdown === 'string' && markdown.length > maxTextLength
        ? markdown.slice(0, maxTextLength) + '\n…（已截断）'
        : markdown
    return {
      id,
      title: o.title || '',
      type: o.type || '',
      chaptersText: includeChapters ? detail?.chaptersText || '' : undefined,
      markdown: includeText ? trimmedMarkdown || '' : undefined,
      hasMarkdown: Boolean(markdown && String(markdown).trim()),
    }
  })
  return {
    success: true,
    bookId: String(bookId),
    total: outlines.length,
    outlines,
  }
}

function getChapterContent(chapterId, title, maxTextLength = 12000) {
  const row = getDb().getArticle(chapterId)
  if (!row) return null
  const raw = row.content || ''
  const plainText = raw ? extractTextFromLexical(raw).slice(0, maxTextLength) : ''
  return { chapterId, title: title ?? '', content: raw, plainText }
}

/** 本会话 toolCtx 上的章节正文缓存（同一 ai-chat-stream / 写作专家管线内共享） */
function ensureChapterContentCache(ctx) {
  if (!ctx || typeof ctx !== 'object') return null
  if (!ctx.chapterContentCache) ctx.chapterContentCache = new Map()
  return ctx.chapterContentCache
}

function invalidateChapterContentCache(ctx, chapterId) {
  const m = ensureChapterContentCache(ctx)
  if (!m || chapterId == null) return
  m.delete(String(chapterId).trim())
}

function titleFromWritingCatalog(chapterId, writingChapters) {
  const key = String(chapterId ?? '').trim()
  const c = (writingChapters || []).find((x) => String(x.id) === key)
  return c ? String(c.title || '').trim() : ''
}

/** 读库并返回全文 plain（不截断），供缓存 */
function readChapterPlainFull(chapterId) {
  const row = getDb().getArticle(chapterId)
  if (!row) return null
  const raw = row.content || ''
  const plainTextFull = raw ? extractTextFromLexical(raw) : ''
  return { plainTextFull }
}

/** 本会话只读工具缓存（与 chapterContentCache 分工：此处存 JSON 字符串返回值） */
function ensureReadToolCache(ctx) {
  if (!ctx || typeof ctx !== 'object') return null
  if (!ctx.readToolCache) ctx.readToolCache = new Map()
  return ctx.readToolCache
}

function readToolCacheGet(ctx, key) {
  const m = ensureReadToolCache(ctx)
  return m && m.get(key)
}

function readToolCacheSet(ctx, key, value) {
  const m = ensureReadToolCache(ctx)
  if (m) m.set(key, value)
}

/** 大纲/总纲等元数据变更后，只读大纲类缓存失效 */
function invalidateReadToolCache(ctx) {
  const m = ensureReadToolCache(ctx)
  if (m) m.clear()
}

function batchGetChapterContents(chapterIds, titleMap, maxTextLength = 12000) {
  const titleOf = (id) => (titleMap && titleMap.get ? titleMap.get(id) : '') || (titleMap && titleMap[id]) || ''
  return chapterIds.map((cid) => {
    const row = getDb().getArticle(cid)
    const raw = row?.content || ''
    const plainText = raw ? extractTextFromLexical(raw).slice(0, maxTextLength) : ''
    return { chapterId: cid, title: titleOf(cid), content: raw, plainText }
  })
}

function listWritingChapters(bookId) {
  const db = getDb()
  const writing = db.getOrCreateWritingOutline(bookId)
  if (!writing?.id) {
    return { success: false, error: '未找到写作目录' }
  }
  const rows = db.getChapters(writing.id) || []
  const parentSet = new Set(
    rows
      .map((r) => (r.parent_id == null || r.parent_id === '' ? null : String(r.parent_id)))
      .filter((x) => x != null && x !== ''),
  )
  const items = rows.map((r) => {
    const id = String(r.id)
    const hasChildren = parentSet.has(id)
    // 有子节点的节点视为“卷”；其余视为“章节”
    const nodeType = hasChildren ? 'volume' : 'chapter'
    return {
      id,
      title: String(r.title || ''),
      parentId: r.parent_id == null || r.parent_id === '' ? null : String(r.parent_id),
      level: Number(r.level || 1),
      sort: Number(r.sort || 0),
      nodeType,
      hasChildren,
    }
  })
  return {
    success: true,
    bookId: String(bookId),
    writingOutlineId: String(writing.id),
    total: items.length,
    items,
  }
}

function parseArgs(argsStr) {
  try {
    return JSON.parse(argsStr || '{}')
  } catch {
    return {}
  }
}

/**
 * 与前端传入的 writingChapters 快照一致：非空时仅允许目录内且标题非空的章节 id 读写正文，
 * 失败则拦截不访问数据库，返回 error 供模型在下一轮 tool 结果中看到。
 * 若 writingChapters 为空，不校验（兼容旧路径或未带目录的请求）。
 */
function chapterAllowedByWritingCatalog(chapterId, writingChapters) {
  if (!Array.isArray(writingChapters) || writingChapters.length === 0) {
    return { ok: true }
  }
  const key = String(chapterId ?? '').trim()
  if (!key) {
    return { ok: false }
  }
  const writable = getWritableChaptersForAgent(writingChapters)
  const row = writable.find((c) => String(c.id) === key)
  if (!row) {
    return { ok: false, chapterId: key }
  }
  if (!String(row.title || '').trim()) {
    return { ok: false, chapterId: key }
  }
  return { ok: true }
}

function chaptersAllowedByWritingCatalog(chapterIds, writingChapters) {
  if (!Array.isArray(writingChapters) || writingChapters.length === 0) {
    return { ok: true }
  }
  const ids = (Array.isArray(chapterIds) ? chapterIds : [])
    .map((x) => String(x).trim())
    .filter((s) => s !== '')
  if (ids.length === 0) {
    return { ok: false, badIds: [] }
  }
  const bad = []
  for (const id of ids) {
    const one = chapterAllowedByWritingCatalog(id, writingChapters)
    if (!one.ok) bad.push(id)
  }
  if (bad.length > 0) {
    return {
      ok: false,
      badIds: bad,
    }
  }
  return { ok: true }
}

/** 章节目录校验类失败对模型仅返回简短 error，详细原因由主稿专家向用户说明 */
const CATALOG_TOOL_FAIL_MSG = '失败'

function ensureCatalogRejectSet(ctx) {
  if (!ctx._catalogRejectedChapterIds) ctx._catalogRejectedChapterIds = new Set()
  return ctx._catalogRejectedChapterIds
}

function catalogRejectPayload(ctx, gate, cidRaw) {
  const set = ensureCatalogRejectSet(ctx)
  const key =
    gate.chapterId != null && String(gate.chapterId).trim() !== ''
      ? String(gate.chapterId).trim()
      : cidRaw != null && String(cidRaw).trim() !== ''
        ? String(cidRaw).trim()
        : ''
  if (key) set.add(key)
  return {
    error: CATALOG_TOOL_FAIL_MSG,
    chapterId: key || undefined,
  }
}

function jsonCatalogReject(ctx, gate, cidRaw) {
  const p = catalogRejectPayload(ctx, gate, cidRaw)
  return JSON.stringify({
    error: p.error,
    ...(p.chapterId != null ? { chapterId: p.chapterId } : {}),
  })
}

function jsonBatchCatalogReject(ctx, batchGate) {
  const set = ensureCatalogRejectSet(ctx)
  const badIds = Array.isArray(batchGate.badIds)
    ? batchGate.badIds.map((x) => String(x).trim()).filter((s) => s !== '')
    : []
  const allDup = badIds.length > 0 && badIds.every((id) => set.has(id))
  if (!allDup) {
    for (const id of badIds) set.add(id)
  }
  return JSON.stringify({ error: CATALOG_TOOL_FAIL_MSG })
}

/**
 * 与 runTools 内 toolFromCache 判定一致：执行前即可知是否将命中会话内只读缓存（用于前端整行不展示，含「正在执行」）
 */
function toolWillHitReadCache(ctx, tc, writingChapters, defaultBookId) {
  const name = tc.function?.name
  const args = parseArgs(tc.function?.arguments || '{}')
  const allow = ctx.subagentAllowedToolNames
  if (allow instanceof Set && name && !allow.has(name)) {
    return false
  }
  try {
    switch (name) {
      case 'getChapterContent': {
        const cid = args.chapterId
        const gate = chapterAllowedByWritingCatalog(cid, writingChapters)
        if (!gate.ok) return false
        const key = String(cid).trim()
        const cache = ensureChapterContentCache(ctx)
        return Boolean(cache && cache.has(key))
      }
      case 'listWritingChapters': {
        const bid = args.bookId ?? defaultBookId
        if (bid == null || String(bid).trim() === '') return false
        const ck = `listWritingChapters:${String(bid)}`
        return readToolCacheGet(ctx, ck) != null
      }
      case 'batchGetChapterContents': {
        const cids = args.chapterIds || []
        const batchGate = chaptersAllowedByWritingCatalog(cids, writingChapters)
        if (!batchGate.ok) return false
        const cache = ensureChapterContentCache(ctx)
        const idList = Array.isArray(cids) ? cids : []
        if (idList.length === 0) return false
        return idList.every((cidRaw) => {
          const cid = String(cidRaw).trim()
          return Boolean(cache && cache.has(cid))
        })
      }
      case 'getBookCharacters': {
        const bid = args.bookId ?? defaultBookId
        const ids = args.characterIds
        const nameQueries = args.names
        const filtered =
          (Array.isArray(ids) && ids.length > 0) ||
          (Array.isArray(nameQueries) && nameQueries.length > 0)
        if (filtered) return false
        const ck = `getBookCharacters:all:${String(bid)}`
        return readToolCacheGet(ctx, ck) != null
      }
      case 'listBookCharacters': {
        const bid = args.bookId ?? defaultBookId
        const ck = `listBookCharacters:${String(bid)}`
        return readToolCacheGet(ctx, ck) != null
      }
      case 'getStoryBackground': {
        const bid = args.bookId ?? defaultBookId
        const ck = `getStoryBackground:${String(bid)}`
        return readToolCacheGet(ctx, ck) != null
      }
      case 'queryOutline': {
        const bid = args.bookId ?? defaultBookId
        if (bid == null || String(bid).trim() === '') return false
        const oids = args.outlineIds
        const includeChapters = args.includeChapters !== false
        const includeText = args.includeText !== false
        const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
        const oidKey = Array.isArray(oids) ? [...oids].map(String).sort().join(',') : ''
        const ck = `queryOutline:${String(bid)}:${oidKey}:${includeChapters}:${includeText}:${maxLen}`
        return readToolCacheGet(ctx, ck) != null
      }
      case 'getGlobalOutline': {
        const bid = args.bookId ?? defaultBookId
        if (bid == null || String(bid).trim() === '') return false
        const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
        const ck = `getGlobalOutline:${String(bid)}:${maxLen}`
        return readToolCacheGet(ctx, ck) != null
      }
      case 'listOutlines': {
        const bid = args.bookId ?? defaultBookId
        if (bid == null || String(bid).trim() === '') return false
        const ck = `listOutlines:${String(bid)}`
        return readToolCacheGet(ctx, ck) != null
      }
      default:
        return false
    }
  } catch {
    return false
  }
}

/**
 * 执行工具调用
 * @param {Array<{ id: string, type: string, function: { name: string, arguments: string } }>} toolCalls
 * @param {object} ctx - { bookId, chapterId, currentChapterTitle, writingChapters, availableOutlines }
 * @param {function} sendChunk - (chunk) => void 用于通知前端章节更新等
 * @returns {Promise<Array<{ tool_call_id: string, content: string }>>}
 */
async function runTools(toolCalls, ctx, sendChunk) {
  const results = []
  const { bookId, chapterId, currentChapterTitle, writingChapters = [], availableOutlines = [] } = ctx
  const defaultBookId = bookId != null ? bookId : null

  const toolReadCacheMask = toolCalls.map((tc) =>
    toolWillHitReadCache(ctx, tc, writingChapters, defaultBookId),
  )
  if (typeof sendChunk === 'function' && toolReadCacheMask.some(Boolean)) {
    sendChunk({ toolReadCacheMask })
  }

  for (let i = 0; i < toolCalls.length; i++) {
    const tc = toolCalls[i]
    const name = tc.function?.name
    const args = parseArgs(tc.function?.arguments || '{}')
    let content
    let toolFromCache = false

    try {
      const allow = ctx.subagentAllowedToolNames
      if (allow instanceof Set && name && !allow.has(name)) {
        content = JSON.stringify({
          error: `工具「${name}」不在当前 subagent 授权范围，已拒绝执行。`,
        })
        results.push({ tool_call_id: tc.id, content })
        if (typeof sendChunk === 'function') {
          sendChunk({ toolIndexCompleted: i })
        }
        continue
      }
      switch (name) {
        case 'getChapterContent': {
          const cid = args.chapterId
          const title = args.title
          const maxLen = args.maxTextLength || 12000
          const gate = chapterAllowedByWritingCatalog(cid, writingChapters)
          if (!gate.ok) {
            content = jsonCatalogReject(ctx, gate, cid)
            break
          }
          const key = String(cid).trim()
          const cache = ensureChapterContentCache(ctx)
          if (cache && cache.has(key)) {
            const entry = cache.get(key)
            const plainSlice = String(entry.plainTextFull ?? '').slice(0, maxLen)
            const titleRes = title || entry.titleResolved || titleFromWritingCatalog(cid, writingChapters) || ''
            content = JSON.stringify({
              chapterId: key,
              title: titleRes,
              plainText: plainSlice || '（本章暂无正文内容）',
            })
            toolFromCache = true
            break
          }
          const full = readChapterPlainFull(cid)
          if (!full) {
            content = JSON.stringify({
              error: CATALOG_TOOL_FAIL_MSG,
              chapterId: cid,
            })
            break
          }
          const titleResolved = title || titleFromWritingCatalog(cid, writingChapters) || ''
          if (cache) {
            cache.set(key, { plainTextFull: full.plainTextFull, titleResolved })
          }
          const plainText = String(full.plainTextFull || '').slice(0, maxLen)
          content = JSON.stringify({
            chapterId: key,
            title: titleResolved,
            plainText: plainText || '（本章暂无正文内容）',
          })
          break
        }
        case 'listWritingChapters': {
          const bid = args.bookId ?? defaultBookId
          if (bid == null || String(bid).trim() === '') {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法获取写作目录章节列表' })
            break
          }
          const ck = `listWritingChapters:${String(bid)}`
          const hit = readToolCacheGet(ctx, ck)
          if (hit != null) {
            content = hit
            toolFromCache = true
            break
          }
          const result = listWritingChapters(String(bid))
          const payload = JSON.stringify(result).slice(0, 24000)
          readToolCacheSet(ctx, ck, payload)
          content = payload
          break
        }
        case 'batchGetChapterContents': {
          const cids = args.chapterIds || []
          const maxLen = args.maxTextLength || 12000
          const batchGate = chaptersAllowedByWritingCatalog(cids, writingChapters)
          if (!batchGate.ok) {
            content = jsonBatchCatalogReject(ctx, batchGate)
            break
          }
          const titleMap = new Map((writingChapters || []).map((c) => [String(c.id), c.title]))
          const cache = ensureChapterContentCache(ctx)
          const idList = Array.isArray(cids) ? cids : []
          let batchAllCached = idList.length > 0
          const list = idList.map((cidRaw) => {
            const cid = String(cidRaw).trim()
            const t = titleMap.get(String(cid))
            if (cache && cache.has(cid)) {
              const entry = cache.get(cid)
              const plainText = String(entry.plainTextFull ?? '').slice(0, maxLen)
              return { chapterId: cid, title: t || entry.titleResolved || '', plainText }
            }
            batchAllCached = false
            const full = readChapterPlainFull(cid)
            if (!full) {
              return { chapterId: cid, title: t || '', plainText: '' }
            }
            const titleResolved = t || titleFromWritingCatalog(cid, writingChapters) || ''
            if (cache) {
              cache.set(cid, { plainTextFull: full.plainTextFull, titleResolved })
            }
            const plainText = String(full.plainTextFull || '').slice(0, maxLen)
            return { chapterId: cid, title: titleResolved, plainText }
          })
          content = JSON.stringify(list.map((c) => ({ chapterId: c.chapterId, title: c.title, plainText: c.plainText }))).slice(0, 24000)
          toolFromCache = batchAllCached
          break
        }
        case 'getBookCharacters': {
          const bid = args.bookId ?? defaultBookId
          const ids = args.characterIds
          const nameQueries = args.names
          const filtered =
            (Array.isArray(ids) && ids.length > 0) ||
            (Array.isArray(nameQueries) && nameQueries.length > 0)
          if (!filtered) {
            const ck = `getBookCharacters:all:${String(bid)}`
            const hit = readToolCacheGet(ctx, ck)
            if (hit != null) {
              content = hit
              toolFromCache = true
              break
            }
          }
          let chars = getDb().getCharacters(bid) || []
          if (Array.isArray(ids) && ids.length > 0) {
            const idSet = new Set(ids.map((x) => Number(x)).filter((n) => !Number.isNaN(n)))
            chars = chars.filter((c) => idSet.has(c.id))
          } else if (Array.isArray(nameQueries) && nameQueries.length > 0) {
            const needles = nameQueries
              .map((n) => String(n).trim().toLowerCase())
              .filter(Boolean)
            if (needles.length > 0) {
              chars = chars.filter((c) => {
                const nm = String(c.name || '').toLowerCase()
                return needles.some((q) => nm.includes(q))
              })
            }
          }
          content = formatCharactersAsText(chars) || '（暂无人物）'
          if (!filtered) {
            readToolCacheSet(ctx, `getBookCharacters:all:${String(bid)}`, content)
          }
          break
        }
        case 'listBookCharacters': {
          const bid = args.bookId ?? defaultBookId
          const ck = `listBookCharacters:${String(bid)}`
          const hit = readToolCacheGet(ctx, ck)
          if (hit != null) {
            content = hit
            toolFromCache = true
            break
          }
          const chars = getDb().getCharacters(bid) || []
          const list = chars.map((c) => ({
            id: c.id,
            name:
              String(c.name || '未命名')
                .replace(/\r?\n/g, ' ')
                .trim() || '未命名',
          }))
          content = JSON.stringify(list)
          readToolCacheSet(ctx, ck, content)
          break
        }
        case 'getStoryBackground': {
          const bid = args.bookId ?? defaultBookId
          const ck = `getStoryBackground:${String(bid)}`
          const hit = readToolCacheGet(ctx, ck)
          if (hit != null) {
            content = hit
            toolFromCache = true
            break
          }
          const row = getDb().getStoryBackground(bid)
          content = row?.content ?? '（暂无小说背景）'
          readToolCacheSet(ctx, ck, content)
          break
        }
        case 'queryOutline': {
          const bid = args.bookId ?? defaultBookId
          if (bid == null || String(bid).trim() === '') {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法查询大纲' })
            break
          }
          const oids = args.outlineIds
          const includeChapters = args.includeChapters !== false
          const includeText = args.includeText !== false
          const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
          const oidKey = Array.isArray(oids) ? [...oids].map(String).sort().join(',') : ''
          const ck = `queryOutline:${String(bid)}:${oidKey}:${includeChapters}:${includeText}:${maxLen}`
          const hit = readToolCacheGet(ctx, ck)
          if (hit != null) {
            content = hit
            toolFromCache = true
            break
          }
          const result = queryOutline(bid, oids, includeChapters, includeText, maxLen)
          console.log('[toolExecutor:queryOutline] done', {
            bookId: String(bid),
            outlineIds: Array.isArray(oids) ? oids : null,
            total: result.total,
            includeChapters,
            includeText,
            maxTextLength: maxLen,
          })
          const payload = JSON.stringify(result).slice(0, 24000)
          readToolCacheSet(ctx, ck, payload)
          content = payload
          break
        }
        case 'getGlobalOutline': {
          const bid = args.bookId ?? defaultBookId
          if (bid == null || String(bid).trim() === '') {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法获取总纲' })
            break
          }
          const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
          const ck = `getGlobalOutline:${String(bid)}:${maxLen}`
          const hit = readToolCacheGet(ctx, ck)
          if (hit != null) {
            content = hit
            toolFromCache = true
            break
          }
          const result = getGlobalOutline(bid, maxLen)
          if (!result) {
            content = JSON.stringify({ success: false, error: '获取总纲失败' })
            break
          }
          content = JSON.stringify(result)
          readToolCacheSet(ctx, ck, content)
          break
        }
        case 'editGlobalOutline': {
          const bid = args.bookId ?? defaultBookId
          if (bid == null || String(bid).trim() === '') {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId' })
            break
          }
          if (typeof args.markdownContent !== 'string') {
            content = JSON.stringify({ success: false, error: 'markdownContent 必须为字符串' })
            break
          }
          try {
            const globalOutline = getDb().getOrCreateGlobalOutline(bid)
            if (!globalOutline?.id) {
              content = JSON.stringify({ success: false, error: '无法创建或获取总纲' })
              break
            }
            const saved = getDb().updateOutline({
              outlineId: String(globalOutline.id),
              markdown_content: args.markdownContent,
            })
            invalidateReadToolCache(ctx)
            content = JSON.stringify({
              success: true,
              bookId: String(bid),
              outlineId: String(globalOutline.id),
              title: saved?.title || '总纲',
              type: saved?.type || 'global',
              markdownLength: String(args.markdownContent).length,
            })
          } catch (e) {
            content = JSON.stringify({ success: false, error: e.message })
          }
          break
        }
        case 'listOutlines': {
          const bid = args.bookId ?? defaultBookId
          if (bid == null || String(bid).trim() === '') {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法获取大纲列表' })
            break
          }
          const ck = `listOutlines:${String(bid)}`
          const hit = readToolCacheGet(ctx, ck)
          if (hit != null) {
            content = hit
            toolFromCache = true
            break
          }
          const list = getAvailableOutlines(bid).map((o) => ({
            id: String(o.id),
            title: o.title || '',
            type: o.type || '',
          }))
          const payload = JSON.stringify({
            success: true,
            bookId: String(bid),
            total: list.length,
            outlines: list,
          })
          readToolCacheSet(ctx, ck, payload)
          content = payload
          break
        }
        case 'updateOutline': {
          const bid = args.bookId ?? defaultBookId
          const oid = args.outlineId != null ? String(args.outlineId).trim() : ''
          if (bid == null || String(bid).trim() === '') {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId' })
            break
          }
          if (!oid) {
            content = JSON.stringify({ success: false, error: '缺少有效 outlineId' })
            break
          }
          const updatePayload = { outlineId: oid }
          if (args.title !== undefined) updatePayload.title = args.title
          if (args.xmind_data !== undefined) updatePayload.xmind_data = args.xmind_data
          if (args.file_path !== undefined) updatePayload.file_path = args.file_path
          if (args.markdown_content !== undefined) updatePayload.markdown_content = args.markdown_content
          if (Object.keys(updatePayload).length === 1) {
            content = JSON.stringify({ success: false, error: '缺少可更新字段（title/xmind_data/file_path/markdown_content）' })
            break
          }
          const allOutlines = loadAllOutlinesForBook(bid)
          const exists = [
            allOutlines?.globalOutline?.outline,
            ...(allOutlines?.volumeOutlines || []).map((v) => v),
            ...(allOutlines?.volumeOutlines || []).flatMap((v) => (v.chapters_detail || []).map((c) => c.outline)),
            ...(allOutlines?.chapterOutlines || []).map((o) => o.outline),
            ...(allOutlines?.otherOutlines || []).map((o) => o.outline),
            allOutlines?.writingOutline?.outline,
          ]
            .filter(Boolean)
            .some((o) => String(o.id) === oid)
          if (!exists) {
            content = JSON.stringify({
              success: false,
              error: 'outlineId 不属于当前书籍，或该大纲不存在',
              outlineId: oid,
            })
            break
          }
          try {
            const saved = getDb().updateOutline(updatePayload)
            invalidateReadToolCache(ctx)
            content = JSON.stringify({
              success: true,
              outlineId: oid,
              title: saved?.title || '',
              type: saved?.type || '',
              updatedFields: Object.keys(updatePayload).filter((k) => k !== 'outlineId'),
            })
          } catch (e) {
            content = JSON.stringify({ success: false, error: e.message })
          }
          break
        }
        case 'editChapterContent': {
          const cid = args.chapterId
          const newContent = typeof args.content === 'string' ? args.content : ''
          if (!cid) {
            content = JSON.stringify({ success: false, error: '缺少 chapterId' })
          } else {
            const editGate = chapterAllowedByWritingCatalog(cid, writingChapters)
            if (!editGate.ok) {
              const p = catalogRejectPayload(ctx, editGate, cid)
              content = JSON.stringify({
                success: false,
                error: p.error,
                ...(p.chapterId != null ? { chapterId: p.chapterId } : {}),
              })
              break
            }
            try {
              let mergedContent = newContent
              // 协作共创：默认按“增量段落”追加到现有正文；若模型已传完整正文（前缀命中）则不重复追加
              if (ctx?.collabWriting === true && newContent.trim()) {
                const oldPlain = readChapterPlainFull(cid)?.plainTextFull || ''
                const oldTrim = String(oldPlain || '').trim()
                const newTrim = String(newContent || '').trim()
                if (oldTrim && newTrim && !newTrim.startsWith(oldTrim)) {
                  mergedContent = `${oldTrim}\n\n${newTrim}`
                }
              }
              getDb().saveArticle(cid, mergedContent)
              invalidateChapterContentCache(ctx, cid)
              if (sendChunk) {
                const latestParagraph = extractLatestParagraph(newContent)
                sendChunk({
                  chapterContentUpdated: cid,
                  ...(latestParagraph ? { collabLatestParagraph: latestParagraph } : {}),
                }) // 通知前端刷新章节，并在协作模式对话中展示最新段落
              }
              content = JSON.stringify({ success: true, message: '章节正文已保存', chapterId: cid })
            } catch (e) {
              content = JSON.stringify({ success: false, error: e.message })
            }
          }
          break
        }
        case 'addMemory': {
          const bid = args.bookId ?? defaultBookId
          const memContent = typeof args.content === 'string' ? args.content.trim() : ''
          const cid = args.chapterId ?? chapterId ?? undefined
          const characterId = args.characterId
          const layerNum = typeof args.layer === 'number' ? args.layer : undefined
          const layerValid = layerNum !== undefined && [0, 1, 2, 3].includes(layerNum)
          if (!layerValid || !memContent) {
            content = JSON.stringify({
              success: false,
              error: !memContent ? '记忆内容不能为空' : `layer 须为 0/1/2/3（${MEMORY_LAYER_ORDERED.join(' / ')}）`,
            })
          } else {
            const layerStr = MEMORY_LAYERS[layerNum]
            try {
              const row = await mem0Service.addMemory(bid, layerStr, memContent, cid ?? null, characterId ?? null)
              content = JSON.stringify({ success: true, message: `已添加【${layerStr}记忆】`, id: row?.id })
            } catch (e) {
              content = JSON.stringify({ success: false, error: e.message })
            }
          }
          break
        }
        case 'addForeshadowing': {
          const bid = args.bookId ?? defaultBookId
          const forChapterId = args.chapterId
          const forContent = typeof args.content === 'string' ? args.content.trim() : ''
          const forType = args.type || FORESHADOWING_TYPES[0]
          if (!forChapterId || !forContent) {
            content = JSON.stringify({
              success: false,
              error: !forChapterId ? '缺少 chapterId（埋入章节）' : '伏笔内容不能为空',
            })
          } else if (!FORESHADOWING_TYPES.includes(forType)) {
            content = JSON.stringify({ success: false, error: `type 须为：${FORESHADOWING_TYPES.join(' / ')}` })
          } else {
            try {
              const row = await mem0Service.addForeshadowing(bid, forChapterId, forContent, forType, null)
              content = JSON.stringify({ success: true, message: `已添加伏笔【${forType}】`, id: row?.id })
            } catch (e) {
              content = JSON.stringify({ success: false, error: e.message })
            }
          }
          break
        }
        case 'searchMemories': {
          const bid = args.bookId ?? defaultBookId
          const query = args.query || ''
          const layer = args.layer
          const cid = args.chapterId ?? chapterId ?? undefined
          const limit = args.limit || 15
          try {
            const parts = []
            const wantForeshadowing = !layer || layer === '伏笔'
            const wantLayers = !layer || layer === '伏笔' ? undefined : [layer]
            const memRes = await mem0Service.getMemoriesForPrompt(bid, query, {
              layers: wantLayers,
              chapterId: cid,
              limit: wantForeshadowing ? Math.max(1, limit - 5) : limit,
            })
            if (memRes && memRes.length > 0) {
              const byLayer = new Map()
              for (const m of memRes) {
                const arr = byLayer.get(m.layer) || []
                arr.push((m.content || '').trim())
                byLayer.set(m.layer, arr)
              }
              for (const L of MEMORY_LAYER_ORDERED) {
                const arr = byLayer.get(L)
                if (arr?.length) parts.push(`【${L}记忆】\n${arr.join('\n')}`)
              }
            }
            if (wantForeshadowing) {
              const forRes = await mem0Service.getForeshadowingForPrompt(bid, query, { limit: 5 })
              if (forRes && forRes.length > 0) {
                const lines = forRes.map((f) => `- ${f.content}（类型：${f.type}，状态：${f.status}）`)
                parts.push('【伏笔记忆】\n' + lines.join('\n'))
              }
            }
            content = parts.length > 0 ? parts.join('\n\n') : '（未找到与当前检索相关的长期记忆）'
          } catch (e) {
            content = JSON.stringify({ error: e.message })
          }
          break
        }
        default:
          content = JSON.stringify({ error: `未知工具: ${name}` })
      }
    } catch (err) {
      content = JSON.stringify({ error: (err && err.message) || String(err) })
    }

    results.push({ tool_call_id: tc.id, content })
    if (typeof sendChunk === 'function') {
      sendChunk(
        toolFromCache
          ? { toolIndexCompleted: i, toolFromCache: true }
          : { toolIndexCompleted: i },
      )
    }
  }

  return results
}

module.exports = { runTools, collectTextOutlineEntries }

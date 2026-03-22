/**
 * 工具执行器：在后端执行所有 Agent 工具调用（书籍、大纲、章节、人物、记忆、伏笔等）
 * 供 ai-chat-stream 在收到 tool_calls 时内部调用，不发给前端
 */

const Database = require('./database')
const mem0Service = require('./mem0Service')
const { collectTextOutlineEntries, formatTextOutlineForAgent } = require('./outlineTextForAgent')

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

async function getAllOutlines(bookId) {
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

function getWritingOutlineWithChapters(bookId) {
  if (bookId == null) return null
  const row = getDb().getOrCreateWritingOutline(bookId)
  if (!row) return null
  const chapters = getDb().getChapters(row.id)
  return { outlineId: row.id, chapters }
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

function batchGetOutlineDetails(outlineIds, allOutlines) {
  return outlineIds.map((oid) => {
    const outline = (allOutlines || []).find((o) => o.id === oid) ?? { id: oid, title: '' }
    return loadOutlineWithChapters(outline)
  })
}

function getChapterContent(chapterId, title, maxTextLength = 12000) {
  const row = getDb().getArticle(chapterId)
  if (!row) return null
  const raw = row.content || ''
  const plainText = raw ? extractTextFromLexical(raw).slice(0, maxTextLength) : ''
  return { chapterId, title: title ?? '', content: raw, plainText }
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

function getBookContext(bookId, currentChapterId, currentChapterTitle) {
  const chapter = currentChapterId ? getChapterContent(currentChapterId, currentChapterTitle) : null
  const writingRow = getDb().getOrCreateWritingOutline(bookId)
  const writingOutline = writingRow ? loadOutlineWithChapters(writingRow) : null
  const bg = getDb().getStoryBackground(bookId)
  const chars = getDb().getCharacters(bookId) || []

  const parts = []
  if (chapter?.plainText) {
    parts.push(`【当前正在写的章节：${chapter.title || '当前章节'}】\n${chapter.plainText}`)
  }
  if (writingOutline?.chaptersText) {
    parts.push(`【本书写作大纲】\n${writingOutline.chaptersText}`)
  }
  if (bg?.content) {
    parts.push(`【小说背景】\n${bg.content}`)
  }
  if (chars.length > 0) {
    parts.push(`【人物信息】\n${formatCharactersAsText(chars)}`)
  }
  return parts.length > 0 ? parts.join('\n\n') : '（暂无内容）'
}

function parseArgs(argsStr) {
  try {
    return JSON.parse(argsStr || '{}')
  } catch {
    return {}
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
  const defaultBookId = bookId ?? 0

  for (let i = 0; i < toolCalls.length; i++) {
    const tc = toolCalls[i]
    const name = tc.function?.name
    const args = parseArgs(tc.function?.arguments || '{}')
    let content

    try {
      switch (name) {
        case 'getBookContext': {
          const bid = args.bookId ?? defaultBookId
          const cid = args.currentChapterId ?? chapterId ?? undefined
          const title = args.currentChapterTitle ?? currentChapterTitle
          content = getBookContext(bid, cid, title) || '（暂无内容）'
          break
        }
        case 'getAllOutlines': {
          const bid = args.bookId ?? defaultBookId
          const data = getAllOutlines(bid)
          content = JSON.stringify(data, null, 0).slice(0, 16000)
          break
        }
        case 'getWritingOutlineWithChapters': {
          const bid = args.bookId ?? defaultBookId
          const data = getWritingOutlineWithChapters(bid)
          content = data ? JSON.stringify(data).slice(0, 12000) : 'null'
          break
        }
        case 'getChapterContent': {
          const cid = args.chapterId
          const title = args.title
          const maxLen = args.maxTextLength || 12000
          const data = getChapterContent(cid, title, maxLen)
          if (!data) {
            content = JSON.stringify({
              error: '未找到该章节正文。请确认 chapterId 来自 getWritingOutlineWithChapters(bookId) 返回的 chapters[].id（写作目录），不要使用 getAllOutlines 或其他大纲的章节 id。',
              chapterId: cid,
            })
          } else {
            content = JSON.stringify({ chapterId: data.chapterId, title: data.title, plainText: data.plainText || '（本章暂无正文内容）' })
          }
          break
        }
        case 'batchGetChapterContents': {
          const cids = args.chapterIds || []
          const maxLen = args.maxTextLength || 12000
          const titleMap = new Map((writingChapters || []).map((c) => [c.id, c.title]))
          const list = batchGetChapterContents(cids, titleMap, maxLen)
          content = JSON.stringify(list.map((c) => ({ chapterId: c.chapterId, title: c.title, plainText: c.plainText }))).slice(0, 24000)
          break
        }
        case 'getBookCharacters': {
          const bid = args.bookId ?? defaultBookId
          let chars = getDb().getCharacters(bid) || []
          const ids = args.characterIds
          const nameQueries = args.names
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
          break
        }
        case 'listBookCharacters': {
          const bid = args.bookId ?? defaultBookId
          const chars = getDb().getCharacters(bid) || []
          const list = chars.map((c) => ({
            id: c.id,
            name:
              String(c.name || '未命名')
                .replace(/\r?\n/g, ' ')
                .trim() || '未命名',
          }))
          content = JSON.stringify(list)
          break
        }
        case 'getStoryBackground': {
          const bid = args.bookId ?? defaultBookId
          const row = getDb().getStoryBackground(bid)
          content = row?.content ?? '（暂无小说背景）'
          break
        }
        case 'getAvailableOutlines': {
          const bid = args.bookId ?? defaultBookId
          const list = getAvailableOutlines(bid)
          content = JSON.stringify(list.map((o) => ({ id: o.id, title: o.title, type: o.type })))
          break
        }
        case 'batchGetOutlineDetails': {
          const oids = args.outlineIds || []
          const bid = args.bookId ?? defaultBookId
          const all = availableOutlines.length > 0 ? availableOutlines : getAvailableOutlines(bid)
          const details = batchGetOutlineDetails(oids, all)
          content = JSON.stringify(details.map((d) => ({ title: d.outline.title, chaptersText: d.chaptersText }))).slice(0, 20000)
          break
        }
        case 'getTextOutline': {
          const bid = args.bookId ?? defaultBookId
          const oids = args.outlineIds
          const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
          const entries = collectTextOutlineEntries(bid, oids)
          content = formatTextOutlineForAgent(entries, maxLen)
          break
        }
        case 'editChapterContent': {
          const cid = args.chapterId
          const newContent = typeof args.content === 'string' ? args.content : ''
          if (!cid) {
            content = JSON.stringify({ success: false, error: '缺少 chapterId' })
          } else {
            try {
              getDb().saveArticle(cid, newContent)
              if (sendChunk) sendChunk({ chapterContentUpdated: cid }) // 通知前端刷新章节
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
      sendChunk({ toolIndexCompleted: i })
    }
  }

  return results
}

module.exports = { runTools, collectTextOutlineEntries, formatTextOutlineForAgent }

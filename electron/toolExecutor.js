/**
 * 工具执行器：在后端执行所有 Agent 工具调用（书籍、大纲、章节、人物、记忆、伏笔等）
 * 供 ai-chat-stream 在收到 tool_calls 时内部调用，不发给前端
 */

const Database = require('./database')
const mem0Service = require('./mem0Service')
const { collectTextOutlineEntries } = require('./outlineTextForAgent')

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
    bookId: Number(bookId),
    outlineId: Number(row.id),
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
      ? new Set(outlineIds.map((x) => Number(x)).filter((n) => Number.isFinite(n)))
      : null
  const targetOutlines = filterSet ? all.filter((o) => filterSet.has(Number(o.id))) : all
  const details = includeChapters
    ? batchGetOutlineDetails(
        targetOutlines.map((o) => Number(o.id)),
        all,
      )
    : []
  const byIdDetail = new Map(details.map((d) => [Number(d.outline?.id), d]))
  const textEntries = includeText ? collectTextOutlineEntries(bookId, filterSet ? Array.from(filterSet) : undefined) : []
  const byIdText = new Map(textEntries.map((e) => [Number(e.id), e.markdown]))

  const outlines = targetOutlines.map((o) => {
    const id = Number(o.id)
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
    bookId: Number(bookId),
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
      .map((r) => (r.parent_id == null ? null : Number(r.parent_id)))
      .filter((x) => Number.isFinite(x) && x > 0),
  )
  const items = rows.map((r) => {
    const id = Number(r.id)
    const hasChildren = parentSet.has(id)
    // 有子节点的节点视为“卷”；其余视为“章节”
    const nodeType = hasChildren ? 'volume' : 'chapter'
    return {
      id,
      title: String(r.title || ''),
      parentId: r.parent_id == null ? null : Number(r.parent_id),
      level: Number(r.level || 1),
      sort: Number(r.sort || 0),
      nodeType,
      hasChildren,
    }
  })
  return {
    success: true,
    bookId: Number(bookId),
    writingOutlineId: Number(writing.id),
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
          const data = getChapterContent(cid, title, maxLen)
          if (!data) {
            content = JSON.stringify({
              error: '未找到该章节正文。请确认 chapterId 为左侧写作章节目录对应的章节 id，不要使用总纲/章节大纲/其他大纲树中的节点 id。',
              chapterId: cid,
            })
          } else {
            content = JSON.stringify({ chapterId: data.chapterId, title: data.title, plainText: data.plainText || '（本章暂无正文内容）' })
          }
          break
        }
        case 'listWritingChapters': {
          const bid = args.bookId ?? defaultBookId
          if (!bid || Number.isNaN(Number(bid))) {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法获取写作目录章节列表' })
            break
          }
          const result = listWritingChapters(Number(bid))
          content = JSON.stringify(result).slice(0, 24000)
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
        case 'queryOutline': {
          const bid = args.bookId ?? defaultBookId
          if (!bid || Number.isNaN(Number(bid))) {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法查询大纲' })
            break
          }
          const oids = args.outlineIds
          const includeChapters = args.includeChapters !== false
          const includeText = args.includeText !== false
          const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
          const result = queryOutline(bid, oids, includeChapters, includeText, maxLen)
          console.log('[toolExecutor:queryOutline] done', {
            bookId: Number(bid),
            outlineIds: Array.isArray(oids) ? oids : null,
            total: result.total,
            includeChapters,
            includeText,
            maxTextLength: maxLen,
          })
          content = JSON.stringify(result).slice(0, 24000)
          break
        }
        case 'getGlobalOutline': {
          const bid = args.bookId ?? defaultBookId
          if (!bid || Number.isNaN(Number(bid))) {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法获取总纲' })
            break
          }
          const maxLen = typeof args.maxTextLength === 'number' ? args.maxTextLength : 32000
          const result = getGlobalOutline(bid, maxLen)
          if (!result) {
            content = JSON.stringify({ success: false, error: '获取总纲失败' })
            break
          }
          content = JSON.stringify(result)
          break
        }
        case 'editGlobalOutline': {
          const bid = args.bookId ?? defaultBookId
          if (!bid || Number.isNaN(Number(bid))) {
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
              outlineId: Number(globalOutline.id),
              markdown_content: args.markdownContent,
            })
            content = JSON.stringify({
              success: true,
              bookId: Number(bid),
              outlineId: Number(globalOutline.id),
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
          if (!bid || Number.isNaN(Number(bid))) {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId，无法获取大纲列表' })
            break
          }
          const list = getAvailableOutlines(bid).map((o) => ({
            id: Number(o.id),
            title: o.title || '',
            type: o.type || '',
          }))
          content = JSON.stringify({
            success: true,
            bookId: Number(bid),
            total: list.length,
            outlines: list,
          })
          break
        }
        case 'updateOutline': {
          const bid = args.bookId ?? defaultBookId
          const oid = Number(args.outlineId)
          if (!bid || Number.isNaN(Number(bid))) {
            content = JSON.stringify({ success: false, error: '缺少有效 bookId' })
            break
          }
          if (!Number.isFinite(oid) || oid <= 0) {
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
            .some((o) => Number(o.id) === oid)
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

module.exports = { runTools, collectTextOutlineEntries }

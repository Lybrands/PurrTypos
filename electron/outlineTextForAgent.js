/**
 * 聚合「文本大纲」Markdown（outlines.markdown_content），供 Agent 只读使用。
 * 与 XMind 解析出的章节树（chaptersText）互补；数据来自左侧大纲面板「文本大纲」标签页保存的内容。
 */

const Database = require('./database')

function getDb() {
  return Database
}

const TYPE_LABEL = {
  global: '总纲',
  volume: '卷大纲',
  chapter: '章节大纲',
  other: '其他大纲',
  writing: '写作大纲',
}

/**
 * 收集本书所有在库中已填写 markdown_content 的大纲条目（按界面常见顺序）。
 * @param {number} bookId
 * @param {number[]|undefined} outlineIds 若传入非空数组，仅保留这些大纲 id（且须属于本书）
 * @returns {{ id: number, title: string, type: string, markdown: string }[]}
 */
function collectTextOutlineEntries(bookId, outlineIds) {
  const db = getDb()
  if (bookId == null || Number.isNaN(Number(bookId))) return []

  const filter =
    Array.isArray(outlineIds) && outlineIds.length > 0
      ? new Set(
          outlineIds
            .map((x) => Number(x))
            .filter((n) => !Number.isNaN(n)),
        )
      : null

  const entries = []

  const consider = (row) => {
    if (!row) return
    if (Number(row.book_id) !== Number(bookId)) return
    const md = String(row.markdown_content || '').trim()
    if (!md) return
    if (filter && !filter.has(row.id)) return
    entries.push({
      id: row.id,
      title: row.title || '',
      type: row.type || '',
      markdown: md,
    })
  }

  consider(db.getGlobalOutline(bookId))

  const vols = db.getVolumeOutlines(bookId) || []
  for (const vol of vols) {
    consider(vol)
    for (const ch of vol.chapters || []) consider(ch)
  }

  for (const o of db.getChapterOutlines(bookId) || []) consider(o)
  for (const o of db.getOtherOutlines(bookId) || []) consider(o)

  const writing = db.getWritingOutline(bookId)
  consider(writing)

  if (Array.isArray(outlineIds) && outlineIds.length > 0 && entries.length > 0) {
    const order = new Map(outlineIds.map((id, i) => [Number(id), i]))
    entries.sort((a, b) => (order.get(a.id) ?? 999) - (order.get(b.id) ?? 999))
  }

  return entries
}

/**
 * 格式化为单一纯文本，便于模型阅读。
 * @param {ReturnType<typeof collectTextOutlineEntries>} entries
 * @param {number} [maxLength=32000]
 */
function formatTextOutlineForAgent(entries, maxLength = 32000) {
  if (!entries || entries.length === 0) {
    return (
      '（暂无文本大纲：各大纲的「文本大纲」标签页中尚未填写内容，或 outlineIds 与本书无匹配项。）'
    )
  }
  const parts = entries.map((e) => {
    const label = TYPE_LABEL[e.type] || e.type || '大纲'
    const title = e.title || '未命名'
    return `【${label} · ${title}】大纲ID:${e.id}\n${e.markdown}`
  })
  let text = parts.join('\n\n---\n\n')
  if (text.length > maxLength) {
    text =
      text.slice(0, maxLength) +
      '\n…（已截断；可缩小 outlineIds 范围或调大 maxTextLength 分批获取）'
  }
  return text
}

module.exports = {
  collectTextOutlineEntries,
  formatTextOutlineForAgent,
  TYPE_LABEL,
}

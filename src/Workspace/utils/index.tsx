import type { Article, Chapter, Character, Outline, VolumeOutline } from '../../types'

// ─── 纯文本提取 ────────────────────────────────────────────────

/** 从 Lexical 编辑器 JSON 中提取纯文本 */
export function extractTextFromLexical(json: string): string {
  try {
    const state = JSON.parse(json)
    const extract = (node: { type?: string; text?: string; children?: unknown[] }): string => {
      if (node.type === 'text') return node.text || ''
      if (node.children)
        return (node.children as { type?: string; text?: string; children?: unknown[] }[])
          .map(extract)
          .join('')
      return ''
    }
    return extract(state.root).replace(/\n{3,}/g, '\n\n').trim()
  } catch {
    return json
  }
}

/** 将章节列表格式化为层级文本（用于 AI 上下文） */
export function formatChaptersAsText(chapters: Chapter[]): string {
  if (!chapters.length) return ''
  const buildTree = (parentId: number | null | undefined, depth: number): string => {
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

// ─── 1. 批量获取章节内容 ───────────────────────────────────────

export interface ChapterContent {
  chapterId: number
  title: string
  content: string
  plainText: string
}

/**
 * 批量获取多个章节的文章内容
 * @param chapterIds 章节 ID 列表
 * @param chapterMap 可选的 id→title 映射，若不传则 title 默认为空串
 * @param maxTextLength 每篇纯文本截取的最大长度，默认 12000
 */
export async function batchGetChapterContents(
  chapterIds: number[],
  chapterMap?: Map<number, string> | Record<number, string>,
  maxTextLength = 12000,
): Promise<ChapterContent[]> {
  const titleOf = (id: number) => {
    if (!chapterMap) return ''
    if (chapterMap instanceof Map) return chapterMap.get(id) ?? ''
    return chapterMap[id] ?? ''
  }
  const results = await Promise.all(
    chapterIds.map(async (cid) => {
      const res = await window.electronAPI.getArticle({ chapterId: cid })
      const raw = res.success && res.data?.content ? res.data.content : ''
      const plainText = extractTextFromLexical(raw).slice(0, maxTextLength)
      return { chapterId: cid, title: titleOf(cid), content: raw, plainText }
    }),
  )
  return results
}

/**
 * 获取单个章节内容（便捷方法）
 * 注意：chapterId 必须是「写作大纲」getWritingOutlineWithChapters 返回的 chapters[].id，不能是其他大纲的章节 id。
 */
export async function getChapterContent(
  chapterId: number,
  title?: string,
  maxTextLength = 12000,
): Promise<ChapterContent | null> {
  const res = await window.electronAPI.getArticle({ chapterId })
  if (!res.success || !res.data) return null
  const raw = typeof res.data.content === 'string' ? res.data.content : ''
  const plainText = raw ? extractTextFromLexical(raw).slice(0, maxTextLength) : ''
  return {
    chapterId,
    title: title ?? '',
    content: raw,
    plainText,
  }
}

// ─── 2. 批量获取大纲 ──────────────────────────────────────────

export interface OutlineWithChapters {
  outline: Outline
  chapters: Chapter[]
  chaptersText: string
}

export interface BookOutlines {
  globalOutline: OutlineWithChapters | null
  volumeOutlines: (VolumeOutline & { chapters_detail: OutlineWithChapters[] })[]
  chapterOutlines: OutlineWithChapters[]
  otherOutlines: OutlineWithChapters[]
  writingOutline: OutlineWithChapters | null
}

/** 给单个大纲加载其子章节列表 */
async function loadOutlineWithChapters(outline: Outline): Promise<OutlineWithChapters> {
  const res = await window.electronAPI.getChapters({ outlineId: outline.id })
  const chapters = res.success ? res.data : []
  return { outline, chapters, chaptersText: formatChaptersAsText(chapters) }
}

/**
 * 一次性获取指定书籍的全部大纲（总纲 + 卷大纲 + 章节大纲 + 其他大纲 + 写作大纲）
 */
export async function getAllOutlines(bookId: number): Promise<BookOutlines> {
  const [globalRes, volumeRes, chapterRes, otherRes, writingRes] = await Promise.all([
    window.electronAPI.getGlobalOutline(bookId),
    window.electronAPI.getVolumeOutlines(bookId),
    window.electronAPI.getChapterOutlines(bookId),
    window.electronAPI.getOtherOutlines(bookId),
    window.electronAPI.getWritingOutline(bookId),
  ])

  const globalOutline =
    globalRes.success && globalRes.data ? await loadOutlineWithChapters(globalRes.data) : null

  const volumeOutlines = await Promise.all(
    (volumeRes.success ? volumeRes.data : []).map(async (vol) => {
      const chaptersDetail = await Promise.all(
        (vol.chapters ?? []).map((ch) => loadOutlineWithChapters(ch)),
      )
      return { ...vol, chapters_detail: chaptersDetail }
    }),
  )

  const chapterOutlines = await Promise.all(
    (chapterRes.success ? chapterRes.data : []).map(loadOutlineWithChapters),
  )
  const otherOutlines = await Promise.all(
    (otherRes.success ? otherRes.data : []).map(loadOutlineWithChapters),
  )
  const writingOutline =
    writingRes.success && writingRes.data ? await loadOutlineWithChapters(writingRes.data) : null

  return { globalOutline, volumeOutlines, chapterOutlines, otherOutlines, writingOutline }
}

/**
 * 获取指定大纲 ID 列表的详情（含子章节）
 */
export async function batchGetOutlineDetails(
  outlineIds: number[],
  allOutlines: Outline[],
): Promise<OutlineWithChapters[]> {
  return Promise.all(
    outlineIds.map(async (oid) => {
      const outline = allOutlines.find((o) => o.id === oid) ?? { id: oid, title: '' }
      return loadOutlineWithChapters(outline)
    }),
  )
}

/**
 * 获取指定书籍的可关联大纲列表（总纲 + 章节大纲 + 其他大纲），扁平化
 */
export async function getAvailableOutlines(bookId: number): Promise<Outline[]> {
  const [globalRes, chapterRes, otherRes] = await Promise.all([
    window.electronAPI.getGlobalOutline(bookId),
    window.electronAPI.getChapterOutlines(bookId),
    window.electronAPI.getOtherOutlines(bookId),
  ])
  const list: Outline[] = []
  if (globalRes.success && globalRes.data) list.push(globalRes.data)
  if (chapterRes.success) list.push(...chapterRes.data)
  if (otherRes.success) list.push(...otherRes.data)
  return list
}

/**
 * 获取本书写作大纲及其章节列表（用于 Workspace 加载写作目录）
 */
export async function getWritingOutlineWithChapters(
  bookId: number | null | undefined,
): Promise<{ outlineId: number; chapters: Chapter[] } | null> {
  if (bookId == null) return null
  const res = await window.electronAPI.getWritingOutline(bookId)
  if (!res.success || !res.data) return null
  const oid = Number(res.data.id)
  const chapRes = await window.electronAPI.getChapters({ outlineId: oid })
  const chapters = chapRes.success && chapRes.data ? chapRes.data : []
  return { outlineId: oid, chapters }
}

// ─── 3. 批量获取人物信息 ──────────────────────────────────────

/**
 * 获取指定书籍的全部人物列表
 */
export async function getBookCharacters(bookId: number): Promise<Character[]> {
  const res = await window.electronAPI.getCharacters({ bookId })
  return res.success && res.data ? res.data : []
}

/**
 * 批量获取指定 ID 的人物信息；若 characterIds 为空则返回全部人物
 */
export async function batchGetCharacters(
  bookId: number,
  characterIds?: number[],
): Promise<Character[]> {
  const all = await getBookCharacters(bookId)
  if (!characterIds || characterIds.length === 0) return all
  const idSet = new Set(characterIds)
  return all.filter((c) => idSet.has(c.id))
}

/**
 * 将人物列表格式化为文本摘要（用于 AI 上下文）
 */
export function formatCharactersAsText(characters: Character[]): string {
  if (!characters.length) return ''
  return characters
    .map((c) => {
      const parts: string[] = [`【${c.name}】`]
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

// ─── 4. 获取小说背景内容 ──────────────────────────────────────

export interface StoryBackground {
  content: string
  updateTime?: string
}

/**
 * 获取指定书籍的小说背景文本
 */
export async function getStoryBackground(bookId: number): Promise<StoryBackground | null> {
  const res = await window.electronAPI.getStoryBackground({ bookId })
  if (!res.success || !res.data) return null
  return { content: res.data.content, updateTime: res.data.update_time }
}

// ─── 5. 聚合：获取书籍完整上下文（用于 AI Agent） ──────────────

export interface BookContext {
  currentChapter: ChapterContent | null
  writingOutline: OutlineWithChapters | null
  storyBackground: StoryBackground | null
  characters: Character[]
}

/**
 * 一次性获取 AI Agent 所需的书籍完整上下文：
 *   当前章节正文 + 写作大纲 + 小说背景 + 全部人物
 */
export async function getBookContext(
  bookId: number,
  currentChapterId?: number | null,
  currentChapterTitle?: string,
): Promise<BookContext> {
  const [chapter, outlines, bg, chars] = await Promise.all([
    currentChapterId
      ? getChapterContent(currentChapterId, currentChapterTitle)
      : Promise.resolve(null),
    getAllOutlines(bookId),
    getStoryBackground(bookId),
    getBookCharacters(bookId),
  ])
  return {
    currentChapter: chapter,
    writingOutline: outlines.writingOutline,
    storyBackground: bg,
    characters: chars,
  }
}

/**
 * 将 BookContext 格式化为适合注入到 AI system prompt 的文本
 */
export function formatBookContextForAI(ctx: BookContext): string {
  const parts: string[] = []
  if (ctx.currentChapter?.plainText) {
    parts.push(`【当前正在写的章节：${ctx.currentChapter.title || '当前章节'}】\n${ctx.currentChapter.plainText}`)
  }
  if (ctx.writingOutline?.chaptersText) {
    parts.push(`【本书写作大纲】\n${ctx.writingOutline.chaptersText}`)
  }
  if (ctx.storyBackground?.content) {
    parts.push(`【小说背景】\n${ctx.storyBackground.content}`)
  }
  if (ctx.characters.length > 0) {
    parts.push(`【人物信息】\n${formatCharactersAsText(ctx.characters)}`)
  }
  return parts.length > 0 ? parts.join('\n\n') : ''
}

import { services } from '@/services'
import type { Chapter, Character, EntityId, Outline } from '../../types'

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

// ─── 批量获取章节内容 ─────────────────────────────────────────

export interface ChapterContent {
  chapterId: EntityId
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
  chapterIds: EntityId[],
  chapterMap?: Map<EntityId, string> | Record<string, string>,
  maxTextLength = 12000,
): Promise<ChapterContent[]> {
  const titleOf = (id: EntityId) => {
    if (!chapterMap) return ''
    if (chapterMap instanceof Map) return chapterMap.get(id) ?? ''
    return chapterMap[id] ?? ''
  }
  const results = await Promise.all(
    chapterIds.map(async (cid) => {
      const res = await services.articles.getArticle({ chapterId: cid })
      const raw = res.success && res.data?.content ? res.data.content : ''
      const plainText = extractTextFromLexical(raw).slice(0, maxTextLength)
      return { chapterId: cid, title: titleOf(cid), content: raw, plainText }
    }),
  )
  return results
}

/**
 * 下拉展示用：在扁平关联列表中生成区分重名、标明卷归属的文案。
 */
export function formatAssociableOutlineLabel(
  outline: Outline,
  orderedList: Outline[],
): string {
  const t = (outline.title ?? '').trim() || '未命名'
  if (outline.type === 'volume') {
    return `卷 · ${t}`
  }
  if (outline.type === 'global') {
    return t
  }
  const pid = outline.parent_outline_id
  if (pid != null && String(pid).trim() !== '') {
    const parent = orderedList.find((x) => String(x.id) === String(pid))
    if (parent?.type === 'volume') {
      const pt = (parent.title ?? '').trim() || '卷'
      return `${pt} / ${t}`
    }
    if (parent) {
      return `${(parent.title ?? '').trim() || '大纲'} / ${t}`
    }
  }
  return t
}

/**
 * AI 关联大纲列表（卷/章节大纲）；顺序与左侧大纲面板章节区一致。
 * 数据来自后端 `GET /outlines/associable/{bookId}`。
 */
export async function getAvailableOutlines(bookId: EntityId): Promise<Outline[]> {
  const res = await services.outlines.getAssociableOutlines(bookId)
  return res.success && Array.isArray(res.data) ? res.data : []
}

/**
 * 获取本书写作大纲及其章节列表（用于 Workspace 加载写作目录）
 */
export async function getWritingOutlineWithChapters(
  bookId: EntityId | null | undefined,
): Promise<{ outlineId: EntityId; chapters: Chapter[] } | null> {
  if (bookId == null) return null
  const res = await services.outlines.getWritingOutline(bookId)
  if (!res.success || !res.data) return null
  const oid = String(res.data.id)
  const chapRes = await services.chapters.getChapters({ outlineId: oid })
  const chapters = chapRes.success && chapRes.data ? chapRes.data : []
  return { outlineId: oid, chapters }
}

// ─── 人物信息 ────────────────────────────────────────────────

/**
 * 获取指定书籍的全部人物列表
 */
export async function getBookCharacters(bookId: EntityId): Promise<Character[]> {
  const res = await services.characters.getCharacters({ bookId })
  return res.success && res.data ? res.data : []
}

// ─── 小说背景 ────────────────────────────────────────────────

export interface StoryBackground {
  content: string
  updateTime?: string
}

/**
 * 获取指定书籍的小说背景文本
 */
export async function getStoryBackground(bookId: EntityId): Promise<StoryBackground | null> {
  const res = await services.storyBackground.getStoryBackground({ bookId })
  if (!res.success || !res.data) return null
  return { content: res.data.content, updateTime: res.data.update_time }
}

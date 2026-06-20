/**
 * Inline 改写的「参考资料」拼装。
 *
 * 从用户勾选的关联章节 / 关联大纲 / 记忆 / 伏笔出发，读取各自内容并组装成一段
 * 注入到 user prompt 的纯文本（[参考资料]）。从 InlineEditPopover 抽出，便于复用与
 * 单独维护。仅依赖 window.electronAPI 读取数据，不持有任何组件状态。
 */

import type {
  EntityId,
  Outline,
} from '../../types'

/** 单条关联章节的内容截断阈值，避免 prompt 爆炸 */
export const ASSOCIATED_CHAPTER_CHAR_LIMIT = 2000

export interface BuildInjectedContextParams {
  bookId: EntityId | null
  userPrompt: string
  associatedChapterIds: EntityId[]
  associatedOutlineIds: EntityId[]
  availableOutlines: Outline[]
  chapterSelectOptions: { value: EntityId; label: string }[]
  selectedMemoryIds: (number | string)[]
  selectedForeshadowingIds: (number | string)[]
}

/** 拉取关联章节正文、关联大纲 markdown、记忆/伏笔条目，拼成注入 user prompt 的文本 */
export async function buildInjectedContext({
  bookId,
  userPrompt,
  associatedChapterIds,
  associatedOutlineIds,
  availableOutlines,
  chapterSelectOptions,
  selectedMemoryIds,
  selectedForeshadowingIds,
}: BuildInjectedContextParams): Promise<string> {
  const blocks: string[] = []

  // 关联章节正文
  if (associatedChapterIds.length > 0) {
    const fetched = await Promise.all(
      associatedChapterIds.map(async (id) => {
        const titleOpt = chapterSelectOptions.find(
          (o) => String(o.value) === String(id),
        )
        const title = titleOpt?.label || `章节 ${id}`
        try {
          const res = await window.electronAPI.getArticle({ chapterId: id })
          const content = res.success ? res.data?.content?.trim() ?? '' : ''
          const truncated =
            content.length > ASSOCIATED_CHAPTER_CHAR_LIMIT
              ? content.slice(0, ASSOCIATED_CHAPTER_CHAR_LIMIT) +
                `\n……（已截断，原文约 ${content.length} 字）`
              : content
          return truncated
            ? `《${title}》\n${truncated}`
            : `《${title}》（暂无正文）`
        } catch {
          return `《${title}》（读取失败）`
        }
      }),
    )
    blocks.push(`【关联章节正文】\n${fetched.join('\n\n———\n\n')}`)
  }

  // 关联大纲（直接复用 availableOutlines 中的 markdown_content）
  if (associatedOutlineIds.length > 0) {
    const lines = associatedOutlineIds.map((oid) => {
      const o = availableOutlines.find((x) => String(x.id) === String(oid))
      if (!o) return ''
      const md = (o.markdown_content || '').trim()
      return md ? `《${o.title}》\n${md}` : `《${o.title}》（暂无大纲文本）`
    })
    const joined = lines.filter(Boolean).join('\n\n———\n\n')
    if (joined) blocks.push(`【关联大纲】\n${joined}`)
  }

  // 记忆 / 伏笔：统一交给后端长期记忆编排器生成；未手动选择时也允许按 prompt 自动召回。
  if (bookId != null) {
    try {
      const res = await window.electronAPI.buildMemoryContext({
        bookId,
        userPrompt,
        mode: 'inline',
        selectedMemoryIds,
        selectedForeshadowingIds,
      })
      if (res.success && res.data?.text) {
        blocks.push(res.data.text)
      }
    } catch {
      // ignore
    }
  }

  return blocks.join('\n\n')
}

import { services } from '@/services'
/**
 * Inline 改写/提问的「参考资料」拼装。
 *
 * 上下文块（按序）：
 * - 【当前章节】选区所在章正文，默认全文；超窗只截断时以选区为中心保留最大篇幅
 *   （截断规则见 selectionAnchor.truncateAroundSelection）
 * - 【关联章节正文】用户勾选的其他章节（当前章已单独注入，此处跳过避免重复）
 * - 【关联大纲】勾选的大纲 markdown，与章节同档截断
 * - 记忆/伏笔：后端 /memories/context 统一编排，召回 query 为「选区文本+指令」
 *
 * 仅依赖领域服务读取数据，不持有任何组件状态。
 */

import type {
  AiContextWindow,
  EntityId,
  Outline,
} from '../../types'
import { truncateAroundSelection } from './selectionAnchor'

export interface BuildInjectedContextParams {
  bookId: EntityId | null
  contextWindow: AiContextWindow
  /** 选区所在章（标题 + 编辑器当前文本，含未保存修改）；为空则不注入 */
  currentChapter: { id: EntityId; title: string; text: string } | null
  /** 选区扁平偏移（当前章截断时的保留中心）；无选区时传 null */
  selectionFlat: { start: number; end: number } | null
  /** 记忆召回与上下文对齐用：选中文本 */
  selectionText: string
  /** 用户本轮指令 */
  instruction: string
  associatedChapterIds: EntityId[]
  associatedOutlineIds: EntityId[]
  availableOutlines: Outline[]
  chapterSelectOptions: { value: EntityId; label: string }[]
  selectedLongTermMemoryIds: string[]
  selectedMemoryIds: (number | string)[]
  selectedForeshadowingIds: (number | string)[]
}

function chapterCharLimit(contextWindow: AiContextWindow): number {
  if (contextWindow === '1m') return 12000
  if (contextWindow === '256k' || contextWindow === '300k') return 6000
  return 4000
}

/** 拉取并组装注入 user prompt 的文本；无可注入内容时返回空串 */
export async function buildInjectedContext({
  bookId,
  contextWindow,
  currentChapter,
  selectionFlat,
  selectionText,
  instruction,
  associatedChapterIds,
  associatedOutlineIds,
  availableOutlines,
  chapterSelectOptions,
  selectedLongTermMemoryIds,
  selectedMemoryIds,
  selectedForeshadowingIds,
}: BuildInjectedContextParams): Promise<string> {
  const blocks: string[] = []
  const limit = chapterCharLimit(contextWindow)

  // 当前章节：全文优先，超限以选区为中心截断
  if (currentChapter && currentChapter.text.trim()) {
    const { text } = truncateAroundSelection(
      currentChapter.text,
      selectionFlat?.start ?? 0,
      selectionFlat?.end ?? 0,
      limit,
    )
    blocks.push(`【当前章节】\n《${currentChapter.title}》\n${text}`)
  }

  // 关联章节正文（当前章已单独注入，跳过避免重复）
  const siblingChapterIds = currentChapter
    ? associatedChapterIds.filter((id) => String(id) !== String(currentChapter.id))
    : associatedChapterIds
  if (siblingChapterIds.length > 0) {
    const fetched = await Promise.all(
      siblingChapterIds.map(async (id) => {
        const titleOpt = chapterSelectOptions.find(
          (o) => String(o.value) === String(id),
        )
        const title = titleOpt?.label || `章节 ${id}`
        try {
          const res = await services.articles.getArticle({ chapterId: id })
          const content = res.success ? res.data?.content?.trim() ?? '' : ''
          const truncated =
            content.length > limit
              ? truncateAroundSelection(content, 0, 0, limit).text
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

  // 关联大纲：同档截断
  if (associatedOutlineIds.length > 0) {
    const lines = associatedOutlineIds.map((oid) => {
      const o = availableOutlines.find((x) => String(x.id) === String(oid))
      if (!o) return ''
      const md = (o.markdown_content || '').trim()
      if (!md) return `《${o.title}》（暂无大纲文本）`
      const truncated =
        md.length > limit ? truncateAroundSelection(md, 0, 0, limit).text : md
      return `《${o.title}》\n${truncated}`
    })
    const joined = lines.filter(Boolean).join('\n\n———\n\n')
    if (joined) blocks.push(`【关联大纲】\n${joined}`)
  }

  // 记忆 / 伏笔：统一交给后端长期记忆编排器生成；召回 query 用选区+指令
  if (bookId != null) {
    const memoryQuery = [selectionText.trim(), instruction.trim()]
      .filter(Boolean)
      .join('\n')
    const res = await services.memories.buildMemoryContext({
      bookId,
      operationKey: crypto.randomUUID(),
      userPrompt: memoryQuery,
      mode: 'inline',
      selectedLongTermMemoryIds,
      selectedMemoryIds,
      selectedForeshadowingIds,
      contextWindow,
    })
    if (!res.success) {
      throw new Error(res.error || '记忆上下文不可用')
    }
    if (res.data?.text) {
      blocks.push(res.data.text)
    }
  }

  return blocks.join('\n\n')
}

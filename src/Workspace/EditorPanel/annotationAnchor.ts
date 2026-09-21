/**
 * 批注锚定解析：按引文快照在当前章节全文中重定位批注范围。
 * 原位文本未变则用原位偏移；否则按引文全文检索（取离原位最近的匹配）；
 * 都失败视为漂移（drifted），渲染层不画高亮、列表显示漂移标记。
 */

import { findAnchorStart } from './selectionAnchor'
import type { ChapterAnnotation } from '../../types'

export interface AnnotationAnchorRange {
  start: number
  end: number
}

export function resolveAnnotationRange(
  flatText: string,
  annotation: Pick<ChapterAnnotation, 'start_offset' | 'end_offset' | 'quoted_text'>,
): AnnotationAnchorRange | null {
  const quoted = annotation.quoted_text
  if (!quoted.trim()) return null
  if (
    annotation.start_offset >= 0 &&
    annotation.end_offset > annotation.start_offset &&
    flatText.slice(annotation.start_offset, annotation.end_offset) === quoted
  ) {
    return { start: annotation.start_offset, end: annotation.end_offset }
  }
  const trimmed = quoted.trim()
  const relocated = findAnchorStart(flatText, trimmed, annotation.start_offset)
  if (relocated == null) return null
  return { start: relocated, end: relocated + trimmed.length }
}

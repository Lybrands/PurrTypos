/**
 * 章节批注列表抽屉：待处理（open）在前、已解决（resolved）在后，
 * 逐条显示引文与批注；点击条目定位到正文并打开详情弹层。
 * 漂移（原文变更后按引文找不到）的条目显示灰色标记，点击只开详情。
 */

import { PurrButton, PurrDrawer, PurrEmpty, PurrTag } from '@/purr-components'
import { HighlightIcon } from '@/purr-components'
import type { ChapterAnnotation, EntityId } from '../../types'
import { resolveAnnotationRange } from './annotationAnchor'

interface AnnotationListDrawerProps {
  open: boolean
  onClose: () => void
  chapterId: EntityId | null
  chapterTitle: string
  annotations: ChapterAnnotation[]
  /** 编辑器当前全文，用于判断批注是否漂移 */
  flatText: string
  onOpenDetail: (annotation: ChapterAnnotation, drifted: boolean) => void
}

export default function AnnotationListDrawer({
  open,
  onClose,
  chapterId,
  chapterTitle,
  annotations,
  flatText,
  onOpenDetail,
}: AnnotationListDrawerProps) {
  if (chapterId == null) return null

  const withDrift = annotations.map((a) => ({
    annotation: a,
    drifted: resolveAnnotationRange(flatText, a) == null,
  }))
  const openItems = withDrift.filter((x) => x.annotation.status === 'open')
  const resolvedItems = withDrift.filter((x) => x.annotation.status === 'resolved')

  return (
    <PurrDrawer
      title={
        <span>
          <HighlightIcon style={{ marginRight: 8 }} />
          批注 · {chapterTitle}
        </span>
      }
      open={open}
      onClose={onClose}
      width={420}
    >
      <div className="annotation-drawer-body">
        {annotations.length === 0 && (
          <PurrEmpty description="暂无批注；在正文中选中文字即可添加" />
        )}
        {openItems.length > 0 && (
          <div className="annotation-drawer-group">待处理（{openItems.length}）</div>
        )}
        {openItems.map(({ annotation, drifted }) => (
          <AnnotationRow
            key={annotation.id}
            annotation={annotation}
            drifted={drifted}
            onOpenDetail={onOpenDetail}
          />
        ))}
        {resolvedItems.length > 0 && (
          <div className="annotation-drawer-group">已解决（{resolvedItems.length}）</div>
        )}
        {resolvedItems.map(({ annotation, drifted }) => (
          <AnnotationRow
            key={annotation.id}
            annotation={annotation}
            drifted={drifted}
            onOpenDetail={onOpenDetail}
          />
        ))}
      </div>
    </PurrDrawer>
  )
}

function AnnotationRow({
  annotation,
  drifted,
  onOpenDetail,
}: {
  annotation: ChapterAnnotation
  drifted: boolean
  onOpenDetail: (annotation: ChapterAnnotation, drifted: boolean) => void
}) {
  const quote = annotation.quoted_text.replace(/\s+/g, ' ').trim()
  return (
    <PurrButton
      type="text"
      className={`annotation-drawer-row${annotation.status === 'resolved' ? ' is-resolved' : ''}`}
      onClick={() => onOpenDetail(annotation, drifted)}
    >
      <div className="annotation-drawer-quote" title={annotation.quoted_text}>
        {quote.length > 60 ? `${quote.slice(0, 60)}…` : quote || '（空引文）'}
      </div>
      <div className="annotation-drawer-note">{annotation.note}</div>
      <div className="annotation-drawer-meta">
        {drifted && <PurrTag color="warning">原文已变更</PurrTag>}
        {annotation.source === 'ai' && <PurrTag>AI</PurrTag>}
        <span className="annotation-drawer-time">{annotation.update_time || annotation.create_time || ''}</span>
      </div>
    </PurrButton>
  )
}

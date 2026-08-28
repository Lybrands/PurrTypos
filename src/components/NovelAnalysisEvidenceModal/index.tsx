import React from 'react'
import {
  BookIcon,
  PurrButton,
  PurrModal,
  PurrSpin,
} from '@/purr-components'
import type { NovelAnalysisEvidence } from '@/types'
import './index.scss'

export interface NovelSourceEvidenceTarget {
  revisionId: string
  sectionId: string
  start: number
  excerpt: string
}

export function NovelAnalysisEvidenceList({
  evidence,
  loading = false,
  sourceRevisionId = '',
  onLocate,
}: {
  evidence: NovelAnalysisEvidence[]
  loading?: boolean
  sourceRevisionId?: string
  onLocate?(target: NovelSourceEvidenceTarget): void
}) {
  if (loading) return <div className="novel-evidence-state"><PurrSpin /></div>
  if (!evidence.length) return <div className="novel-evidence-state">没有可查看的原文证据。</div>

  return <div className="novel-evidence-list">
    {evidence.map((item, index) => <article key={`${item.sectionId}:${item.locator?.start ?? index}`}>
      <header>
        <div>
          <span>原文证据 {index + 1}</span>
          <strong>{item.sectionTitle || (item.sectionOrdinal != null ? `第 ${item.sectionOrdinal + 1} 节` : '原文章节')}</strong>
        </div>
        {onLocate && sourceRevisionId ? <PurrButton
          size="small"
          icon={<BookIcon />}
          onClick={() => onLocate({
            revisionId: sourceRevisionId,
            sectionId: item.sectionId,
            start: Number(item.locator?.start ?? 0),
            excerpt: item.excerpt,
          })}
        >定位原文</PurrButton> : null}
      </header>
      <blockquote>{item.excerpt}</blockquote>
    </article>)}
  </div>
}

export default function NovelAnalysisEvidenceModal({
  open,
  title,
  evidence,
  loading = false,
  sourceRevisionId = '',
  onClose,
  onLocate,
}: {
  open: boolean
  title: string
  evidence: NovelAnalysisEvidence[]
  loading?: boolean
  sourceRevisionId?: string
  onClose(): void
  onLocate?(target: NovelSourceEvidenceTarget): void
}) {
  return <PurrModal
    title={title}
    open={open}
    width="min(760px, calc(100vw - 32px))"
    footer={null}
    onCancel={onClose}
    styles={{ body: { maxHeight: 'min(70vh, 680px)', overflowY: 'auto' } }}
  >
    <NovelAnalysisEvidenceList
      evidence={evidence}
      loading={loading}
      sourceRevisionId={sourceRevisionId}
      onLocate={onLocate}
    />
  </PurrModal>
}

/**
 * 批注编辑弹层：选中文字 → 工具条「批注」（手动）或 Inline 提问「存为批注」
 * （AI 答案预填）打开。保存锚 = 创建时刻的扁平偏移 + 引文/前后文快照，
 * 原文后续变更由渲染层按引文重定位（见 selectionAnchor）。
 */

import React from 'react'
import { CheckIcon, CloseIcon, PurrButton, usePurrToast } from '@/purr-components'
import { services } from '@/services'
import type { EntityId } from '../../types'
import { bumpAnnotationsRevision } from '../../stores/annotationsStore'
import type { InlineCapture } from './InlineEditPopover'

export interface AnnotationDraft {
  capture: InlineCapture
  initialNote: string
  source: 'manual' | 'ai'
}

interface AnnotationComposerProps {
  bookId: EntityId | null
  chapterId: EntityId | null
  draft: AnnotationDraft
  /** 读取编辑器当前全文，用于前后文快照 */
  getFlatText: () => string
  onClose: () => void
}

const CONTEXT_CHARS = 80

export default function AnnotationComposerPopover({
  bookId,
  chapterId,
  draft,
  getFlatText,
  onClose,
}: AnnotationComposerProps) {
  const appMessage = usePurrToast()
  const [note, setNote] = React.useState(draft.initialNote)
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const textareaRef = React.useRef<HTMLTextAreaElement>(null)

  React.useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSave()
    }
  }

  const handleSave = async () => {
    const trimmed = note.trim()
    if (!trimmed || saving) return
    if (!bookId || !chapterId) {
      setError('缺少书籍或章节上下文，无法保存批注')
      return
    }
    setSaving(true)
    setError(null)
    const { capture } = draft
    const fullText = getFlatText()
    const before = fullText.slice(Math.max(0, capture.flatStart - CONTEXT_CHARS), capture.flatStart)
    const after = fullText.slice(capture.flatEnd, capture.flatEnd + CONTEXT_CHARS)
    const res = await services.annotations.addAnnotation({
      bookId,
      chapterId,
      startOffset: capture.flatStart,
      endOffset: capture.flatEnd,
      quotedText: capture.text,
      note: trimmed,
      contextBefore: before,
      contextAfter: after,
      source: draft.source,
    })
    setSaving(false)
    if (!res.success) {
      setError(res.error || '批注保存失败')
      return
    }
    bumpAnnotationsRevision(bookId, chapterId)
    appMessage.success('批注已保存')
    onClose()
  }

  return (
    <div
      className="annotation-popover inline-edit-popover"
      style={{ position: 'fixed', top: '20vh', left: '50%', transform: 'translateX(-50%)' }}
      onKeyDown={handleKeyDown}
    >
      <div className="inline-edit-popover-header">
        <span>{draft.source === 'ai' ? '存为批注（AI 回答）' : '添加批注'}</span>
        <PurrButton
          type="text"
          size="small"
          icon={<CloseIcon style={{ fontSize: 14 }} />}
          onClick={onClose}
        />
      </div>
      <div className="inline-edit-popover-body">
        <div className="inline-edit-popover-origin" title="锚定的选中文本">
          {draft.capture.text}
        </div>
        <div className="annotation-popover-input-wrap">
          <textarea
            ref={textareaRef}
            className="inline-edit-popover-textarea"
            placeholder="写下这段文字的批注... (Ctrl+Enter 保存)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={4}
            disabled={saving}
          />
        </div>
        {error && <div className="inline-edit-popover-error">{error}</div>}
      </div>
      <div className="inline-edit-popover-footer">
        <div className="inline-edit-popover-footer-left" />
        <div className="inline-edit-popover-footer-right">
          <PurrButton
            type="primary"
            size="small"
            icon={<CheckIcon />}
            onClick={handleSave}
            disabled={!note.trim() || saving}
          >
            {saving ? '保存中...' : '保存批注'}
          </PurrButton>
        </div>
      </div>
    </div>
  )
}

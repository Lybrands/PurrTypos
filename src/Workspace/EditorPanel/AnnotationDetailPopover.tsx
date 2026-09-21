/**
 * 批注详情弹层：点击正文高亮或列表项打开。编辑批注内容、
 * 切换 open/resolved、删除。锚定的原文只读展示。
 */

import React from 'react'
import { CheckIcon, CloseIcon, DeleteIcon, PurrButton, usePurrToast } from '@/purr-components'
import type { ChapterAnnotation } from '../../types'

interface AnnotationDetailProps {
  annotation: ChapterAnnotation
  drifted: boolean
  onClose: () => void
  onUpdate: (id: number, data: { note?: string; status?: 'open' | 'resolved' }) => Promise<boolean>
  onDelete: (id: number) => Promise<boolean>
  onLocate?: (annotation: ChapterAnnotation) => void
}

export default function AnnotationDetailPopover({
  annotation,
  drifted,
  onClose,
  onUpdate,
  onDelete,
  onLocate,
}: AnnotationDetailProps) {
  const appMessage = usePurrToast()
  const [note, setNote] = React.useState(annotation.note)
  const [saving, setSaving] = React.useState(false)

  React.useEffect(() => {
    setNote(annotation.note)
  }, [annotation])

  const saveNote = async () => {
    if (saving) return
    setSaving(true)
    const ok = await onUpdate(annotation.id, { note: note.trim() || annotation.note })
    setSaving(false)
    if (ok) appMessage.success('批注已更新')
    else appMessage.error('批注更新失败')
  }

  const toggleStatus = async () => {
    const next = annotation.status === 'open' ? 'resolved' : 'open'
    const ok = await onUpdate(annotation.id, { status: next })
    if (!ok) appMessage.error('状态更新失败')
  }

  const remove = async () => {
    const ok = await onDelete(annotation.id)
    if (ok) {
      appMessage.success('批注已删除')
      onClose()
    } else {
      appMessage.error('批注删除失败')
    }
  }

  return (
    <div
      className="annotation-popover inline-edit-popover annotation-detail-popover"
      style={{ position: 'fixed', top: '20vh', left: '50%', transform: 'translateX(-50%)' }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation()
          onClose()
        }
      }}
    >
      <div className="inline-edit-popover-header">
        <span>
          批注详情
          {annotation.source === 'ai' && <em className="annotation-source-tag">AI</em>}
        </span>
        <PurrButton
          type="text"
          size="small"
          icon={<CloseIcon style={{ fontSize: 14 }} />}
          onClick={onClose}
        />
      </div>
      <div className="inline-edit-popover-body">
        <div className="inline-edit-popover-origin" title="锚定的选中文本">
          {annotation.quoted_text}
        </div>
        {drifted && (
          <div className="annotation-drifted-tip">原文已变更，暂无法在正文中定位该批注</div>
        )}
        <div className="annotation-popover-input-wrap">
          <textarea
            className="inline-edit-popover-textarea"
            placeholder="批注内容..."
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={4}
            disabled={saving}
          />
        </div>
      </div>
      <div className="inline-edit-popover-footer">
        <div className="inline-edit-popover-footer-left">
          {onLocate && !drifted && (
            <PurrButton type="text" size="small" onClick={() => onLocate(annotation)}>
              定位原文
            </PurrButton>
          )}
          <PurrButton
            type="text"
            size="small"
            icon={<DeleteIcon size={15} />}
            onClick={remove}
          >
            删除
          </PurrButton>
        </div>
        <div className="inline-edit-popover-footer-right">
          <PurrButton
            type="text"
            size="small"
            onClick={toggleStatus}
          >
            {annotation.status === 'open' ? '标记解决' : '重新打开'}
          </PurrButton>
          <PurrButton
            type="primary"
            size="small"
            icon={<CheckIcon />}
            onClick={saveNote}
            disabled={saving || !note.trim() || note.trim() === annotation.note}
          >
            保存
          </PurrButton>
        </div>
      </div>
    </div>
  )
}

import React from 'react'
import { App as AntdApp, Alert, Button, Modal, Spin } from 'antd'
import { FileAddOutlined, ExportOutlined } from '@ant-design/icons'
import type { Chapter, EntityId, Outline } from '../../types'
import OutlineMarkdownPane, { type OutlineMarkdownPaneRef } from '../OutlinePanel/OutlineMarkdownPane'
import './ChapterOutlineModal.scss'

/** 弹窗承载的大纲类型 */
export type OutlineModalMode = 'chapter' | 'volume' | 'global'

export interface ChapterOutlineModalTarget {
  mode: OutlineModalMode
  /** chapter / volume 模式下传入对应的写作条目；global 模式不需要 */
  chapter?: Chapter | null
  /** 标题前缀展示用；不传时按 mode + chapter.title 拼 */
  titleOverride?: string
}

export interface ChapterOutlineModalProps {
  open: boolean
  target: ChapterOutlineModalTarget | null
  bookId: EntityId | null | undefined
  onClose: () => void
  /** 创建/更新成功后通知外层（用于关联状态刷新等） */
  onChanged?: () => void
}

/**
 * 大纲弹窗：合并自原「大纲」分组，承载章节 / 卷 / 总纲三类大纲的查看与编辑。
 *
 * 设计：
 * - 主流程：编辑「文本大纲」（markdown_content）
 * - 没有大纲记录时一键创建：
 *   - chapter / volume → `saveOutline({type, writing_chapter_id, book_id})`
 *   - global → `ensureGlobalOutline(bookId)`
 * - 已有 XMind 大纲时显示提示条 + 「打开源文件」入口（XMind 上传/换图保留给后续）
 * - 关闭前自动 flushSave，保证草稿不丢
 */
export default function ChapterOutlineModal({
  open,
  target,
  bookId,
  onClose,
  onChanged,
}: ChapterOutlineModalProps) {
  const { message: appMessage } = AntdApp.useApp()
  const [loading, setLoading] = React.useState(false)
  const [outline, setOutline] = React.useState<Outline | null>(null)
  const [error, setError] = React.useState<string>('')
  const [creating, setCreating] = React.useState(false)
  const [openingSource, setOpeningSource] = React.useState(false)
  const paneRef = React.useRef<OutlineMarkdownPaneRef | null>(null)

  const mode = target?.mode ?? 'chapter'
  const chapter = target?.chapter ?? null

  const titleLabel = React.useMemo(() => {
    if (target?.titleOverride) return target.titleOverride
    if (mode === 'global') return '总纲'
    if (mode === 'volume') return chapter?.title || '卷大纲'
    return chapter?.title || '章节大纲'
  }, [target, mode, chapter])

  const reload = React.useCallback(async () => {
    setError('')
    setLoading(true)
    try {
      if (mode === 'global') {
        const res = await window.electronAPI.getGlobalOutline(bookId)
        if (res.success) setOutline(res.data ?? null)
        else { setError(res.error || '加载总纲失败'); setOutline(null) }
        return
      }

      if (mode === 'volume' && chapter) {
        const res = await window.electronAPI.getVolumeOutlines(bookId)
        if (res.success) {
          const list = res.data ?? []
          const found =
            list.find(
              (o) => o.writing_chapter_id != null && String(o.writing_chapter_id) === String(chapter.id),
            ) ??
            list.find((o) => o.title === chapter.title) ??
            null
          setOutline(found)
        } else { setError(res.error || '加载卷大纲失败'); setOutline(null) }
        return
      }

      // chapter 模式
      if (chapter) {
        const res = await window.electronAPI.getChapterOutlines(bookId)
        if (res.success) {
          const list = res.data ?? []
          const found =
            list.find(
              (o) => o.writing_chapter_id != null && String(o.writing_chapter_id) === String(chapter.id),
            ) ??
            list.find((o) => o.title === chapter.title) ??
            null
          setOutline(found)
        } else { setError(res.error || '加载章节大纲失败'); setOutline(null) }
      }
    } catch (err) {
      setError('加载大纲失败：' + (err instanceof Error ? err.message : String(err)))
      setOutline(null)
    } finally {
      setLoading(false)
    }
  }, [mode, chapter, bookId])

  React.useEffect(() => {
    if (!open) return
    void reload()
  }, [open, reload])

  const handleCreateOutline = React.useCallback(async () => {
    if (creating) return
    setCreating(true)
    try {
      if (mode === 'global') {
        const res = await window.electronAPI.ensureGlobalOutline(bookId)
        if (res.success && res.data) {
          setOutline(res.data)
          onChanged?.()
        } else {
          appMessage.error(res.error || '创建总纲失败')
        }
        return
      }

      if (!chapter) return
      const res = await window.electronAPI.saveOutline({
        title: chapter.title,
        type: mode,
        book_id: bookId ?? null,
        writing_chapter_id: chapter.id,
      })
      if (res.success) {
        setOutline(res.data)
        onChanged?.()
      } else {
        appMessage.error(res.error || '创建大纲失败')
      }
    } finally {
      setCreating(false)
    }
  }, [mode, chapter, bookId, creating, appMessage, onChanged])

  const handleOpenSource = React.useCallback(async () => {
    if (!outline?.file_path) return
    setOpeningSource(true)
    try {
      const res = await window.electronAPI.openFilePath(outline.file_path)
      if (!res.success) appMessage.error('打开失败：' + (res.error || ''))
    } finally {
      setOpeningSource(false)
    }
  }, [outline?.file_path, appMessage])

  const handleClose = React.useCallback(async () => {
    try {
      await paneRef.current?.flushSave()
    } finally {
      onClose()
    }
  }, [onClose])

  const titlePrefix = mode === 'global' ? '总纲' : mode === 'volume' ? '卷大纲' : '大纲'

  return (
    <Modal
      open={open}
      onCancel={handleClose}
      title={
        <span className="chapter-outline-modal-title">
          {titlePrefix} · <span className="chapter-outline-modal-chapter">{titleLabel}</span>
        </span>
      }
      footer={null}
      width={760}
      destroyOnClose
      className="chapter-outline-modal"
      styles={{ body: { padding: 0 } }}
    >
      <div className="chapter-outline-modal-body">
        {error && (
          <Alert
            type="error"
            message={error}
            showIcon
            closable
            onClose={() => setError('')}
            className="chapter-outline-modal-alert"
          />
        )}

        {loading ? (
          <div className="chapter-outline-modal-center">
            <Spin tip="加载中…" />
          </div>
        ) : !outline ? (
          <div className="chapter-outline-modal-empty">
            <FileAddOutlined className="chapter-outline-modal-empty-icon" />
            <p className="chapter-outline-modal-empty-title">
              {mode === 'global' ? '暂无总纲' : mode === 'volume' ? '该卷暂无大纲' : '该章节暂无大纲'}
            </p>
            <p className="chapter-outline-modal-empty-desc">
              新建后即可在此弹窗内编辑文本大纲；如需 XMind 思维导图大纲，可上传 .xmind 文件。
            </p>
            <Button
              type="primary"
              icon={<FileAddOutlined />}
              loading={creating}
              onClick={() => void handleCreateOutline()}
            >
              新建文本大纲
            </Button>
          </div>
        ) : (
          <>
            {outline.file_path && (
              <Alert
                type="info"
                showIcon
                className="chapter-outline-modal-alert"
                message={
                  <span>
                    该大纲有 XMind 思维导图文件，弹窗只能编辑文本部分。
                    <Button
                      type="link"
                      size="small"
                      icon={<ExportOutlined />}
                      loading={openingSource}
                      onClick={handleOpenSource}
                    >
                      打开源文件
                    </Button>
                  </span>
                }
              />
            )}
            <div className="chapter-outline-modal-pane">
              <OutlineMarkdownPane
                ref={paneRef}
                key={String(outline.id)}
                outlineId={outline.id}
                markdownContent={outline.markdown_content ?? null}
                onSaved={() => {
                  onChanged?.()
                  void reload()
                }}
              />
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}

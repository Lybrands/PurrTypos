import React from 'react'
import { PlusOutlined, ArrowLeftOutlined, DeleteOutlined, EditOutlined, ExportOutlined } from '../ui'
import { Button, Checkbox, Input, Modal, Tooltip } from '../ui'
import { Book, type EntityId } from '../types'
import { useAppFeedback } from '../hooks/useAppFeedback'
import AppHeader from '../components/AppHeader'
import ExportModal, { type ExportFormat } from '../components/ExportModal'
import { fetchExportData, buildExportEntries, buildSingleTxtContent } from '../utils/exportBooks'
import './index.scss'

interface BookshelfPageProps {
  books: Book[]
  lastOpenedBookId: EntityId | null
  onOpenBook: (book: Book) => void
  onCreateBook: (title: string, enableVolume?: boolean) => void
  onDeleteBook: (bookId: EntityId) => void
  onRenameBook: (bookId: EntityId, title: string) => void
  onBack: () => void
}

export default function BookshelfPage({
  books,
  lastOpenedBookId,
  onOpenBook,
  onCreateBook,
  onDeleteBook,
  onRenameBook,
  onBack,
}: BookshelfPageProps) {
  const { message } = useAppFeedback()
  const [createModalOpen, setCreateModalOpen] = React.useState(false)
  const [createTitle, setCreateTitle] = React.useState('')
  const [createEnableVolume, setCreateEnableVolume] = React.useState(false)
  const [renameModalOpen, setRenameModalOpen] = React.useState(false)
  const [renameTarget, setRenameTarget] = React.useState<Book | null>(null)
  const [renameTitle, setRenameTitle] = React.useState('')
  const [deleteTarget, setDeleteTarget] = React.useState<Book | null>(null)
  const [exportModalOpen, setExportModalOpen] = React.useState(false)
  const [exportSelectedIds, setExportSelectedIds] = React.useState<EntityId[]>([])
  const [exporting, setExporting] = React.useState(false)

  const handleCreate = React.useCallback(() => {
    const t = createTitle.trim()
    if (!t) { message.warning('书名不能为空'); return }
    onCreateBook(t, createEnableVolume)
    setCreateTitle('')
    setCreateEnableVolume(false)
    setCreateModalOpen(false)
  }, [createTitle, createEnableVolume, onCreateBook, message])

  const handleRename = React.useCallback(() => {
    if (!renameTarget) return
    const t = renameTitle.trim()
    if (!t) { message.warning('书名不能为空'); return }
    onRenameBook(renameTarget.id, t)
    setRenameModalOpen(false)
    setRenameTarget(null)
  }, [renameTarget, renameTitle, onRenameBook, message])

  const handleDeleteConfirm = React.useCallback(() => {
    if (!deleteTarget) return
    onDeleteBook(deleteTarget.id)
    setDeleteTarget(null)
  }, [deleteTarget, onDeleteBook])

  const openRename = React.useCallback((e: React.MouseEvent, book: Book) => {
    e.stopPropagation()
    setRenameTarget(book)
    setRenameTitle(book.title)
    setRenameModalOpen(true)
  }, [])

  const openDelete = React.useCallback((e: React.MouseEvent, book: Book) => {
    e.stopPropagation()
    setDeleteTarget(book)
  }, [])

  const openExport = React.useCallback(() => {
    setExportSelectedIds([])
    setExportModalOpen(true)
  }, [])

  const handleExportConfirm = React.useCallback(
    async (selectedIds: EntityId[], format: ExportFormat, exportAsZip: boolean) => {
      if (selectedIds.length === 0) {
        message.warning('请至少选择一本书籍')
        return
      }
      setExporting(true)
      try {
        // EPUB：逐本书走「后端生成 + 保存对话框」
        if (format === 'epub') {
          let okCount = 0
          for (const bookId of selectedIds) {
            const book = books.find((b) => b.id === bookId)
            const res = await window.electronAPI.exportEpub({
              bookId,
              defaultName: book?.title || '书籍',
            })
            if (res.success) okCount += 1
            else if (res.error === 'canceled') break
            else message.error(`「${book?.title || bookId}」导出失败：${res.error || '未知错误'}`)
          }
          if (okCount > 0) {
            message.success(`已导出 ${okCount} 本 EPUB`)
            setExportModalOpen(false)
          }
          return
        }

        const booksData = await fetchExportData(selectedIds)

        // 整本 TXT：逐本书保存为单文件
        if (format === 'txt-single') {
          let okCount = 0
          for (const bookData of booksData) {
            if (bookData.chapters.length === 0) {
              message.warning(`「${bookData.title}」暂无内容，已跳过`)
              continue
            }
            const res = await window.electronAPI.writeSingleTextFile({
              defaultName: `${bookData.title || '导出'}.txt`,
              content: buildSingleTxtContent(bookData),
            })
            if (res.success) okCount += 1
            else if (res.error === 'canceled') break
            else message.error(`「${bookData.title}」导出失败：${res.error || '未知错误'}`)
          }
          if (okCount > 0) {
            message.success(`已导出 ${okCount} 本 TXT`)
            setExportModalOpen(false)
          }
          return
        }

        const entries = buildExportEntries(booksData, format)
        if (entries.length === 0) {
          message.warning('所选书籍暂无内容可导出')
          return
        }
        const res = await window.electronAPI.writeExportFiles({ entries, exportAsZip })
        if (res.success) {
          message.success('导出成功')
          setExportModalOpen(false)
        } else {
          if (res.error !== 'canceled') message.error(res.error || '导出失败')
        }
      } finally {
        setExporting(false)
      }
    },
    [message, books]
  )

  return (
    <div className="bookshelf-page">
      <AppHeader
        title="我的书架"
        left={
          <Tooltip title="返回首页">
            <Button
              type="text"
              size="small"
              icon={<ArrowLeftOutlined style={{ fontSize: 14 }} />}
              onClick={onBack}
            />
          </Tooltip>
        }
        right={
          <Tooltip title="导出书籍">
            <Button
              type="text"
              size="small"
              icon={<ExportOutlined style={{ fontSize: 16 }} />}
              onClick={openExport}
              className="app-header-action-btn"
            />
          </Tooltip>
        }
        showActions
      />

      <section className="bookshelf-main">
        <div className="bookshelf-toolbar">
          <div>
            <span className="bookshelf-eyebrow">YOUR STORIES</span>
            <h1>作品书架</h1>
            <p>{books.length > 0 ? `共 ${books.length} 部作品，挑一本继续创作吧。` : '从一个书名开始，写下你的第一部作品。'}</p>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
            新建书籍
          </Button>
        </div>

        <div className="bookshelf-list">
          {books.map((book) => {
            const isLastOpened = book.id === lastOpenedBookId
            return (
              <div
                key={book.id}
                className={`book-card${isLastOpened ? ' is-last-opened' : ''}`}
              >
                <button
                  type="button"
                  className="book-card-open"
                  aria-label={`打开《${book.title}》${isLastOpened ? '，上次打开' : ''}`}
                  onClick={() => onOpenBook(book)}
                >
                  <span className="book-cover" style={{ '--book-color': book.cover_color || '#c94361' } as React.CSSProperties}>
                    <span className="book-spine" style={{ background: book.cover_color || '#c94361' }} />
                    <span className="book-cover-brand">PURR TYPOS</span>
                    <span className="book-cover-title">{book.title}</span>
                    {isLastOpened && <span className="book-last-opened-badge">上次打开</span>}
                    <span className="book-cover-mark">✦</span>
                  </span>
                  <span className="book-title">{book.title}</span>
                </button>
                <div className="book-actions">
                  <Tooltip title="重命名">
                    <button type="button" aria-label={`重命名《${book.title}》`} className="book-action-btn" onClick={(e) => openRename(e, book)}>
                      <EditOutlined />
                    </button>
                  </Tooltip>
                  <Tooltip title="删除书籍">
                    <button type="button" aria-label={`删除《${book.title}》`} className="book-action-btn danger" onClick={(e) => openDelete(e, book)}>
                      <DeleteOutlined />
                    </button>
                  </Tooltip>
                </div>
              </div>
            )
          })}

          <button type="button" className="book-card book-card-add" onClick={() => setCreateModalOpen(true)}>
            <span className="book-card-add-inner">
              <span className="book-add-icon"><PlusOutlined /></span>
              <strong>新建书籍</strong>
              <small>让一个新故事从这里开始</small>
            </span>
          </button>
        </div>
      </section>

      {/* 新建书籍 */}
      <Modal
        title="新建书籍"
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => { setCreateModalOpen(false); setCreateTitle(''); setCreateEnableVolume(false) }}
        okText="创建"
        cancelText="取消"
      >
        <Input
          placeholder="请输入书名"
          value={createTitle}
          onChange={(e) => setCreateTitle(e.target.value)}
          onPressEnter={handleCreate}
          maxLength={50}
          autoFocus
          style={{ marginBottom: 12 }}
        />
        <Checkbox
          checked={createEnableVolume}
          onChange={(e) => setCreateEnableVolume(e.target.checked)}
        >
          文章分「卷」（创建后不可修改）
        </Checkbox>
      </Modal>

      {/* 重命名 */}
      <Modal
        title="重命名书籍"
        open={renameModalOpen}
        onOk={handleRename}
        onCancel={() => { setRenameModalOpen(false); setRenameTarget(null) }}
        okText="保存"
        cancelText="取消"
      >
        <Input
          placeholder="请输入新书名"
          value={renameTitle}
          onChange={(e) => setRenameTitle(e.target.value)}
          onPressEnter={handleRename}
          maxLength={50}
          autoFocus
        />
      </Modal>

      {/* 删除确认 */}
      <Modal
        title="删除书籍"
        open={!!deleteTarget}
        onOk={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>
          确认删除《{deleteTarget?.title}》？此操作将删除该书的所有章节和大纲，且不可恢复。
        </p>
      </Modal>

      <ExportModal
        title="导出书籍"
        open={exportModalOpen}
        onCancel={() => setExportModalOpen(false)}
        items={books.map((b) => ({ id: b.id, title: b.title }))}
        selectedIds={exportSelectedIds}
        onSelectedIdsChange={setExportSelectedIds}
        onConfirm={handleExportConfirm}
        confirmLoading={exporting}
        selectLabel="选择书籍（可多选）："
        emptyText="暂无书籍"
        showFolderHint
      />
    </div>
  )
}

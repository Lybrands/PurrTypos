import React from 'react'
import { PlusOutlined, ArrowLeftOutlined, DeleteOutlined, EditOutlined, ExportOutlined } from '@ant-design/icons'
import { Button, Modal, Input, Tooltip, Checkbox } from 'antd'
import { Book } from '../types'
import { useAntdApp } from '../hooks/useAntdApp'
import AppHeader from '../components/AppHeader'
import ExportModal from '../components/ExportModal'
import { fetchExportData, buildExportEntries } from '../utils/exportBooks'
import './index.scss'

interface BookshelfPageProps {
  books: Book[]
  onOpenBook: (book: Book) => void
  onCreateBook: (title: string, enableVolume?: boolean) => void
  onDeleteBook: (bookId: number) => void
  onRenameBook: (bookId: number, title: string) => void
  onBack: () => void
}

export default function BookshelfPage({
  books,
  onOpenBook,
  onCreateBook,
  onDeleteBook,
  onRenameBook,
  onBack,
}: BookshelfPageProps) {
  const { message } = useAntdApp()
  const [createModalOpen, setCreateModalOpen] = React.useState(false)
  const [createTitle, setCreateTitle] = React.useState('')
  const [createEnableVolume, setCreateEnableVolume] = React.useState(false)
  const [renameModalOpen, setRenameModalOpen] = React.useState(false)
  const [renameTarget, setRenameTarget] = React.useState<Book | null>(null)
  const [renameTitle, setRenameTitle] = React.useState('')
  const [deleteTarget, setDeleteTarget] = React.useState<Book | null>(null)
  const [exportModalOpen, setExportModalOpen] = React.useState(false)
  const [exportSelectedIds, setExportSelectedIds] = React.useState<number[]>([])
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
    async (selectedIds: number[], format: 'md' | 'txt', exportAsZip: boolean) => {
      if (selectedIds.length === 0) {
        message.warning('请至少选择一本书籍')
        return
      }
      setExporting(true)
      try {
        const booksData = await fetchExportData(selectedIds)
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
    [message]
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

      <div className="bookshelf-list">
        {books.map((book) => (
          <div
            key={book.id}
            className="book-card"
            onClick={() => onOpenBook(book)}
          >
            <div className="book-spine" style={{ background: book.cover_color || '#4A90D9' }} />
            <div className="book-cover" style={{ borderTopColor: book.cover_color || '#4A90D9' }}>
              <span className="book-cover-title">{book.title}</span>
            </div>
            <div className="book-actions">
              <Tooltip title="重命名">
                <button className="book-action-btn" onClick={(e) => openRename(e, book)}>
                  <EditOutlined />
                </button>
              </Tooltip>
              <Tooltip title="删除书籍">
                <button className="book-action-btn danger" onClick={(e) => openDelete(e, book)}>
                  <DeleteOutlined />
                </button>
              </Tooltip>
            </div>
            <div className="book-title">{book.title}</div>
          </div>
        ))}

        <div className="book-card book-card-add" onClick={() => setCreateModalOpen(true)}>
          <div className="book-card-add-inner">
            <PlusOutlined />
            <span>新建书籍</span>
          </div>
        </div>
      </div>

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

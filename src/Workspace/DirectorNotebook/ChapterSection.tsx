import React from 'react'
import {
  PlusOutlined, EditOutlined, DeleteOutlined,
  CheckSquareOutlined, ExportOutlined, ProfileOutlined,
  BookOutlined,
} from '@ant-design/icons'
import { App as AntdApp, Button, Input, Empty, Checkbox, Tooltip } from 'antd'
import type { InputRef } from 'antd/es/input/Input'
import type { Chapter, EntityId, Outline } from '../../types'
import { useWorkspace } from '../WorkspaceContext'
import { HighlightText } from '../search/highlightText'
import ConfirmModal from '../../components/ConfirmModal'
import ExportModal from '../../components/ExportModal'
import { buildExportEntries } from '../../utils/exportBooks'
import type { ExportChapter } from '../../utils/exportBooks'
import ChapterOutlineModal, { type ChapterOutlineModalTarget } from './ChapterOutlineModal'

async function ensureDefaultOutline(bookId?: EntityId | null): Promise<Outline | null> {
  const res = await window.electronAPI.getWritingOutline(bookId)
  if (res.success && res.data) return res.data
  return null
}

export interface ChapterSectionProps {
  bookTitle: string
  /** 创建写作章节后通知父组件，用以同步大纲等 */
  onItemCreated?: (chapterId: EntityId, title: string, isVolume: boolean, parentWritingChapterId: EntityId | null) => void
  /** 删除写作章节时通知父组件，删除对应大纲 */
  onWritingChapterDeleted?: (writingChapterId: EntityId) => void
}

export default function ChapterSection({
  bookTitle,
  onItemCreated,
  onWritingChapterDeleted,
}: ChapterSectionProps) {
  const { message: appMessage } = AntdApp.useApp()
  const {
    writingChapters: chapters,
    activeChapterId: chapterId,
    writingOutlineId,
    bookId,
    enableVolume,
    setActiveChapter: onChapterSelect,
    setChaptersData: onChaptersChange,
    workspaceSearchQuery,
  } = useWorkspace()

  const [editingChapterId, setEditingChapterId] = React.useState<EntityId | null>(null)
  const [editingTitle, setEditingTitle] = React.useState('')
  const addingRef = React.useRef(false)

  const [showAddInput, setShowAddInput] = React.useState(false)
  const [newTitle, setNewTitle] = React.useState('')
  const addInputRef = React.useRef<InputRef>(null)

  const [addingVolume, setAddingVolume] = React.useState(false)
  const [newVolSubtitle, setNewVolSubtitle] = React.useState('')
  const addVolRef = React.useRef<InputRef>(null)

  const [addingChapterVolId, setAddingChapterVolId] = React.useState<EntityId | null>(null)
  const [newChapterSubtitle, setNewChapterSubtitle] = React.useState('')
  const addChapterRef = React.useRef<InputRef>(null)

  const [collapsedVolIds, setCollapsedVolIds] = React.useState<Set<EntityId>>(new Set())

  type DeleteModal = { chapter: Chapter; onConfirm: (checked: boolean) => void; checkboxLabel?: string }
  const [deleteModal, setDeleteModal] = React.useState<DeleteModal | null>(null)
  type BatchDeleteModal = { ids: EntityId[]; onConfirm: () => void }
  const [batchDeleteModal, setBatchDeleteModal] = React.useState<BatchDeleteModal | null>(null)
  const [batchMode, setBatchMode] = React.useState(false)
  const [selectedIds, setSelectedIds] = React.useState<Set<EntityId>>(new Set())

  const [exportModalOpen, setExportModalOpen] = React.useState(false)
  const [exportSelectedIds, setExportSelectedIds] = React.useState<EntityId[]>([])
  const [exportLoading, setExportLoading] = React.useState(false)

  /** 大纲弹窗：null 表示关闭，否则按 mode 显示对应大纲（章节/卷/总纲） */
  const [outlineModalTarget, setOutlineModalTarget] = React.useState<ChapterOutlineModalTarget | null>(null)
  const openChapterOutline = React.useCallback((ch: Chapter) => {
    setOutlineModalTarget({ mode: 'chapter', chapter: ch })
  }, [])
  const openVolumeOutline = React.useCallback((vol: Chapter) => {
    setOutlineModalTarget({ mode: 'volume', chapter: vol })
  }, [])
  const openGlobalOutline = React.useCallback(() => {
    setOutlineModalTarget({ mode: 'global', titleOverride: bookTitle || '本书' })
  }, [bookTitle])
  const handleOutlineChanged = React.useCallback(() => {
    // 跨组件感知大纲变化（命令面板等可能要刷新）；当前无外部消费者，留作扩展点
    window.dispatchEvent(new CustomEvent('chapter-outline-changed'))
  }, [])

  const volumes = React.useMemo(
    () => enableVolume ? chapters.filter((c) => c.parent_id == null) : [],
    [chapters, enableVolume]
  )
  const chaptersByVolId = React.useMemo(() => {
    if (!enableVolume) return new Map<EntityId, Chapter[]>()
    const map = new Map<EntityId, Chapter[]>()
    for (const ch of chapters) {
      if (ch.parent_id != null) {
        const list = map.get(ch.parent_id) ?? []
        list.push(ch)
        map.set(ch.parent_id, list)
      }
    }
    return map
  }, [chapters, enableVolume])

  const writableChapters = enableVolume ? chapters.filter((c) => c.parent_id != null) : chapters
  const idToChapter = React.useMemo(() => new Map(chapters.map((c) => [c.id, c])), [chapters])

  React.useEffect(() => {
    if (showAddInput) addInputRef.current?.focus()
  }, [showAddInput])

  React.useEffect(() => {
    if (addingVolume) addVolRef.current?.focus()
  }, [addingVolume])

  React.useEffect(() => {
    if (addingChapterVolId != null) addChapterRef.current?.focus()
  }, [addingChapterVolId])

  const getOutlineId = async (): Promise<EntityId | null> => {
    if (writingOutlineId) return writingOutlineId
    const outline = await ensureDefaultOutline(bookId)
    return outline ? outline.id : null
  }

  const reloadChapters = async (outlineId: EntityId) => {
    const chapRes = await window.electronAPI.getChapters({ outlineId })
    if (chapRes.success) onChaptersChange?.(outlineId, chapRes.data)
  }

  const handleAddChapter = async () => {
    if (addingRef.current) return
    const subtitle = newTitle.trim()
    const num = writableChapters.length + 1
    const title = subtitle ? `第${num}章 ${subtitle}` : `第${num}章`
    addingRef.current = true
    try {
      const outlineId = await getOutlineId()
      if (!outlineId) return
      const res = await window.electronAPI.addChapter({ outlineId, title })
      if (res.success) {
        await reloadChapters(outlineId)
        setNewTitle('')
        setShowAddInput(false)
        onChapterSelect?.(res.data.id, res.data.title)
        onItemCreated?.(res.data.id, title, false, null)
      }
    } finally { addingRef.current = false }
  }

  const handleAddVolume = async () => {
    if (addingRef.current) return
    const subtitle = newVolSubtitle.trim()
    const num = volumes.length + 1
    const title = subtitle ? `第${num}卷 ${subtitle}` : `第${num}卷`
    addingRef.current = true
    try {
      const outlineId = await getOutlineId()
      if (!outlineId) return
      const res = await window.electronAPI.addChapter({ outlineId, title, parentId: undefined, isVolume: true })
      if (res.success) {
        await reloadChapters(outlineId)
        setNewVolSubtitle('')
        setAddingVolume(false)
        onItemCreated?.(res.data.id, title, true, null)
      }
    } finally { addingRef.current = false }
  }

  const handleAddChapterUnderVolume = async (volumeId: EntityId) => {
    if (addingRef.current) return
    const subtitle = newChapterSubtitle.trim()
    const volChapters = chaptersByVolId.get(volumeId) ?? []
    const num = volChapters.length + 1
    const title = subtitle ? `第${num}章 ${subtitle}` : `第${num}章`
    addingRef.current = true
    try {
      const outlineId = await getOutlineId()
      if (!outlineId) return
      const res = await window.electronAPI.addChapter({ outlineId, title, parentId: volumeId })
      if (res.success) {
        await reloadChapters(outlineId)
        setNewChapterSubtitle('')
        setAddingChapterVolId(null)
        onChapterSelect?.(res.data.id, res.data.title)
        onItemCreated?.(res.data.id, title, false, volumeId)
      }
    } finally { addingRef.current = false }
  }

  const handleRenameChapter = async (ch: Chapter) => {
    const title = editingTitle.trim()
    if (!title || title === ch.title) { setEditingChapterId(null); return }
    await window.electronAPI.renameChapter({ id: ch.id, title })
    setEditingChapterId(null)
    if (writingOutlineId) await reloadChapters(writingOutlineId)
    if (chapterId === ch.id) onChapterSelect?.(ch.id, title)
  }

  const handleDeleteItem = async (ch: Chapter) => {
    const isVol = enableVolume && ch.parent_id == null
    const childIds = isVol ? (chaptersByVolId.get(ch.id) ?? []).map((c) => c.id) : []
    const allIds = isVol ? [ch.id, ...childIds] : [ch.id]

    setDeleteModal({
      chapter: ch,
      onConfirm: async (checked) => {
        setDeleteModal(null)
        for (const id of allIds) await window.electronAPI.deleteChapter({ id })
        if (writingOutlineId) await reloadChapters(writingOutlineId)
        if (chapterId != null && allIds.includes(chapterId)) onChapterSelect?.('', '')
        if (checked) {
          for (const id of allIds) onWritingChapterDeleted?.(id)
        }
      },
      checkboxLabel: '同时删除对应大纲',
    })
  }

  const toggleSelect = (id: EntityId) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleBatchDelete = () => {
    let ids = Array.from(selectedIds)
    if (enableVolume) {
      const expanded = new Set<EntityId>()
      for (const id of ids) {
        expanded.add(id)
        const ch = chapters.find((c) => c.id === id)
        if (ch?.parent_id == null) {
          for (const child of chaptersByVolId.get(id) ?? []) expanded.add(child.id)
        }
      }
      ids = Array.from(expanded)
    }
    if (!ids.length) return
    setBatchDeleteModal({
      ids,
      onConfirm: async () => {
        setBatchDeleteModal(null); setBatchMode(false); setSelectedIds(new Set())
        for (const id of ids) await window.electronAPI.deleteChapter({ id })
        if (writingOutlineId) await reloadChapters(writingOutlineId)
        if (chapterId != null && ids.includes(chapterId)) onChapterSelect?.('', '')
        for (const id of ids) onWritingChapterDeleted?.(id)
      },
    })
  }

  const openExportModal = React.useCallback(() => {
    setExportSelectedIds([])
    setExportModalOpen(true)
  }, [])

  const exportGroups = React.useMemo(
    () =>
      enableVolume
        ? volumes.map((vol) => ({
            id: vol.id,
            title: vol.title,
            children: (chaptersByVolId.get(vol.id) ?? []).map((c) => ({ id: c.id, title: c.title })),
          }))
        : [],
    [enableVolume, volumes, chaptersByVolId]
  )

  const exportItems = React.useMemo(
    () => (enableVolume ? [] : writableChapters.map((c) => ({ id: c.id, title: c.title }))),
    [enableVolume, writableChapters]
  )

  const handleExportConfirm = React.useCallback(
    async (selectedIds: EntityId[], format: 'md' | 'txt', exportAsZip: boolean) => {
      if (selectedIds.length === 0) {
        appMessage.warning('请至少选择一章')
        return
      }
      setExportLoading(true)
      try {
        const chaptersWithContent: ExportChapter[] = await Promise.all(
          selectedIds.map(async (chId) => {
            const ch = idToChapter.get(chId)
            const res = await window.electronAPI.getArticle({ chapterId: chId })
            const content = (res.success && res.data?.content != null) ? String(res.data.content) : ''
            const volumeTitle = enableVolume && ch?.parent_id != null ? idToChapter.get(ch.parent_id)?.title : undefined
            const volumeId = enableVolume ? ch?.parent_id ?? undefined : undefined
            return { title: ch?.title ?? '', content, volumeTitle, volumeId }
          })
        )
        const bookData = { title: bookTitle, chapters: chaptersWithContent }
        const entries = buildExportEntries([bookData], format)
        const res = await window.electronAPI.writeExportFiles({ entries, exportAsZip })
        if (res.success) {
          appMessage.success('导出成功')
          setExportModalOpen(false)
        } else {
          if (res.error !== 'canceled') appMessage.error(res.error || '导出失败')
        }
      } finally {
        setExportLoading(false)
      }
    },
    [bookTitle, enableVolume, idToChapter, appMessage]
  )

  const renderRenameInput = (ch: Chapter) => (
    <Input
      className="nav-rename-input"
      value={editingTitle}
      autoFocus size="small"
      onChange={(e) => setEditingTitle(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') handleRenameChapter(ch)
        if (e.key === 'Escape') setEditingChapterId(null)
      }}
      onBlur={() => handleRenameChapter(ch)}
      onClick={(e) => e.stopPropagation()}
    />
  )

  const renderVolumeNav = () => (
    <div className="nav-chapter-list">
      {volumes.length === 0 && !addingVolume && (
        <Empty image={false} description={<><span>暂无卷，点击 + 新建卷</span><br /><small>再在卷内新增章节</small></>} className="nav-empty" />
      )}

      {volumes.map((vol) => {
        const volChaps = chaptersByVolId.get(vol.id) ?? []
        const collapsed = collapsedVolIds.has(vol.id)
        return (
          <React.Fragment key={vol.id}>
            <div className={`nav-chapter-item nav-volume-item ${batchMode && selectedIds.has(vol.id) ? 'selected' : ''}`}>
              {batchMode && (
                <Checkbox
                  checked={selectedIds.has(vol.id)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => toggleSelect(vol.id)}
                  className="nav-chapter-checkbox"
                />
              )}
              <button
                className="nav-volume-collapse-btn"
                onClick={() => setCollapsedVolIds((prev) => {
                  const next = new Set(prev)
                  next.has(vol.id) ? next.delete(vol.id) : next.add(vol.id)
                  return next
                })}
              >
                <span className={`nav-volume-arrow ${collapsed ? 'collapsed' : ''}`}>▾</span>
              </button>
              {editingChapterId === vol.id ? (
                renderRenameInput(vol)
              ) : (
                <>
                  <span className="nav-chapter-title nav-volume-title">
                    <HighlightText text={vol.title} query={workspaceSearchQuery} />
                  </span>
                  <div className="nav-chapter-actions" onClick={(e) => e.stopPropagation()}>
                    <Tooltip title="新建章节">
                      <Button type="text" size="small" icon={<PlusOutlined style={{ fontSize: 12 }} />}
                        onClick={() => { setAddingChapterVolId(vol.id); setNewChapterSubtitle(''); setCollapsedVolIds(prev => { const n = new Set(prev); n.delete(vol.id); return n }) }}
                        className="nav-action-btn" />
                    </Tooltip>
                    <Tooltip title="卷大纲">
                      <Button type="text" size="small" icon={<ProfileOutlined style={{ fontSize: 14 }} />}
                        onClick={() => openVolumeOutline(vol)} className="nav-action-btn" />
                    </Tooltip>
                    <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} title="重命名"
                      onClick={() => { setEditingChapterId(vol.id); setEditingTitle(vol.title) }}
                      className="nav-action-btn" />
                    <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} title="删除"
                      onClick={() => handleDeleteItem(vol)} className="nav-action-btn" />
                  </div>
                </>
              )}
            </div>

            {!collapsed && (
              <>
                {volChaps.map((ch) => (
                  <div
                    key={ch.id}
                    className={`nav-chapter-item nav-chapter-under-volume ${chapterId === ch.id ? 'active' : ''} ${batchMode && selectedIds.has(ch.id) ? 'selected' : ''}`}
                    onClick={() => { if (editingChapterId !== ch.id) onChapterSelect?.(ch.id, ch.title) }}
                  >
                    {batchMode && (
                      <Checkbox
                        checked={selectedIds.has(ch.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleSelect(ch.id)}
                        className="nav-chapter-checkbox"
                      />
                    )}
                    {editingChapterId === ch.id ? (
                      renderRenameInput(ch)
                    ) : (
                      <>
                        <span className="nav-chapter-title">
                          <HighlightText text={ch.title} query={workspaceSearchQuery} />
                        </span>
                        <div className="nav-chapter-actions" onClick={(e) => e.stopPropagation()}>
                          <Tooltip title="大纲">
                            <Button type="text" size="small" icon={<ProfileOutlined style={{ fontSize: 14 }} />}
                              onClick={() => openChapterOutline(ch)} className="nav-action-btn" />
                          </Tooltip>
                          <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} title="重命名"
                            onClick={() => { setEditingChapterId(ch.id); setEditingTitle(ch.title) }}
                            className="nav-action-btn" />
                          <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} title="删除"
                            onClick={() => handleDeleteItem(ch)} className="nav-action-btn" />
                        </div>
                      </>
                    )}
                  </div>
                ))}

                {addingChapterVolId === vol.id && (
                  <div className="nav-add-row nav-add-row-indent">
                    <span className="nav-add-prefix">第{volChaps.length + 1}章</span>
                    <Input
                      ref={addChapterRef}
                      className="nav-add-input"
                      value={newChapterSubtitle}
                      onChange={(e) => setNewChapterSubtitle(e.target.value)}
                      placeholder="副标题（可选）"
                      onBlur={() => handleAddChapterUnderVolume(vol.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleAddChapterUnderVolume(vol.id)
                        if (e.key === 'Escape') { setAddingChapterVolId(null); setNewChapterSubtitle('') }
                      }}
                    />
                  </div>
                )}
              </>
            )}
          </React.Fragment>
        )
      })}

      {addingVolume && (
        <div className="nav-add-row">
          <span className="nav-add-prefix">第{volumes.length + 1}卷</span>
          <Input
            ref={addVolRef}
            className="nav-add-input"
            value={newVolSubtitle}
            onChange={(e) => setNewVolSubtitle(e.target.value)}
            placeholder="副标题（可选）"
            onBlur={handleAddVolume}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleAddVolume()
              if (e.key === 'Escape') { setAddingVolume(false); setNewVolSubtitle('') }
            }}
          />
        </div>
      )}
    </div>
  )

  const renderFlatNav = () => (
    <div className="nav-chapter-list">
      {chapters.length === 0 && !showAddInput && (
        <Empty image={false} description={<><span>暂无章节，点击 + 新建</span><br /><small>或打开 XMind 导入大纲</small></>} className="nav-empty" />
      )}
      {chapters.map((ch) => (
        <div
          key={ch.id}
          className={`nav-chapter-item ${chapterId === ch.id ? 'active' : ''} ${batchMode && selectedIds.has(ch.id) ? 'selected' : ''}`}
          style={{ paddingLeft: `${((ch.level || 1) - 1) * 12 + 8}px` }}
          onClick={() => { if (editingChapterId !== ch.id) onChapterSelect?.(ch.id, ch.title) }}
        >
          {batchMode && (
            <Checkbox
              checked={selectedIds.has(ch.id)}
              onClick={(e) => e.stopPropagation()}
              onChange={() => toggleSelect(ch.id)}
              className="nav-chapter-checkbox"
            />
          )}
          {editingChapterId === ch.id ? renderRenameInput(ch) : (
            <>
              <span className="nav-chapter-title">
                <HighlightText text={ch.title} query={workspaceSearchQuery} />
              </span>
              <div className="nav-chapter-actions" onClick={(e) => e.stopPropagation()}>
                <Tooltip title="大纲">
                  <Button type="text" size="small" icon={<ProfileOutlined style={{ fontSize: 14 }} />}
                    onClick={() => openChapterOutline(ch)} className="nav-action-btn" />
                </Tooltip>
                <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} title="重命名"
                  onClick={() => { setEditingChapterId(ch.id); setEditingTitle(ch.title) }} className="nav-action-btn" />
                <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} title="删除"
                  onClick={() => handleDeleteItem(ch)} className="nav-action-btn" />
              </div>
            </>
          )}
        </div>
      ))}
      {showAddInput && (
        <div className="nav-add-row">
          <span className="nav-add-prefix">第{writableChapters.length + 1}章</span>
          <Input
            ref={addInputRef}
            className="nav-add-input"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="副标题（可选）"
            onBlur={handleAddChapter}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleAddChapter()
              if (e.key === 'Escape') { setShowAddInput(false); setNewTitle('') }
            }}
          />
        </div>
      )}
    </div>
  )

  const sectionActions = (
    <>
      <Tooltip title="总纲（全书大纲）">
        <Button type="text" size="small"
          icon={<BookOutlined style={{ fontSize: 14 }} />} onClick={openGlobalOutline}
          className="nav-action-btn" />
      </Tooltip>
      <Tooltip title="导出章节">
        <Button type="text" size="small"
          icon={<ExportOutlined style={{ fontSize: 14 }} />} onClick={openExportModal}
          className="nav-action-btn" />
      </Tooltip>
      {batchMode ? (
        <>
          {selectedIds.size > 0 && (
            <Tooltip title={`删除(${selectedIds.size})`}>
              <Button type="text" size="small"
                icon={<DeleteOutlined style={{ fontSize: 14 }} />}
                onClick={handleBatchDelete} className="nav-batch-delete" />
            </Tooltip>
          )}
          <Button type="text" size="small"
            onClick={() => { setBatchMode(false); setSelectedIds(new Set()) }}
            className="nav-batch-cancel">取消</Button>
        </>
      ) : (
        <Button type="text" size="small"
          icon={<CheckSquareOutlined style={{ fontSize: 14 }} />}
          onClick={() => setBatchMode(true)} title="批量操作" className="nav-batch-btn" />
      )}
      {enableVolume ? (
        <Button type="text" size="small"
          icon={<PlusOutlined style={{ fontSize: 14 }} />} title="新建卷"
          onClick={() => { setAddingVolume(true); setNewVolSubtitle('') }}
          className="nav-add-btn" />
      ) : (
        <Button type="text" size="small"
          icon={<PlusOutlined style={{ fontSize: 14 }} />} title="新建章节"
          onClick={() => { setShowAddInput((v) => !v); setNewTitle('') }}
          className="nav-add-btn" />
      )}
    </>
  )

  return (
    <>
      {deleteModal && (
        <ConfirmModal
          title={enableVolume && deleteModal.chapter.parent_id == null ? '删除卷' : '删除章节'}
          message={`确认删除${enableVolume && deleteModal.chapter.parent_id == null ? '卷' : '章节'}「${deleteModal.chapter.title}」？${enableVolume && deleteModal.chapter.parent_id == null ? '卷内章节将一并删除。' : ''}删除后无法恢复。`}
          checkboxLabel={deleteModal.checkboxLabel}
          onConfirm={deleteModal.onConfirm}
          onCancel={() => setDeleteModal(null)}
        />
      )}
      {batchDeleteModal && (
        <ConfirmModal
          title="批量删除"
          message={`确认删除选中的 ${batchDeleteModal.ids.length} 个条目？删除后无法恢复。`}
          onConfirm={() => batchDeleteModal.onConfirm()}
          onCancel={() => setBatchDeleteModal(null)}
        />
      )}

      <ExportModal
        title="导出章节"
        open={exportModalOpen}
        onCancel={() => setExportModalOpen(false)}
        items={exportItems}
        groups={exportGroups}
        selectedIds={exportSelectedIds}
        onSelectedIdsChange={setExportSelectedIds}
        onConfirm={handleExportConfirm}
        confirmLoading={exportLoading}
        selectLabel="选择章节（可多选）："
        emptyText="暂无章节"
      />

      <div className="chapter-list-actionbar">
        <span className="chapter-list-count">
          共 {writableChapters.length} 章
        </span>
        <div className="chapter-list-actionbar-actions">
          {sectionActions}
        </div>
      </div>
      <div className="chapter-list-body">
        {enableVolume ? renderVolumeNav() : renderFlatNav()}
      </div>

      <ChapterOutlineModal
        open={outlineModalTarget !== null}
        target={outlineModalTarget}
        bookId={bookId}
        onClose={() => setOutlineModalTarget(null)}
        onChanged={handleOutlineChanged}
      />
    </>
  )
}

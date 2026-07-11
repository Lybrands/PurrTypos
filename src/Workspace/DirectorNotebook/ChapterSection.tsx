import React from 'react'
import {
  BookOutlined,
  CheckSquareOutlined,
  DeleteOutlined,
  ExportOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import type { Chapter, EntityId } from '../../types'
import ConfirmModal from '../../components/ConfirmModal'
import ExportModal from '../../components/ExportModal'
import { useWorkspace } from '../WorkspaceContext'
import ChapterOutlineModal, { type ChapterOutlineModalTarget } from './ChapterOutlineModal'
import ChapterSectionNavigation from './ChapterSectionNavigation'
import {
  buildChapterSectionModel,
  expandSelectedChapterIds,
  getItemAndDescendantIds,
  numberedChapterTitle,
} from './ChapterSectionModel'
import { useChapterSectionActions } from './useChapterSectionActions'

export interface ChapterSectionProps {
  bookTitle: string
  /** 创建写作章节后通知父组件，用以同步大纲等 */
  onItemCreated?: (chapterId: EntityId, title: string, isVolume: boolean, parentWritingChapterId: EntityId | null) => void
  /** 删除写作章节时通知父组件，删除对应大纲 */
  onWritingChapterDeleted?: (writingChapterId: EntityId) => void
}

type DeleteModal = {
  chapter: Chapter
  onConfirm: (checked: boolean) => void
  checkboxLabel?: string
}

type BatchDeleteModal = {
  ids: EntityId[]
  onConfirm: () => void
}

export default function ChapterSection({
  bookTitle,
  onItemCreated,
  onWritingChapterDeleted,
}: ChapterSectionProps) {
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
  const [showAddInput, setShowAddInput] = React.useState(false)
  const [newTitle, setNewTitle] = React.useState('')
  const [addingVolume, setAddingVolume] = React.useState(false)
  const [newVolumeSubtitle, setNewVolumeSubtitle] = React.useState('')
  const [addingChapterVolumeId, setAddingChapterVolumeId] = React.useState<EntityId | null>(null)
  const [newChapterSubtitle, setNewChapterSubtitle] = React.useState('')
  const [collapsedVolumeIds, setCollapsedVolumeIds] = React.useState<Set<EntityId>>(new Set())
  const [deleteModal, setDeleteModal] = React.useState<DeleteModal | null>(null)
  const [batchDeleteModal, setBatchDeleteModal] = React.useState<BatchDeleteModal | null>(null)
  const [batchMode, setBatchMode] = React.useState(false)
  const [selectedIds, setSelectedIds] = React.useState<Set<EntityId>>(new Set())
  const [exportModalOpen, setExportModalOpen] = React.useState(false)
  const [exportSelectedIds, setExportSelectedIds] = React.useState<EntityId[]>([])
  const [outlineModalTarget, setOutlineModalTarget] = React.useState<ChapterOutlineModalTarget | null>(null)

  const {
    volumes,
    chaptersByVolumeId,
    writableChapters,
    chaptersById,
  } = React.useMemo(
    () => buildChapterSectionModel(chapters, enableVolume),
    [chapters, enableVolume],
  )

  const closeExportModal = React.useCallback(() => setExportModalOpen(false), [])
  const {
    createItem,
    deleteByIds,
    exportChapters,
    exportLoading,
    renameChapter,
  } = useChapterSectionActions({
    bookId,
    bookTitle,
    writingOutlineId,
    activeChapterId: chapterId,
    enableVolume,
    chaptersById,
    onChapterSelect,
    onChaptersChange,
    onItemCreated,
    onWritingChapterDeleted,
    onExportSuccess: closeExportModal,
  })

  const openChapterOutline = React.useCallback((chapter: Chapter) => {
    setOutlineModalTarget({ mode: 'chapter', chapter })
  }, [])

  const openVolumeOutline = React.useCallback((chapter: Chapter) => {
    setOutlineModalTarget({ mode: 'volume', chapter })
  }, [])

  const openGlobalOutline = React.useCallback(() => {
    setOutlineModalTarget({ mode: 'global', titleOverride: bookTitle || '本书' })
  }, [bookTitle])

  const handleOutlineChanged = React.useCallback(() => {
    window.dispatchEvent(new CustomEvent('chapter-outline-changed'))
  }, [])

  const handleAddChapter = () => createItem({
    title: numberedChapterTitle('章', writableChapters.length + 1, newTitle),
    resetInput: () => {
      setNewTitle('')
      setShowAddInput(false)
    },
  })

  const handleQuickAddChapter = () => createItem({
    title: numberedChapterTitle('章', writableChapters.length + 1),
  })

  const handleQuickAddChapterToVolume = (volumeId: EntityId) => createItem({
    title: numberedChapterTitle('章', (chaptersByVolumeId.get(volumeId) ?? []).length + 1),
    parentId: volumeId,
  })

  const handleAddVolume = () => createItem({
    title: numberedChapterTitle('卷', volumes.length + 1, newVolumeSubtitle),
    isVolume: true,
    resetInput: () => {
      setNewVolumeSubtitle('')
      setAddingVolume(false)
    },
  })

  const handleAddChapterToVolume = (volumeId: EntityId) => createItem({
    title: numberedChapterTitle(
      '章',
      (chaptersByVolumeId.get(volumeId) ?? []).length + 1,
      newChapterSubtitle,
    ),
    parentId: volumeId,
    resetInput: () => {
      setNewChapterSubtitle('')
      setAddingChapterVolumeId(null)
    },
  })

  const handleRenameChapter = (chapter: Chapter) => renameChapter(
    chapter,
    editingTitle,
    () => setEditingChapterId(null),
  )

  const handleDeleteItem = (chapter: Chapter) => {
    const ids = getItemAndDescendantIds(chapter, enableVolume, chaptersByVolumeId)
    setDeleteModal({
      chapter,
      onConfirm: async (checked) => {
        setDeleteModal(null)
        await deleteByIds(ids, checked)
      },
      checkboxLabel: '同时删除对应大纲',
    })
  }

  const toggleSelect = (id: EntityId) => {
    setSelectedIds((previousIds) => {
      const nextIds = new Set(previousIds)
      nextIds.has(id) ? nextIds.delete(id) : nextIds.add(id)
      return nextIds
    })
  }

  const handleBatchDelete = () => {
    const ids = expandSelectedChapterIds(
      selectedIds,
      enableVolume,
      chaptersById,
      chaptersByVolumeId,
    )
    if (ids.length === 0) return

    setBatchDeleteModal({
      ids,
      onConfirm: async () => {
        setBatchDeleteModal(null)
        setBatchMode(false)
        setSelectedIds(new Set())
        await deleteByIds(ids, true)
      },
    })
  }

  const openExportModal = React.useCallback(() => {
    setExportSelectedIds([])
    setExportModalOpen(true)
  }, [])

  const exportGroups = React.useMemo(
    () => enableVolume
      ? volumes.map((volume) => ({
          id: volume.id,
          title: volume.title,
          children: (chaptersByVolumeId.get(volume.id) ?? []).map((chapter) => ({
            id: chapter.id,
            title: chapter.title,
          })),
        }))
      : [],
    [chaptersByVolumeId, enableVolume, volumes],
  )

  const exportItems = React.useMemo(
    () => enableVolume
      ? []
      : writableChapters.map((chapter) => ({ id: chapter.id, title: chapter.title })),
    [enableVolume, writableChapters],
  )

  const startAddingChapterToVolume = (volumeId: EntityId) => {
    setAddingChapterVolumeId(volumeId)
    setNewChapterSubtitle('')
    setCollapsedVolumeIds((previousIds) => {
      const nextIds = new Set(previousIds)
      nextIds.delete(volumeId)
      return nextIds
    })
  }

  const toggleVolumeCollapsed = (volumeId: EntityId) => {
    setCollapsedVolumeIds((previousIds) => {
      const nextIds = new Set(previousIds)
      nextIds.has(volumeId) ? nextIds.delete(volumeId) : nextIds.add(volumeId)
      return nextIds
    })
  }

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
        onCancel={closeExportModal}
        items={exportItems}
        groups={exportGroups}
        selectedIds={exportSelectedIds}
        onSelectedIdsChange={setExportSelectedIds}
        onConfirm={exportChapters}
        confirmLoading={exportLoading}
        selectLabel="选择章节（可多选）："
        emptyText="暂无章节"
      />

      <div className="chapter-list-actionbar">
        <span className="chapter-list-count">共 {writableChapters.length} 章</span>
        <div className="chapter-list-actionbar-actions">
          <Tooltip title="总纲（全书大纲）">
            <Button
              type="text"
              size="small"
              icon={<BookOutlined style={{ fontSize: 14 }} />}
              onClick={openGlobalOutline}
              className="nav-action-btn"
            />
          </Tooltip>
          <Tooltip title="导出章节">
            <Button
              type="text"
              size="small"
              icon={<ExportOutlined style={{ fontSize: 14 }} />}
              onClick={openExportModal}
              className="nav-action-btn"
            />
          </Tooltip>
          {batchMode ? (
            <>
              {selectedIds.size > 0 && (
                <Tooltip title={`删除(${selectedIds.size})`}>
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined style={{ fontSize: 14 }} />}
                    onClick={handleBatchDelete}
                    className="nav-batch-delete"
                  />
                </Tooltip>
              )}
              <Button
                type="text"
                size="small"
                onClick={() => {
                  setBatchMode(false)
                  setSelectedIds(new Set())
                }}
                className="nav-batch-cancel"
              >
                取消
              </Button>
            </>
          ) : (
            <Button
              type="text"
              size="small"
              icon={<CheckSquareOutlined style={{ fontSize: 14 }} />}
              onClick={() => setBatchMode(true)}
              title="批量操作"
              className="nav-batch-btn"
            />
          )}
          {enableVolume ? (
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined style={{ fontSize: 14 }} />}
              title="新建卷"
              onClick={() => {
                setAddingVolume(true)
                setNewVolumeSubtitle('')
              }}
              className="nav-add-btn"
            />
          ) : (
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined style={{ fontSize: 14 }} />}
              title="新建章节"
              onClick={() => {
                setShowAddInput((visible) => !visible)
                setNewTitle('')
              }}
              className="nav-add-btn"
            />
          )}
        </div>
      </div>

      <div className="chapter-list-body">
        <ChapterSectionNavigation
          enableVolume={enableVolume}
          chapters={chapters}
          volumes={volumes}
          chaptersByVolumeId={chaptersByVolumeId}
          writableChapterCount={writableChapters.length}
          activeChapterId={chapterId}
          searchQuery={workspaceSearchQuery}
          editingChapterId={editingChapterId}
          editingTitle={editingTitle}
          onEditingTitleChange={setEditingTitle}
          onStartRename={(chapter) => {
            setEditingChapterId(chapter.id)
            setEditingTitle(chapter.title)
          }}
          onCommitRename={handleRenameChapter}
          onCancelRename={() => setEditingChapterId(null)}
          batchMode={batchMode}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          showAddInput={showAddInput}
          newTitle={newTitle}
          onNewTitleChange={setNewTitle}
          onAddChapter={handleAddChapter}
          onCancelAddChapter={() => {
            setShowAddInput(false)
            setNewTitle('')
          }}
          onQuickAddChapter={handleQuickAddChapter}
          addingVolume={addingVolume}
          newVolumeSubtitle={newVolumeSubtitle}
          onNewVolumeSubtitleChange={setNewVolumeSubtitle}
          onAddVolume={handleAddVolume}
          onCancelAddVolume={() => {
            setAddingVolume(false)
            setNewVolumeSubtitle('')
          }}
          addingChapterVolumeId={addingChapterVolumeId}
          newChapterSubtitle={newChapterSubtitle}
          onNewChapterSubtitleChange={setNewChapterSubtitle}
          onStartAddingChapterToVolume={startAddingChapterToVolume}
          onAddChapterToVolume={handleAddChapterToVolume}
          onCancelAddChapterToVolume={() => {
            setAddingChapterVolumeId(null)
            setNewChapterSubtitle('')
          }}
          onQuickAddChapterToVolume={handleQuickAddChapterToVolume}
          collapsedVolumeIds={collapsedVolumeIds}
          onToggleVolumeCollapsed={toggleVolumeCollapsed}
          onSelectChapter={(chapter) => onChapterSelect(chapter.id, chapter.title)}
          onOpenChapterOutline={openChapterOutline}
          onOpenVolumeOutline={openVolumeOutline}
          onDeleteItem={handleDeleteItem}
        />
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

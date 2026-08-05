import React from 'react'
import { DeleteIcon, EditIcon, OutlineIcon, PlusIcon } from '@/purr-components'
import { PurrButton, PurrCheckbox, PurrEmpty, PurrInput, PurrTooltip, type PurrInputRef } from '@/purr-components'
import type { Chapter, EntityId } from '../../types'
import { HighlightText } from '../search/highlightText'

interface ChapterSectionNavigationProps {
  enableVolume: boolean
  chapters: Chapter[]
  volumes: Chapter[]
  chaptersByVolumeId: Map<EntityId, Chapter[]>
  writableChapterCount: number
  activeChapterId?: EntityId | null
  searchQuery: string

  editingChapterId: EntityId | null
  editingTitle: string
  onEditingTitleChange: (title: string) => void
  onStartRename: (chapter: Chapter) => void
  onCommitRename: (chapter: Chapter) => void
  onCancelRename: () => void

  batchMode: boolean
  selectedIds: ReadonlySet<EntityId>
  onToggleSelect: (id: EntityId) => void

  showAddInput: boolean
  newTitle: string
  onNewTitleChange: (title: string) => void
  onAddChapter: () => void
  onCancelAddChapter: () => void
  onQuickAddChapter: () => void

  addingVolume: boolean
  newVolumeSubtitle: string
  onNewVolumeSubtitleChange: (title: string) => void
  onAddVolume: () => void
  onCancelAddVolume: () => void

  addingChapterVolumeId: EntityId | null
  newChapterSubtitle: string
  onNewChapterSubtitleChange: (title: string) => void
  onStartAddingChapterToVolume: (volumeId: EntityId) => void
  onAddChapterToVolume: (volumeId: EntityId) => void
  onCancelAddChapterToVolume: () => void
  onQuickAddChapterToVolume: (volumeId: EntityId) => void

  collapsedVolumeIds: ReadonlySet<EntityId>
  onToggleVolumeCollapsed: (volumeId: EntityId) => void
  onSelectChapter: (chapter: Chapter) => void
  onOpenChapterOutline: (chapter: Chapter) => void
  onOpenVolumeOutline: (volume: Chapter) => void
  onDeleteItem: (chapter: Chapter) => void
}

export default function ChapterSectionNavigation({
  enableVolume,
  chapters,
  volumes,
  chaptersByVolumeId,
  writableChapterCount,
  activeChapterId,
  searchQuery,
  editingChapterId,
  editingTitle,
  onEditingTitleChange,
  onStartRename,
  onCommitRename,
  onCancelRename,
  batchMode,
  selectedIds,
  onToggleSelect,
  showAddInput,
  newTitle,
  onNewTitleChange,
  onAddChapter,
  onCancelAddChapter,
  onQuickAddChapter,
  addingVolume,
  newVolumeSubtitle,
  onNewVolumeSubtitleChange,
  onAddVolume,
  onCancelAddVolume,
  addingChapterVolumeId,
  newChapterSubtitle,
  onNewChapterSubtitleChange,
  onStartAddingChapterToVolume,
  onAddChapterToVolume,
  onCancelAddChapterToVolume,
  onQuickAddChapterToVolume,
  collapsedVolumeIds,
  onToggleVolumeCollapsed,
  onSelectChapter,
  onOpenChapterOutline,
  onOpenVolumeOutline,
  onDeleteItem,
}: ChapterSectionNavigationProps) {
  const addInputRef = React.useRef<PurrInputRef>(null)
  const addVolumeInputRef = React.useRef<PurrInputRef>(null)
  const addChapterInputRef = React.useRef<PurrInputRef>(null)
  const [hoveredItemId, setHoveredItemId] = React.useState<EntityId | null>(null)

  React.useEffect(() => {
    if (showAddInput) addInputRef.current?.focus()
  }, [showAddInput])

  React.useEffect(() => {
    if (addingVolume) addVolumeInputRef.current?.focus()
  }, [addingVolume])

  React.useEffect(() => {
    if (addingChapterVolumeId != null) addChapterInputRef.current?.focus()
  }, [addingChapterVolumeId])

  const renderRenameInput = (chapter: Chapter) => (
    <PurrInput
      className="nav-rename-input"
      value={editingTitle}
      autoFocus
      size="small"
      onChange={(event) => onEditingTitleChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onCommitRename(chapter)
        if (event.key === 'Escape') onCancelRename()
      }}
      onBlur={() => onCommitRename(chapter)}
      onClick={(event) => event.stopPropagation()}
    />
  )

  const renderChapterRow = (
    chapter: Chapter,
    options?: { extraClass?: string; style?: React.CSSProperties },
  ) => {
    const className = [
      'nav-chapter-item',
      options?.extraClass,
      activeChapterId === chapter.id ? 'active' : '',
      batchMode && selectedIds.has(chapter.id) ? 'selected' : '',
    ].filter(Boolean).join(' ')

    return (
      <div
        key={chapter.id}
        className={className}
        style={options?.style}
        onMouseEnter={() => setHoveredItemId(chapter.id)}
        onMouseLeave={() => setHoveredItemId(null)}
        onClick={() => {
          if (editingChapterId !== chapter.id) onSelectChapter(chapter)
        }}
      >
        {batchMode && (
          <PurrCheckbox
            checked={selectedIds.has(chapter.id)}
            onClick={(event) => event.stopPropagation()}
            onChange={() => onToggleSelect(chapter.id)}
            className="nav-chapter-checkbox"
          />
        )}
        {editingChapterId === chapter.id ? renderRenameInput(chapter) : (
          <>
            <span className="nav-chapter-title">
              <HighlightText text={chapter.title} query={searchQuery} />
            </span>
            {hoveredItemId === chapter.id && (
              <div className="nav-chapter-actions" onClick={(event) => event.stopPropagation()}>
                <PurrTooltip title="大纲">
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<OutlineIcon />}
                    onClick={() => onOpenChapterOutline(chapter)}
                    className="nav-action-btn"
                  />
                </PurrTooltip>
                <PurrButton
                  type="text"
                  size="small"
                  icon={<EditIcon style={{ fontSize: 14 }} />}
                  title="重命名"
                  onClick={() => onStartRename(chapter)}
                  className="nav-action-btn"
                />
                <PurrButton
                  type="text"
                  size="small"
                  icon={<DeleteIcon style={{ fontSize: 14 }} />}
                  title="删除"
                  onClick={() => onDeleteItem(chapter)}
                  className="nav-action-btn"
                />
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  if (!enableVolume) {
    return (
      <div className="nav-chapter-list">
        {chapters.length === 0 && !showAddInput && (
          <PurrEmpty
            image={false}
            description={<><span>暂无章节，点击 + 新建</span><br /><small>或打开 XMind 导入大纲</small></>}
            className="nav-empty"
          />
        )}
        {chapters.map((chapter) => renderChapterRow(chapter, {
          style: { paddingLeft: `${((chapter.level || 1) - 1) * 12 + 8}px` },
        }))}
        {showAddInput && (
          <div className="nav-add-row">
            <span className="nav-add-prefix">第{writableChapterCount + 1}章</span>
            <PurrInput
              ref={addInputRef}
              className="nav-add-input"
              value={newTitle}
              onChange={(event) => onNewTitleChange(event.target.value)}
              placeholder="副标题（可选）"
              onBlur={onAddChapter}
              onKeyDown={(event) => {
                if (event.key === 'Enter') onAddChapter()
                if (event.key === 'Escape') onCancelAddChapter()
              }}
            />
          </div>
        )}
        {!showAddInput && !batchMode && chapters.length > 0 && (
          <div
            className="nav-add-placeholder"
            onClick={onQuickAddChapter}
            title="新建章节（如需自定义副标题，请用顶部 + 按钮）"
          >
            <PlusIcon />
            <span>新建第{writableChapterCount + 1}章</span>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="nav-chapter-list">
      {volumes.length === 0 && !addingVolume && (
        <PurrEmpty
          image={false}
          description={<><span>暂无卷，点击 + 新建卷</span><br /><small>再在卷内新增章节</small></>}
          className="nav-empty"
        />
      )}

      {volumes.map((volume) => {
        const volumeChapters = chaptersByVolumeId.get(volume.id) ?? []
        const collapsed = collapsedVolumeIds.has(volume.id)
        return (
          <React.Fragment key={volume.id}>
            <div
              className={`nav-chapter-item nav-volume-item ${batchMode && selectedIds.has(volume.id) ? 'selected' : ''}`}
              onMouseEnter={() => setHoveredItemId(volume.id)}
              onMouseLeave={() => setHoveredItemId(null)}
            >
              {batchMode && (
                <PurrCheckbox
                  checked={selectedIds.has(volume.id)}
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => onToggleSelect(volume.id)}
                  className="nav-chapter-checkbox"
                />
              )}
              <button
                className="nav-volume-collapse-btn"
                onClick={() => onToggleVolumeCollapsed(volume.id)}
              >
                <span className={`nav-volume-arrow ${collapsed ? 'collapsed' : ''}`}>▾</span>
              </button>
              {editingChapterId === volume.id ? renderRenameInput(volume) : (
                <>
                  <span className="nav-chapter-title nav-volume-title">
                    <HighlightText text={volume.title} query={searchQuery} />
                  </span>
                  {hoveredItemId === volume.id && (
                    <div className="nav-chapter-actions" onClick={(event) => event.stopPropagation()}>
                      <PurrTooltip title="新建章节">
                        <PurrButton
                          type="text"
                          size="small"
                          icon={<PlusIcon style={{ fontSize: 12 }} />}
                          onClick={() => onStartAddingChapterToVolume(volume.id)}
                          className="nav-action-btn"
                        />
                      </PurrTooltip>
                      <PurrTooltip title="卷大纲">
                        <PurrButton
                          type="text"
                          size="small"
                          icon={<OutlineIcon />}
                          onClick={() => onOpenVolumeOutline(volume)}
                          className="nav-action-btn"
                        />
                      </PurrTooltip>
                      <PurrButton
                        type="text"
                        size="small"
                        icon={<EditIcon style={{ fontSize: 14 }} />}
                        title="重命名"
                        onClick={() => onStartRename(volume)}
                        className="nav-action-btn"
                      />
                      <PurrButton
                        type="text"
                        size="small"
                        icon={<DeleteIcon style={{ fontSize: 14 }} />}
                        title="删除"
                        onClick={() => onDeleteItem(volume)}
                        className="nav-action-btn"
                      />
                    </div>
                  )}
                </>
              )}
            </div>

            {!collapsed && (
              <>
                {volumeChapters.map((chapter) => renderChapterRow(chapter, {
                  extraClass: 'nav-chapter-under-volume',
                }))}

                {addingChapterVolumeId === volume.id && (
                  <div className="nav-add-row nav-add-row-indent">
                    <span className="nav-add-prefix">第{volumeChapters.length + 1}章</span>
                    <PurrInput
                      ref={addChapterInputRef}
                      className="nav-add-input"
                      value={newChapterSubtitle}
                      onChange={(event) => onNewChapterSubtitleChange(event.target.value)}
                      placeholder="副标题（可选）"
                      onBlur={() => onAddChapterToVolume(volume.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') onAddChapterToVolume(volume.id)
                        if (event.key === 'Escape') onCancelAddChapterToVolume()
                      }}
                    />
                  </div>
                )}
                {addingChapterVolumeId !== volume.id && !batchMode && volumeChapters.length > 0 && (
                  <div
                    className="nav-add-placeholder nav-add-placeholder--indent"
                    onClick={() => onQuickAddChapterToVolume(volume.id)}
                    title="新建章节（如需自定义副标题，请用卷上的 + 按钮）"
                  >
                    <PlusIcon />
                    <span>新建第{volumeChapters.length + 1}章</span>
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
          <PurrInput
            ref={addVolumeInputRef}
            className="nav-add-input"
            value={newVolumeSubtitle}
            onChange={(event) => onNewVolumeSubtitleChange(event.target.value)}
            placeholder="副标题（可选）"
            onBlur={onAddVolume}
            onKeyDown={(event) => {
              if (event.key === 'Enter') onAddVolume()
              if (event.key === 'Escape') onCancelAddVolume()
            }}
          />
        </div>
      )}
    </div>
  )
}

import React, { Suspense, lazy } from 'react'
import {
  ExpandOutlined,
  CompressOutlined,
  UploadOutlined,
  ExportOutlined,
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  ArrowLeftOutlined,
  LoadingOutlined,
  InfoCircleOutlined,
  BookOutlined,
  FileAddOutlined,
  CheckSquareOutlined,
  EllipsisOutlined,
  SwapOutlined,
} from '@ant-design/icons'
import type { Chapter, EntityId, Outline, VolumeOutline } from '../../types'
import { Button, Tabs, Spin, Empty, Tooltip, Alert, Checkbox, Dropdown, Segmented, Space } from 'antd'
import type { MenuProps } from 'antd'
import { useOutlineCrud } from '../hooks/useOutlineCrud'
import { useWorkspace } from '../WorkspaceContext'
import { HighlightText } from '../search/highlightText'
import ConfirmModal from '../../components/ConfirmModal'
import CharacterTab from './CharacterTab'
import StoryBackgroundTab from './StoryBackgroundTab'
import OutlineMarkdownPane, { type OutlineMarkdownPaneRef } from './OutlineMarkdownPane'
import './index.scss'

const MindMapView = lazy(() => import('./MindMapView'))

export type ChapterOutlineSelectInfo = { title: string; writingChapterId?: EntityId | null }

interface OutlinePanelProps {
  isFullscreen: boolean
  onToggleFullscreen: () => void
  onChapterOutlineDeleted?: (title: string) => void
  onChapterOutlineSelect?: (info: ChapterOutlineSelectInfo) => void
  skipAutoOpenForTitle?: string | null
  refreshKey?: number
  onRefreshReady?: (refresh: () => void) => void
}

export default function OutlinePanel({
  isFullscreen,
  onToggleFullscreen,
  onChapterOutlineDeleted,
  onChapterOutlineSelect,
  skipAutoOpenForTitle,
  refreshKey,
  onRefreshReady,
}: OutlinePanelProps) {
  const {
    writingChapters = [],
    enableVolume,
    bookId,
    syncOutlineChapter,
    activeChapterId,
    activeChapterTitle,
    workspaceSearchQuery,
  } = useWorkspace()
  const [displayChapters, setDisplayChapters] = React.useState<Chapter[]>([])
  const [displayOutlineId, setDisplayOutlineId] = React.useState<EntityId | null>(null)
  const [displayLoading, setDisplayLoading] = React.useState(false)
  const [activeTab, setActiveTab] = React.useState(0)
  const [viewMode, setViewMode] = React.useState<'list' | 'detail'>('list')
  const [showSwitcher, setShowSwitcher] = React.useState(false)
  const [chapterBatchMode, setChapterBatchMode] = React.useState(false)
  const [selectedChapterOutlineIds, setSelectedChapterOutlineIds] = React.useState<Set<EntityId>>(new Set())
  const [otherBatchMode, setOtherBatchMode] = React.useState(false)
  const [selectedOtherOutlineIds, setSelectedOtherOutlineIds] = React.useState<Set<EntityId>>(new Set())
  const [batchDeleteModal, setBatchDeleteModal] = React.useState<{ ids: EntityId[]; titles: string[]; onConfirm: () => void } | null>(null)
  /** 同步选中的章节无大纲记录时，用于展示待上传 */
  const [syncedChapterNoOutlineTitle, setSyncedChapterNoOutlineTitle] = React.useState<string | null>(null)
  /** 详情内：思维导图 / Markdown */
  const [detailOutlineSub, setDetailOutlineSub] = React.useState<'xmind' | 'markdown'>('xmind')
  const outlineMarkdownPaneRef = React.useRef<OutlineMarkdownPaneRef | null>(null)

  const crud = useOutlineCrud({
    refreshKey,
    onRefreshReady,
    onChapterOutlineDeleted,
    displayOutlineId,
    setDisplayChapters,
    setDisplayOutlineId,
    setViewMode,
    bookId,
    enableVolume,
  })
  const {
    loadData,
    globalOutline,
    setGlobalOutline,
    chapterOutlines,
    otherOutlines,
    volumeOutlines,
    chaptersCache,
    loading,
    loadingType,
    error,
    setError,
    deleteModal,
    setDeleteModal,
    handleUploadXmind,
    handleUploadXmindForOutline,
    handleEdit,
    handleDelete,
    handleDeleteVolumeOutline,
    handleDeleteGlobal,
  } = crud

  const toggleChapterOutlineSelect = (id: EntityId) => {
    setSelectedChapterOutlineIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleOtherOutlineSelect = (id: EntityId) => {
    setSelectedOtherOutlineIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleBatchDeleteChapterOutlines = () => {
    const ids = Array.from(selectedChapterOutlineIds)
    if (ids.length === 0) return
    const titles = chapterOutlines.filter((o) => ids.includes(o.id)).map((o) => o.title)
    setBatchDeleteModal({
      ids,
      titles,
      onConfirm: async () => {
        setBatchDeleteModal(null)
        setChapterBatchMode(false)
        setSelectedChapterOutlineIds(new Set())
        for (const id of ids) {
          const res = await window.electronAPI.deleteOutline({ outlineId: id })
          if (res.success) {
            const outline = chapterOutlines.find((o) => o.id === id)
            if (outline) {
              delete chaptersCache.current[id]
              onChapterOutlineDeleted?.(outline.title)
            }
          }
        }
        loadData()
        if (displayOutlineId != null && ids.includes(displayOutlineId)) {
          setViewMode('list')
          setDisplayChapters([])
          setDisplayOutlineId(null)
        }
      },
    })
  }

  const handleBatchDeleteOtherOutlines = () => {
    const ids = Array.from(selectedOtherOutlineIds)
    if (ids.length === 0) return
    const titles = otherOutlines.filter((o) => ids.includes(o.id)).map((o) => o.title)
    setBatchDeleteModal({
      ids,
      titles,
      onConfirm: async () => {
        setBatchDeleteModal(null)
        setOtherBatchMode(false)
        setSelectedOtherOutlineIds(new Set())
        for (const id of ids) {
          const res = await window.electronAPI.deleteOutline({ outlineId: id })
          if (res.success) {
            const outline = otherOutlines.find((o) => o.id === id)
            if (outline) {
              delete chaptersCache.current[id]
            }
          }
        }
        loadData()
        if (displayOutlineId != null && ids.includes(displayOutlineId)) {
          setViewMode('list')
          setDisplayChapters([])
          setDisplayOutlineId(null)
        }
      },
    })
  }

  const fetchOutlineChaptersLatest = React.useCallback(
    async (outlineId: EntityId) => {
      setDisplayChapters([])
      setDisplayLoading(true)
      try {
        const chapRes = await window.electronAPI.getChapters({ outlineId })
        const data = chapRes?.data
        const chapters = Array.isArray(data) ? data : []
        if (chapRes?.success) {
          chaptersCache.current[outlineId] = chapters
          setDisplayChapters(chapters)
        } else {
          setError(chapRes?.error || '加载失败')
        }
      } catch (err) {
        console.error('[OutlinePanel] getChapters error:', err)
        setError('加载失败：' + (err instanceof Error ? err.message : String(err)))
      } finally {
        setDisplayLoading(false)
      }
    },
    [setError]
  )

  const prevSyncedKeyRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    if (!syncOutlineChapter) {
      prevSyncedKeyRef.current = null
      return
    }
    const key = `${activeChapterId ?? ''}\u0000${activeChapterTitle || ''}`
    if (prevSyncedKeyRef.current === key) return
    prevSyncedKeyRef.current = key
    if (!activeChapterId && !activeChapterTitle) return
    if (skipAutoOpenForTitle != null && skipAutoOpenForTitle === activeChapterTitle) return

    const wid =
      activeChapterId != null && String(activeChapterId).trim() !== '' ? activeChapterId : null
    const outline =
      wid != null
        ? chapterOutlines.find(
            (o) => o.writing_chapter_id != null && String(o.writing_chapter_id) === String(wid),
          )
        : undefined
    const outlineByTitle =
      !outline && activeChapterTitle
        ? chapterOutlines.find((o) => o.title === activeChapterTitle)
        : undefined
    const resolved = outline ?? outlineByTitle

    setViewMode('detail')
    if (!resolved) {
      setSyncedChapterNoOutlineTitle(activeChapterTitle || null)
      setDisplayOutlineId(null)
      setDisplayChapters([])
      setDisplayLoading(false)
      return
    }
    setSyncedChapterNoOutlineTitle(null)
    const id = resolved.id
    if (!id || String(id).trim() === '') {
      setSyncedChapterNoOutlineTitle(activeChapterTitle || null)
      setDisplayOutlineId(null)
      setDisplayChapters([])
      return
    }
    setDisplayOutlineId(id)
    if (!resolved.file_path) {
      setDisplayChapters([])
      setDisplayLoading(false)
      return
    }
    void fetchOutlineChaptersLatest(id)
  }, [
    syncOutlineChapter,
    activeChapterId,
    activeChapterTitle,
    chapterOutlines,
    skipAutoOpenForTitle,
    fetchOutlineChaptersLatest,
  ])

  const allOutlines = React.useMemo(() => {
    const list: Outline[] = []
    if (globalOutline) list.push(globalOutline)
    if (enableVolume) {
      for (const vol of volumeOutlines) {
        list.push(vol)
        list.push(...vol.chapters)
      }
      list.push(...chapterOutlines)
    } else {
      list.push(...chapterOutlines)
    }
    list.push(...otherOutlines)
    return list
  }, [globalOutline, chapterOutlines, otherOutlines, volumeOutlines, enableVolume])

  const currentOutline = React.useMemo(
    () => allOutlines.find((o) => String(o?.id) === String(displayOutlineId)),
    [allOutlines, displayOutlineId]
  )

  const prevDisplayOutlineIdForSub = React.useRef<EntityId | null>(null)
  React.useEffect(() => {
    if (viewMode === 'list') prevDisplayOutlineIdForSub.current = null
  }, [viewMode])

  React.useEffect(() => {
    if (!currentOutline || String(currentOutline.id) !== String(displayOutlineId)) return
    if (prevDisplayOutlineIdForSub.current === displayOutlineId) return
    prevDisplayOutlineIdForSub.current = displayOutlineId
    setDetailOutlineSub(currentOutline.file_path ? 'xmind' : 'markdown')
  }, [viewMode, displayOutlineId, currentOutline])

  const flushMarkdownSave = React.useCallback(async () => {
    await outlineMarkdownPaneRef.current?.flushSave()
  }, [])

  const handleOutlineClick = React.useCallback(
    async (outline: Outline, syncChapter = true) => {
      await flushMarkdownSave()
      // 每次点击进入详情都刷新大纲列表，避免详情使用旧的 outline 元数据
      loadData()
      setSyncedChapterNoOutlineTitle(null)
      const id = outline?.id
      if (!id || String(id).trim() === '') {
        console.warn('[OutlinePanel] invalid id, outline:', outline)
        return
      }

      setViewMode('detail')
      setShowSwitcher(false)
      setDisplayOutlineId(id)
      setError('')
      if (syncChapter) {
        onChapterOutlineSelect?.({
          title: outline.title,
          writingChapterId: outline.writing_chapter_id ?? null,
        })
      }
      await fetchOutlineChaptersLatest(id)
    },
    [onChapterOutlineSelect, flushMarkdownSave, fetchOutlineChaptersLatest, loadData]
  )

  const handleOpenGlobalMarkdownOnly = React.useCallback(async () => {
    if (bookId == null) {
      setError('请先选择书籍')
      return
    }
    await flushMarkdownSave()
    setError('')
    const res = await window.electronAPI.ensureGlobalOutline(bookId)
    if (!res.success || !res.data) {
      setError(res.error || '无法创建总纲')
      return
    }
    setGlobalOutline(res.data)
    void handleOutlineClick(res.data)
  }, [bookId, flushMarkdownSave, setGlobalOutline, handleOutlineClick])

  const [openingSourceId, setOpeningSourceId] = React.useState<EntityId | null>(null)

  const handleOpenSource = React.useCallback((outline: Outline) => {
    const fp = outline.file_path
    if (!fp) {
      setError('该大纲没有关联的源文件路径')
      return
    }
    setOpeningSourceId(outline.id)
    window.electronAPI.openFilePath(fp).then((res) => {
      if (!res.success) setError('打开失败：' + res.error)
    }).finally(() => setOpeningSourceId(null))
  }, [])

  const openDetailXmindUpload = React.useCallback(() => {
    if (!currentOutline) return
    if (currentOutline.type === 'global') void handleUploadXmind('global')
    else if (currentOutline.type === 'other') void handleUploadXmind('other')
    else if (enableVolume) void handleUploadXmindForOutline(currentOutline)
    else void handleUploadXmind('chapter', currentOutline.title)
  }, [currentOutline, enableVolume, handleUploadXmind, handleUploadXmindForOutline])

  return (
    <div className={`outline-panel ${isFullscreen ? 'fullscreen' : ''}`}>
      {deleteModal && (
        <ConfirmModal
          title={deleteModal.title}
          message={deleteModal.message}
          checkboxLabel={deleteModal.checkboxLabel}
          onConfirm={deleteModal.onConfirm}
          onCancel={() => setDeleteModal(null)}
        />
      )}
      {batchDeleteModal && (
        <ConfirmModal
          title="批量删除章节大纲"
          message={`确认删除选中的 ${batchDeleteModal.ids.length} 个大纲？删除后无法恢复。`}
          onConfirm={() => batchDeleteModal.onConfirm()}
          onCancel={() => setBatchDeleteModal(null)}
        />
      )}
      <div className="panel-header">
        <span className="panel-title">大纲</span>
        <div className="panel-header-actions">
          <Button type="text" size="small" icon={isFullscreen ? <CompressOutlined style={{ fontSize: 16 }} /> : <ExpandOutlined style={{ fontSize: 16 }} />} title={isFullscreen ? '退出全屏' : '全屏'} onClick={onToggleFullscreen} />
        </div>
      </div>

      <Tabs
        activeKey={String(activeTab)}
        onChange={(k) => setActiveTab(Number(k))}
        size="small"
        className="outline-tabs"
        indicator={{ size: 20, align: 'center' }}
        items={[
          {
            key: '0',
            label: '大纲',
            children: (
        <div className={`tab-content outline-tab ${viewMode === 'detail' ? 'view-detail' : ''}`}>
          {error && <Alert type="error" message={error} showIcon closable onClose={() => setError('')} className="outline-error-alert" />}

          {viewMode === 'list' ? (
            <>
              {/* 列表视图：全局大纲 + 各章大纲 */}
              <div className="outline-section global-section">
                <div className="section-header">
                  <span className="section-title">总纲</span>
                  {!globalOutline && (
                    <Space size={8} align="center" wrap className="global-section-header-actions">
                      <Tooltip title={bookId == null ? '请先打开一本书籍' : 'Markdown 文本大纲，与章节大纲相同'}>
                        <Button
                          type="default"
                          size="small"
                          icon={<EditOutlined style={{ fontSize: 14 }} />}
                          onClick={() => void handleOpenGlobalMarkdownOnly()}
                          disabled={loading || bookId == null}
                          className="btn-upload"
                        >
                          文本大纲
                        </Button>
                      </Tooltip>
                    </Space>
                  )}
                </div>
                {globalOutline && (
                  <div
                    className={`chapter-outline-item global-outline-item${globalOutline.file_path ? '' : ' no-outline'}${displayOutlineId === globalOutline.id ? ' active' : ''}`}
                    onClick={() => handleOutlineClick(globalOutline)}
                    title={globalOutline.file_path ? undefined : '点击进入大纲详情'}
                  >
                    <span className="item-title" title={globalOutline.title}>
                      <HighlightText text={globalOutline.title} query={workspaceSearchQuery} />
                    </span>
                    <div className="item-actions" onClick={(e) => e.stopPropagation()}>
                      {globalOutline.file_path ? (
                        <>
                          <Tooltip title="打开源文件">
                            <Button type="text" size="small" icon={openingSourceId === globalOutline.id ? <LoadingOutlined style={{ fontSize: 14 }} spin /> : <ExportOutlined style={{ fontSize: 14 }} />} onClick={() => handleOpenSource(globalOutline)} disabled={openingSourceId === globalOutline.id} className="btn-icon-small" />
                          </Tooltip>
                          <Tooltip title="编辑（重新上传 XMind）">
                            <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} onClick={() => handleEdit(globalOutline)} disabled={loading} className="btn-icon-small" />
                          </Tooltip>
                          <Tooltip title="删除">
                            <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={handleDeleteGlobal} disabled={loading} className="btn-icon-small" />
                          </Tooltip>
                        </>
                      ) : (
                        <>
                          <Tooltip title="删除">
                            <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={handleDeleteGlobal} disabled={loading} className="btn-icon-small" />
                          </Tooltip>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="outline-section chapter-section">
                <div className="section-header">
                  <div className="section-title-row">
                    <span className="section-title">{enableVolume ? '卷/章节大纲' : '章节大纲'}</span>
                    <Tooltip title={enableVolume ? '创建卷和章节时自动新建' : '创建写作章节时自动新建'}>
                      <span className="section-info-icon"><InfoCircleOutlined style={{ fontSize: 14 }} /></span>
                    </Tooltip>
                  </div>
                  {!enableVolume && writingChapters.length > 0 && (
                    <div className="section-actions">
                      {chapterBatchMode ? (
                        <>
                          {selectedChapterOutlineIds.size > 0 && (
                            <Tooltip title={`删除(${selectedChapterOutlineIds.size})`}>
                              <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={handleBatchDeleteChapterOutlines} className="outline-batch-delete" />
                            </Tooltip>
                          )}
                          <Button type="text" size="small" onClick={() => { setChapterBatchMode(false); setSelectedChapterOutlineIds(new Set()); }} className="outline-batch-cancel">
                            取消
                          </Button>
                        </>
                      ) : (
                        <Button type="text" size="small" icon={<CheckSquareOutlined style={{ fontSize: 14 }} />} onClick={() => setChapterBatchMode(true)} title="批量操作" className="outline-batch-btn" />
                      )}
                    </div>
                  )}
                </div>
                <div className="chapter-outline-list">
                  {writingChapters.length > 0 ? (
                    enableVolume ? (
                      volumeOutlines.length > 0 ? (
                        volumeOutlines.map((vol: VolumeOutline) => (
                          <React.Fragment key={vol.id}>
                            <div
                              className={`chapter-outline-item outline-volume-header${vol.file_path ? ' has-outline' : ''}${displayOutlineId === vol.id ? ' active' : ''}`}
                              onClick={() => handleOutlineClick(vol)}
                              style={{ cursor: 'pointer' }}
                            >
                              <span className="item-title" title={vol.title}>
                                <HighlightText text={vol.title} query={workspaceSearchQuery} />
                              </span>
                              <div className="item-actions" onClick={(e) => e.stopPropagation()}>
                                {vol.file_path && (
                                  <Tooltip title="打开源文件">
                                    <Button type="text" size="small" icon={openingSourceId === vol.id ? <LoadingOutlined style={{ fontSize: 14 }} spin /> : <ExportOutlined style={{ fontSize: 14 }} />} onClick={() => handleOpenSource(vol)} disabled={openingSourceId === vol.id} className="btn-icon-small" />
                                  </Tooltip>
                                )}
                                <Tooltip title="删除此卷及其所有章节大纲">
                                  <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={(e) => { e.stopPropagation(); handleDeleteVolumeOutline(vol); }} disabled={loading} className="btn-icon-small" />
                                </Tooltip>
                              </div>
                            </div>
                            {vol.chapters.map((chOutline: Outline) => {
                              if (chOutline.file_path) {
                                return (
                                  <div
                                    key={chOutline.id}
                                    className={`chapter-outline-item outline-under-volume ${displayOutlineId === chOutline.id ? 'active' : ''}`}
                                    onClick={() => handleOutlineClick(chOutline)}
                                  >
                                    <span className="item-title" title={chOutline.title}>
                                      <HighlightText text={chOutline.title} query={workspaceSearchQuery} />
                                    </span>
                                    <div className="item-actions" onClick={(e) => e.stopPropagation()}>
                                      {chOutline.file_path && (
                                        <Tooltip title="打开源文件">
                                          <Button type="text" size="small" icon={openingSourceId === chOutline.id ? <LoadingOutlined style={{ fontSize: 14 }} spin /> : <ExportOutlined style={{ fontSize: 14 }} />} onClick={() => handleOpenSource(chOutline)} disabled={openingSourceId === chOutline.id} className="btn-icon-small" />
                                        </Tooltip>
                                      )}
                                      <Tooltip title="编辑（重新上传 XMind）">
                                        <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} onClick={() => handleUploadXmindForOutline(chOutline)} disabled={loading} className="btn-icon-small" />
                                      </Tooltip>
                                      <Tooltip title="删除">
                                        <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={() => handleDelete(chOutline)} disabled={loading} className="btn-icon-small" />
                                      </Tooltip>
                                    </div>
                                  </div>
                                )
                              }
                              return (
                                <div
                                  key={`ch-outline-${chOutline.id}`}
                                  className="chapter-outline-item no-outline outline-under-volume"
                                  title="点击进入大纲详情"
                                  onClick={() => void handleOutlineClick(chOutline)}
                                >
                                  <span className="item-title" title={chOutline.title}>
                                    <HighlightText text={chOutline.title} query={workspaceSearchQuery} />
                                  </span>
                                  <div className="item-actions">
                                    <Tooltip title="删除">
                                      <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={(e) => { e.stopPropagation(); handleDelete(chOutline); }} disabled={loading} className="btn-icon-small" />
                                    </Tooltip>
                                  </div>
                                </div>
                              )
                            })}
                          </React.Fragment>
                        ))
                      ) : (
                        <div className="outline-empty-card">
                          <BookOutlined className="outline-empty-card-icon" />
                          <p className="outline-empty-card-title">暂无卷大纲</p>
                          <p className="outline-empty-card-desc">在章节导航创建卷后自动生成</p>
                        </div>
                      )
                    ) : (
                      writingChapters.map((ch) => {
                        const outline =
                          chapterOutlines.find(
                            (o) =>
                              o.writing_chapter_id != null &&
                              String(o.writing_chapter_id) === String(ch.id),
                          )
                          ?? chapterOutlines.find((o) => o.title === ch.title)
                        if (outline && outline.file_path) {
                          return (
                            <div
                              key={outline.id}
                              className={`chapter-outline-item ${displayOutlineId === outline.id ? 'active' : ''} ${chapterBatchMode && selectedChapterOutlineIds.has(outline.id) ? 'selected' : ''}`}
                              onClick={() => handleOutlineClick(outline)}
                            >
                              {chapterBatchMode && <Checkbox checked={selectedChapterOutlineIds.has(outline.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggleChapterOutlineSelect(outline.id)} className="outline-item-checkbox" />}
                              <span className="item-title" title={outline.title}>
                                <HighlightText text={outline.title} query={workspaceSearchQuery} />
                              </span>
                              <div className="item-actions" onClick={(e) => e.stopPropagation()}>
                                {outline.file_path && (
                                  <Tooltip title="打开源文件">
                                    <Button type="text" size="small" icon={openingSourceId === outline.id ? <LoadingOutlined style={{ fontSize: 14 }} spin /> : <ExportOutlined style={{ fontSize: 14 }} />} onClick={() => handleOpenSource(outline)} disabled={openingSourceId === outline.id} className="btn-icon-small" />
                                  </Tooltip>
                                )}
                                <Tooltip title="编辑">
                                  <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} onClick={() => handleEdit(outline)} disabled={loading} className="btn-icon-small" />
                                </Tooltip>
                                <Tooltip title="删除">
                                  <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={() => handleDelete(outline)} disabled={loading} className="btn-icon-small" />
                                </Tooltip>
                              </div>
                            </div>
                          )
                        }
                        return (
                          <div
                            key={`ch-${ch.id}`}
                            className={`chapter-outline-item no-outline ${outline && selectedChapterOutlineIds.has(outline.id) ? 'selected' : ''}`}
                            title={outline ? '点击进入大纲详情' : undefined}
                            onClick={() => (outline ? void handleOutlineClick(outline) : undefined)}
                          >
                            {chapterBatchMode && outline && (
                              <Checkbox checked={selectedChapterOutlineIds.has(outline.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggleChapterOutlineSelect(outline.id)} className="outline-item-checkbox" />
                            )}
                            <span className="item-title" title={ch.title}>
                              <HighlightText text={ch.title} query={workspaceSearchQuery} />
                            </span>
                            <div className="item-actions">
                              {outline && (
                                <Tooltip title="删除">
                                  <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={(e) => { e.stopPropagation(); handleDelete(outline); }} disabled={loading} className="btn-icon-small" />
                                </Tooltip>
                              )}
                            </div>
                          </div>
                        )
                      })
                    )
                  ) : (
                    <div className="outline-empty-card">
                      <BookOutlined className="outline-empty-card-icon" />
                      <p className="outline-empty-card-title">暂无写作章节</p>
                      <p className="outline-empty-card-desc">在右侧编辑区创建章节后会自动生成</p>
                    </div>
                  )}
                </div>
              </div>

              <div className="outline-section other-section">
                <div className="section-header">
                  <span className="section-title">其他大纲</span>
                  <div className="section-actions">
                    {otherOutlines.length > 0 && (
                      otherBatchMode ? (
                        <>
                          {selectedOtherOutlineIds.size > 0 && (
                            <Tooltip title={`删除(${selectedOtherOutlineIds.size})`}>
                              <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={handleBatchDeleteOtherOutlines} className="outline-batch-delete" />
                            </Tooltip>
                          )}
                          <Button type="text" size="small" onClick={() => { setOtherBatchMode(false); setSelectedOtherOutlineIds(new Set()); }} className="outline-batch-cancel">
                            取消
                          </Button>
                        </>
                      ) : (
                        <Button type="text" size="small" icon={<CheckSquareOutlined style={{ fontSize: 14 }} />} onClick={() => setOtherBatchMode(true)} title="批量操作" className="outline-batch-btn" />
                      )
                    )}
                    <Tooltip title={loading && loadingType === 'other' ? '上传中...' : '新建'}>
                      <Button
                        type="text"
                        size="small"
                        icon={<PlusOutlined style={{ fontSize: 14 }} />}
                        onClick={() => handleUploadXmind('other')}
                        loading={loading && loadingType === 'other'}
                        disabled={loading}
                        className="outline-add-btn"
                      />
                    </Tooltip>
                  </div>
                </div>
                {otherOutlines.length === 0 && !loading && (
                  <div
                    className="outline-empty-card outline-empty-card-action"
                    onClick={() => !loading && handleUploadXmind('other')}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && !loading && handleUploadXmind('other')}
                  >
                    <FileAddOutlined className="outline-empty-card-icon" />
                    <p className="outline-empty-card-title">上传 XMind 大纲</p>
                    <p className="outline-empty-card-desc">点击此处或上方 + 上传</p>
                  </div>
                )}
                <div className="chapter-outline-list">
                  {otherOutlines.map((outline) => (
                      <div
                        key={outline.id}
                        className={`chapter-outline-item ${displayOutlineId === outline.id ? 'active' : ''} ${otherBatchMode && selectedOtherOutlineIds.has(outline.id) ? 'selected' : ''}`}
                        onClick={() => handleOutlineClick(outline)}
                      >
                        {otherBatchMode && <Checkbox checked={selectedOtherOutlineIds.has(outline.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggleOtherOutlineSelect(outline.id)} className="outline-item-checkbox" />}
                        <span className="item-title" title={outline.title}>
                          <HighlightText text={outline.title} query={workspaceSearchQuery} />
                        </span>
                        <div className="item-actions" onClick={(e) => e.stopPropagation()}>
                          {outline.file_path && (
                            <Tooltip title="打开源文件">
                              <Button type="text" size="small" icon={openingSourceId === outline.id ? <LoadingOutlined style={{ fontSize: 14 }} spin /> : <ExportOutlined style={{ fontSize: 14 }} />} onClick={() => handleOpenSource(outline)} disabled={openingSourceId === outline.id} className="btn-icon-small" />
                            </Tooltip>
                          )}
                          <Tooltip title="编辑">
                            <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} onClick={() => handleEdit(outline)} disabled={loading} className="btn-icon-small" />
                          </Tooltip>
                          <Tooltip title="删除">
                            <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} onClick={() => handleDelete(outline)} disabled={loading} className="btn-icon-small" />
                          </Tooltip>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            </>
          ) : (
            /* 详情视图：整个左侧区域展示大纲内容 */
            <div className="outline-detail-view">
              <div className="detail-header">
                <Button
                  type="text"
                  size="small"
                  icon={<ArrowLeftOutlined style={{ fontSize: 14 }} />}
                  onClick={() => {
                    void flushMarkdownSave()
                    setViewMode('list')
                    setSyncedChapterNoOutlineTitle(null)
                  }}
                  title="返回列表"
                  className="btn-back"
                />
                <div className="detail-title-wrap">
                  <span className="detail-title">{currentOutline?.title || syncedChapterNoOutlineTitle || '大纲'}</span>
                  {!syncedChapterNoOutlineTitle && (
                  <div className="detail-switcher-wrap">
                    <Button type="text" size="small" icon={<SwapOutlined style={{ fontSize: 12 }} />} onClick={() => setShowSwitcher((v) => !v)} title="切换大纲" className="btn-switcher" />
                    {showSwitcher && (
                      <div className="switcher-list">
                        {enableVolume ? (
                          <>
                            {globalOutline && (
                              <div
                                key={globalOutline.id}
                                className={`switcher-item ${globalOutline.id === displayOutlineId ? 'active' : ''}`}
                                onClick={() => { handleOutlineClick(globalOutline, false); setShowSwitcher(false) }}
                              >
                                {globalOutline.title}
                              </div>
                            )}
                            {volumeOutlines.map((vol) => (
                              <React.Fragment key={vol.id}>
                                {vol.file_path ? (
                                  <div
                                    className={`switcher-item ${vol.id === displayOutlineId ? 'active' : ''}`}
                                    onClick={() => { handleOutlineClick(vol, false); setShowSwitcher(false) }}
                                  >
                                    {vol.title}
                                  </div>
                                ) : (
                                  <div className="switcher-group-label">{vol.title}</div>
                                )}
                                {vol.chapters.map((ch) => (
                                  <div
                                    key={ch.id}
                                    className={`switcher-item switcher-item-indent ${ch.id === displayOutlineId ? 'active' : ''}`}
                                    onClick={() => { handleOutlineClick(ch, false); setShowSwitcher(false) }}
                                  >
                                    {ch.title}
                                  </div>
                                ))}
                              </React.Fragment>
                            ))}
                            {otherOutlines.map((o) => (
                              <div
                                key={o.id}
                                className={`switcher-item ${o.id === displayOutlineId ? 'active' : ''}`}
                                onClick={() => { handleOutlineClick(o, false); setShowSwitcher(false) }}
                              >
                                {o.title}
                              </div>
                            ))}
                          </>
                        ) : (
                          allOutlines.map((o) => (
                            <div
                              key={o.id}
                              className={`switcher-item ${o.id === displayOutlineId ? 'active' : ''}`}
                              onClick={() => { handleOutlineClick(o, false); setShowSwitcher(false) }}
                            >
                              {o.title}
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>
                  )}
                </div>
                {(currentOutline || syncedChapterNoOutlineTitle) && (
                  <Dropdown
                    menu={{
                      items: [
                        currentOutline?.file_path && {
                          key: 'open',
                          icon: <ExportOutlined style={{ fontSize: 14 }} />,
                          label: openingSourceId === currentOutline.id ? '打开中…' : '打开源文件',
                          disabled: openingSourceId === currentOutline.id,
                          onClick: () => handleOpenSource(currentOutline),
                        },
                        {
                          key: 'edit',
                          icon: <EditOutlined style={{ fontSize: 14 }} />,
                          label: currentOutline?.file_path ? '编辑（重新上传 XMind）' : '上传 XMind 大纲',
                          disabled: loading,
                          onClick: () => (currentOutline?.file_path ? handleEdit(currentOutline!) : openDetailXmindUpload()),
                        },
                        currentOutline && {
                          key: 'delete',
                          icon: <DeleteOutlined style={{ fontSize: 14 }} />,
                          label: '删除',
                          disabled: loading,
                          danger: true,
                          onClick: () => handleDelete(currentOutline),
                        },
                      ].filter(Boolean) as MenuProps['items'],
                    }}
                    trigger={['click']}
                    placement="bottomRight"
                  >
                    <Button type="text" size="small" icon={<EllipsisOutlined style={{ fontSize: 14 }} />} title="更多操作" className="detail-more-btn" />
                  </Dropdown>
                )}
              </div>
              <div className="detail-body">
                {displayLoading ? (
                  <div className="display-area-loading"><Spin description="加载中..." /></div>
                ) : syncedChapterNoOutlineTitle && !currentOutline ? (
                  <div
                    className="outline-empty-card outline-empty-card-action"
                    onClick={() => !loading && handleUploadXmind('chapter', syncedChapterNoOutlineTitle || '')}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === 'Enter' && !loading && handleUploadXmind('chapter', syncedChapterNoOutlineTitle || '')}
                  >
                    <FileAddOutlined className="outline-empty-card-icon" />
                    <p className="outline-empty-card-title">上传 XMind 大纲</p>
                    <p className="outline-empty-card-desc">点击此处或上方编辑图标上传</p>
                  </div>
                ) : !currentOutline ? (
                  <Empty image={false} description="未找到大纲" className="display-area-empty" />
                ) : (
                  <div className="outline-detail-body-inner">
                    <Segmented
                      className="outline-detail-segmented"
                      value={detailOutlineSub}
                      onChange={(v) => setDetailOutlineSub(v as 'xmind' | 'markdown')}
                      options={[
                        { label: '思维导图大纲', value: 'xmind' },
                        { label: '文本大纲', value: 'markdown' },
                      ]}
                      block
                    />
                    {detailOutlineSub === 'xmind' ? (
                      <div className="outline-xmind-panel">
                        {!currentOutline.file_path ? (
                          <div
                            className="outline-empty-card outline-empty-card-action"
                            onClick={() => !loading && openDetailXmindUpload()}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => e.key === 'Enter' && !loading && openDetailXmindUpload()}
                          >
                            <FileAddOutlined className="outline-empty-card-icon" />
                            <p className="outline-empty-card-title">上传 XMind 大纲</p>
                            <p className="outline-empty-card-desc">点击此处或上方菜单上传；也可切换到「文本大纲」编辑 Markdown</p>
                          </div>
                        ) : displayChapters.length === 0 ? (
                          <Empty image={false} description="该大纲暂无思维导图节点" className="display-area-empty" />
                        ) : (
                          <Suspense fallback={<div className="display-area-loading"><Spin description="加载思维导图…" /></div>}>
                            <MindMapView
                              chapters={displayChapters}
                              rootTitle={currentOutline.title}
                              xmindData={currentOutline.xmind_data}
                              filePath={currentOutline.file_path}
                            />
                          </Suspense>
                        )}
                      </div>
                    ) : (
                      <OutlineMarkdownPane
                        ref={outlineMarkdownPaneRef}
                        key={String(displayOutlineId)}
                        outlineId={currentOutline.id}
                        markdownContent={currentOutline.markdown_content ?? null}
                        onSaved={loadData}
                      />
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
            )},
          {
            key: '1',
            label: '人物',
            children: (
              <div className="tab-content character-tab-wrapper">
                <CharacterTab bookId={bookId ?? null} />
              </div>
            ),
          },
          {
            key: '2',
            label: '小说背景',
            children: (
              <div className="tab-content story-background-tab-wrapper">
                <StoryBackgroundTab bookId={bookId ?? null} />
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}


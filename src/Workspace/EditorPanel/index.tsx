/// <reference path="../../vite-env.d.ts" />
import React from 'react'
import {
  ExpandOutlined, CompressOutlined, MenuFoldOutlined,
  PlusOutlined, EditOutlined, DeleteOutlined, CloseOutlined, CheckSquareOutlined, ExportOutlined,
  UndoOutlined, RedoOutlined,
} from '@ant-design/icons'
import { App as AntdApp, Button, Input, Empty, Checkbox, Tooltip } from 'antd'
import type { InputRef } from 'antd/es/input/Input'
import type { AiModelConfig, Chapter, EntityId, Outline } from '../../types'
import { useWorkspace } from '../WorkspaceContext'
import { HighlightText } from '../search/highlightText'
import ConfirmModal from '../../components/ConfirmModal'
import LexicalEditorComponent, { type LexicalEditorHandle } from './LexicalEditor'
import ExportModal from '../../components/ExportModal'
import { buildExportEntries } from '../../utils/exportBooks'
import type { ExportChapter } from '../../utils/exportBooks'
import './index.scss'

const AUTOSAVE_DELAY = 800

async function ensureDefaultOutline(bookId?: EntityId | null): Promise<Outline | null> {
  const res = await window.electronAPI.getWritingOutline(bookId)
  if (res.success && res.data) return res.data
  return null
}

interface AiFloatState {
  visible: boolean; x: number; y: number
  prompt: string; loading: boolean; result: string
}

interface EditorPanelProps {
  bookTitle: string
  /** 非分卷模式 OR 分卷模式下均通过此回调通知父组件创建大纲 */
  onItemCreated?: (chapterId: EntityId, title: string, isVolume: boolean, parentWritingChapterId: EntityId | null) => void
  /** 删除写作章节时，通过 writingChapterId 通知父组件删除对应大纲 */
  onWritingChapterDeleted?: (writingChapterId: EntityId) => void
  modelConfigs?: AiModelConfig[]
  isFullscreen: boolean
  onToggleFullscreen: () => void
  /** 工作台搜索：注册 Lexical 实例 */
  onLexicalEditor?: (editor: import('lexical').LexicalEditor | null) => void
}

export default function EditorPanel({
  bookTitle,
  onItemCreated,
  onWritingChapterDeleted,
  modelConfigs = [],
  isFullscreen,
  onToggleFullscreen,
  onLexicalEditor,
}: EditorPanelProps) {
  const { message: appMessage } = AntdApp.useApp()
  const {
    writingChapters: chapters,
    activeChapterId: chapterId,
    activeChapterTitle: chapterTitle,
    writingOutlineId,
    bookId,
    enableVolume,
    setActiveChapter: onChapterSelect,
    setChaptersData: onChaptersChange,
    workspaceSearchQuery,
    notifyWorkspaceSearchContentChanged,
  } = useWorkspace()
  const searchNotifyTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  // ─── 编辑器状态 ───────────────────────────────────────────
  const [content, setContent] = React.useState('')
  const [saveStatus, setSaveStatus] = React.useState('已保存')
  const [wordCount, setWordCount] = React.useState(0)
  const [navCollapsed, setNavCollapsed] = React.useState(false)

  // ─── 通用添加/重命名/删除状态 ─────────────────────────────
  const [editingChapterId, setEditingChapterId] = React.useState<EntityId | null>(null)
  const [editingTitle, setEditingTitle] = React.useState('')
  const addingRef = React.useRef(false)

  // ─── 非分卷模式：添加章节 ─────────────────────────────────
  const [showAddInput, setShowAddInput] = React.useState(false)
  const [newTitle, setNewTitle] = React.useState('')
  const addInputRef = React.useRef<InputRef>(null)

  // ─── 分卷模式：添加卷 ─────────────────────────────────────
  const [addingVolume, setAddingVolume] = React.useState(false)
  const [newVolSubtitle, setNewVolSubtitle] = React.useState('')
  const addVolRef = React.useRef<InputRef>(null)

  // ─── 分卷模式：添加章节（归属某卷） ──────────────────────
  const [addingChapterVolId, setAddingChapterVolId] = React.useState<EntityId | null>(null)
  const [newChapterSubtitle, setNewChapterSubtitle] = React.useState('')
  const addChapterRef = React.useRef<InputRef>(null)

  // ─── 分卷模式：折叠卷 ────────────────────────────────────
  const [collapsedVolIds, setCollapsedVolIds] = React.useState<Set<EntityId>>(new Set())

  // ─── 批量删除 ────────────────────────────────────────────
  type DeleteModal = { chapter: Chapter; onConfirm: (checked: boolean) => void; checkboxLabel?: string }
  const [deleteModal, setDeleteModal] = React.useState<DeleteModal | null>(null)
  type BatchDeleteModal = { ids: EntityId[]; onConfirm: () => void }
  const [batchDeleteModal, setBatchDeleteModal] = React.useState<BatchDeleteModal | null>(null)
  const [batchMode, setBatchMode] = React.useState(false)
  const [selectedIds, setSelectedIds] = React.useState<Set<EntityId>>(new Set())

  // ─── 导出章节 ─────────────────────────────────────────────
  const [exportModalOpen, setExportModalOpen] = React.useState(false)
  const [exportSelectedIds, setExportSelectedIds] = React.useState<EntityId[]>([])
  const [exportLoading, setExportLoading] = React.useState(false)

  // ─── AI 浮窗 ─────────────────────────────────────────────
  const [aiFloat, setAiFloat] = React.useState<AiFloatState>(
    { visible: false, x: 0, y: 0, prompt: '', loading: false, result: '' }
  )

  const saveTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastChapterIdRef = React.useRef<EntityId | null>(null)
  const lexicalEditorRef = React.useRef<LexicalEditorHandle>(null)

  // ─── 派生数据：分卷模式 ───────────────────────────────────
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

  // ─── 加载/保存文章 ────────────────────────────────────────
  const refreshArticle = React.useCallback((cid: EntityId) => {
    window.electronAPI.getArticle({ chapterId: cid }).then((res) => {
      const text = res.success && res.data ? res.data.content : ''
      setContent(text)
      setWordCount(text.replace(/\s/g, '').length)
    })
  }, [])

  React.useEffect(() => {
    if (chapterId === lastChapterIdRef.current) return
    lastChapterIdRef.current = chapterId
    if (!chapterId) { setContent(''); setWordCount(0); return }
    refreshArticle(chapterId)
  }, [chapterId, refreshArticle])

  // AI 通过 editChapterContent 保存某章后发出事件，若当前正在编辑该章则立即刷新
  React.useEffect(() => {
    const handler = (e: Event) => {
      const { chapterId: updatedId } = (e as CustomEvent<{ chapterId: EntityId }>).detail ?? {}
      if (updatedId != null && updatedId === chapterId) refreshArticle(updatedId)
    }
    window.addEventListener('chapter-content-updated', handler)
    return () => window.removeEventListener('chapter-content-updated', handler)
  }, [chapterId, refreshArticle])

  React.useEffect(() => {
    if (showAddInput) addInputRef.current?.focus()
  }, [showAddInput])

  React.useEffect(() => {
    if (addingVolume) addVolRef.current?.focus()
  }, [addingVolume])

  React.useEffect(() => {
    if (addingChapterVolId != null) addChapterRef.current?.focus()
  }, [addingChapterVolId])

  const scheduleAutoSave = React.useCallback((text: string) => {
    if (!chapterId) return
    setSaveStatus('保存中...')
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      const res = await window.electronAPI.saveArticle({ chapterId, content: text })
      setSaveStatus(res.success ? '已保存' : '保存失败')
    }, AUTOSAVE_DELAY)
  }, [chapterId])

  const handleContentChange = React.useCallback((text: string) => {
    setContent(text)
    setWordCount(text.replace(/\s/g, '').length)
    scheduleAutoSave(text)
    if (searchNotifyTimerRef.current) clearTimeout(searchNotifyTimerRef.current)
    searchNotifyTimerRef.current = setTimeout(() => {
      notifyWorkspaceSearchContentChanged()
    }, 200)
  }, [scheduleAutoSave, notifyWorkspaceSearchContentChanged])

  const handleKeyTrigger = React.useCallback((key: string, rect: DOMRect) => {
    if (key === 'backslash') {
      setAiFloat({ visible: true, x: rect.left + 40, y: rect.top + 60, prompt: '', loading: false, result: '' })
    }
    if (key === 'escape') {
      setAiFloat({ visible: false, x: 0, y: 0, prompt: '', loading: false, result: '' })
    }
  }, [])

  const closeAiFloat = () => {
    setAiFloat({ visible: false, x: 0, y: 0, prompt: '', loading: false, result: '' })
  }

  const firstModelConfig = modelConfigs[0]
  const handleAiFloatSubmit = () => {
    if (!aiFloat.prompt.trim()) return
    if (!firstModelConfig?.apiKey?.trim()) {
      setAiFloat((prev) => ({ ...prev, result: '请先在设置中添加模型并填写 API Key', loading: false }))
      return
    }
    setAiFloat((prev) => ({ ...prev, loading: true, result: '' }))

    const unsubscribe = window.electronAPI.onAiChunk((chunk) => {
      if (chunk.toolRouterWarning) {
        appMessage.warning(chunk.toolRouterWarning)
      }
      if (chunk.error) {
        setAiFloat((prev) => ({ ...prev, loading: false, result: '请求失败：' + chunk.error }))
        unsubscribe()
        return
      }
      if (chunk.delta) {
        setAiFloat((prev) => ({ ...prev, result: prev.result + chunk.delta }))
      }
      if (chunk.done) {
        setAiFloat((prev) => ({ ...prev, loading: false }))
        unsubscribe()
      }
    })

    window.electronAPI.aiChatStream({
      apiKey: firstModelConfig.apiKey,
      baseURL: firstModelConfig.baseUrl || undefined,
      apiProvider:
        firstModelConfig.apiProvider === 'anthropic' ? 'anthropic' : 'openai',
      messages: [
        { role: 'system', content: '你是一位专业写作助手，请根据用户需求提供写作建议或内容。' },
        { role: 'user', content: aiFloat.prompt },
      ],
      options: {
        model: firstModelConfig.name,
        ...(firstModelConfig.customizeTemperature === false
          ? {}
          : { temperature: firstModelConfig.temperatureNonThinking ?? 0.6 }),
      },
    })
  }

  // ─── 获取写作大纲 ID ──────────────────────────────────────
  const getOutlineId = async (): Promise<EntityId | null> => {
    if (writingOutlineId) return writingOutlineId
    const outline = await ensureDefaultOutline(bookId)
    return outline ? outline.id : null
  }

  const reloadChapters = async (outlineId: EntityId) => {
    const chapRes = await window.electronAPI.getChapters({ outlineId })
    if (chapRes.success) onChaptersChange?.(outlineId, chapRes.data)
  }

  // ─── 非分卷：新建章节 ─────────────────────────────────────
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

  // ─── 分卷：新建卷 ─────────────────────────────────────────
  const handleAddVolume = async () => {
    if (addingRef.current) return
    const subtitle = newVolSubtitle.trim()
    const num = volumes.length + 1
    const title = subtitle ? `第${num}卷 ${subtitle}` : `第${num}卷`
    addingRef.current = true
    try {
      const outlineId = await getOutlineId()
      if (!outlineId) return
      const res = await window.electronAPI.addChapter({ outlineId, title, parentId: undefined })
      if (res.success) {
        await reloadChapters(outlineId)
        setNewVolSubtitle('')
        setAddingVolume(false)
        onItemCreated?.(res.data.id, title, true, null)
      }
    } finally { addingRef.current = false }
  }

  // ─── 分卷：在某卷下新建章节 ───────────────────────────────
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

  // ─── 重命名 ───────────────────────────────────────────────
  const handleRenameChapter = async (ch: Chapter) => {
    const title = editingTitle.trim()
    if (!title || title === ch.title) { setEditingChapterId(null); return }
    await window.electronAPI.renameChapter({ id: ch.id, title })
    setEditingChapterId(null)
    if (writingOutlineId) await reloadChapters(writingOutlineId)
    if (chapterId === ch.id) onChapterSelect?.(ch.id, title)
  }

  // ─── 删除单个（卷/章节） ──────────────────────────────────
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

  // ─── 批量删除 ────────────────────────────────────────────
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

  // ─── 导出章节 ─────────────────────────────────────────────
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
          selectedIds.map(async (chapterId) => {
            const ch = idToChapter.get(chapterId)
            const res = await window.electronAPI.getArticle({ chapterId })
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
    [bookTitle, enableVolume, idToChapter]
  )

  // ─── 渲染：重命名输入框 ───────────────────────────────────
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

  // ─── 渲染：分卷模式 ───────────────────────────────────────
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
            {/* 卷行 */}
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
                    <Button type="text" size="small" icon={<EditOutlined style={{ fontSize: 14 }} />} title="重命名"
                      onClick={() => { setEditingChapterId(vol.id); setEditingTitle(vol.title) }}
                      className="nav-action-btn" />
                    <Button type="text" size="small" icon={<DeleteOutlined style={{ fontSize: 14 }} />} title="删除"
                      onClick={() => handleDeleteItem(vol)} className="nav-action-btn" />
                  </div>
                </>
              )}
            </div>

            {/* 卷内章节 */}
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

                {/* 在此卷下新建章节的输入框 */}
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

      {/* 新建卷输入框 */}
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

  // ─── 渲染：非分卷模式 ─────────────────────────────────────
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

  return (
    <div className={`editor-panel ${isFullscreen ? 'fullscreen' : ''}`}>
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

      <div className="panel-header">
        <span className="panel-title">{chapterTitle || '选择章节开始写作'}</span>
        <div className="panel-header-actions">
          <Tooltip title="导出章节">
            <Button type="text" size="small" icon={<ExportOutlined style={{ fontSize: 16 }} />} onClick={openExportModal} />
          </Tooltip>
          <Button type="text" size="small"
            icon={isFullscreen ? <CompressOutlined style={{ fontSize: 16 }} /> : <ExpandOutlined style={{ fontSize: 16 }} />}
            title={isFullscreen ? '退出全屏' : '全屏'} onClick={onToggleFullscreen} />
        </div>
      </div>

      <div className="editor-body">
        <div className={`editor-nav ${navCollapsed ? 'collapsed' : ''}`}>
          {navCollapsed && (
            <div className="nav-collapse-bar" onClick={() => setNavCollapsed(false)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setNavCollapsed(false)} title="展开导航" />
          )}
          {!navCollapsed && (
            <div className="nav-content">
              <div className="nav-title-row">
                <span className="nav-title">章节</span>
                <div className="nav-title-actions">
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
                  {!isFullscreen && (
                    <Button type="text" size="small"
                      icon={<MenuFoldOutlined style={{ fontSize: 14 }} />} title="收起导航"
                      onClick={() => setNavCollapsed(true)} className="nav-toggle-inline" />
                  )}
                </div>
              </div>

              {enableVolume ? renderVolumeNav() : renderFlatNav()}
            </div>
          )}
        </div>

        <div className="editor-main">
          {!chapterId ? (
            <Empty image={false} description={
              <><p>从导航选择章节</p><small>点击导航中的章节可切换，输入 <kbd>\</kbd> 可唤起 AI 助手</small></>
            } className="editor-empty" />
          ) : (
            <>
              <LexicalEditorComponent
                ref={lexicalEditorRef}
                key={chapterId}
                value={content}
                chapterId={chapterId}
                onChange={handleContentChange}
                onKeyTrigger={handleKeyTrigger}
                placeholder={`开始写作「${chapterTitle}」... 提示：输入 \\ 可唤起 AI 助手`}
                className="editor-lexical-wrap"
                onLexicalEditor={onLexicalEditor}
              />
              <div className="editor-footer">
                <div className="editor-footer-left">
                  <Tooltip title="撤回 (Ctrl+Z)">
                    <Button type="text" size="small" icon={<UndoOutlined />} className="editor-toolbar-btn"
                      onClick={() => lexicalEditorRef.current?.undo()} />
                  </Tooltip>
                  <Tooltip title="前进 (Ctrl+Shift+Z)">
                    <Button type="text" size="small" icon={<RedoOutlined />} className="editor-toolbar-btn"
                      onClick={() => lexicalEditorRef.current?.redo()} />
                  </Tooltip>
                </div>
                <div className="editor-footer-right">
                  <span className="word-count">{wordCount} 字</span>
                  <span className={`save-status ${saveStatus === '保存失败' ? 'save-error' : ''}`}>{saveStatus}</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {aiFloat.visible && (
        <AiFloatBox
          x={aiFloat.x} y={aiFloat.y}
          prompt={aiFloat.prompt} loading={aiFloat.loading} result={aiFloat.result}
          onPromptChange={(v) => setAiFloat((prev) => ({ ...prev, prompt: v }))}
          onSubmit={handleAiFloatSubmit}
          onClose={closeAiFloat}
        />
      )}
    </div>
  )
}

interface AiFloatBoxProps {
  x: number; y: number; prompt: string; loading: boolean; result: string
  onPromptChange: (v: string) => void; onSubmit: () => void; onClose: () => void
}

function AiFloatBox({ x, y, prompt, loading, result, onPromptChange, onSubmit, onClose }: AiFloatBoxProps) {
  const inputRef = React.useRef<InputRef>(null)
  React.useEffect(() => { inputRef.current?.focus() }, [])
  return (
    <div className="ai-float-box" style={{ left: x, top: y }}>
      <div className="ai-float-header">
        <span>✨ AI 写作助手</span>
        <Button type="text" size="small" icon={<CloseOutlined style={{ fontSize: 16 }} />} onClick={onClose} className="btn-close" />
      </div>
      <div className="ai-float-input-row">
        <Input
          ref={inputRef} className="ai-float-input"
          value={prompt} onChange={(e) => onPromptChange(e.target.value)}
          placeholder="描述你的写作需求..."
          onKeyDown={(e) => { if (e.key === 'Enter') onSubmit(); if (e.key === 'Escape') onClose() }}
          disabled={loading}
        />
        <Button type="primary" size="small" onClick={onSubmit} loading={loading}>
          {loading ? '生成中' : '生成'}
        </Button>
      </div>
      {(loading || result) && (
        <div className="ai-float-result">
          {loading ? <span className="a-loading-dots">思考中...</span> : result}
        </div>
      )}
    </div>
  )
}


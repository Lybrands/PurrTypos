import React, { Suspense, lazy } from 'react'
import { ArrowLeftOutlined, HomeOutlined } from '@ant-design/icons'
import { Button, Tooltip, Spin } from 'antd'
import type { LexicalEditor } from 'lexical'
import AppHeader from '../components/AppHeader'
import type { Chapter, AiModelConfig } from '../types'
import { editorStateToText } from './EditorPanel/LexicalEditor'
import { findAllMatchStarts, selectLexicalSearchMatch } from './search/lexicalSearch'
import { getWritingOutlineWithChapters } from './utils'
import WorkspaceContext from './WorkspaceContext'
import type { WorkspaceContextValue } from './WorkspaceContext'
import WorkspaceSearchPanel from './WorkspaceSearchPanel'
import './workspaceSearch.scss'

const OutlinePanel = lazy(() => import('./OutlinePanel'))
const EditorPanel = lazy(() => import('./EditorPanel'))
const AiPanel = lazy(() => import('./AiPanel'))

const PanelFallback = () => (
  <div className="workspace-panel-fallback"><Spin size="small" /></div>
)

type PanelType = 'left' | 'editor' | 'ai'

const WORKSPACE_PANEL_STORAGE_KEY = 'purrtypos_workspace_panel_state'

function loadPanelState(): { leftCollapsed: boolean; rightCollapsed: boolean; editorCollapsed: boolean } {
  try {
    const raw = localStorage.getItem(WORKSPACE_PANEL_STORAGE_KEY)
    if (raw) {
      const p = JSON.parse(raw) as Record<string, boolean>
      return {
        leftCollapsed: !!p.leftCollapsed,
        rightCollapsed: !!p.rightCollapsed,
        editorCollapsed: !!p.editorCollapsed,
      }
    }
  } catch {
    // ignore
  }
  return { leftCollapsed: false, rightCollapsed: false, editorCollapsed: false }
}

function savePanelState(state: { leftCollapsed: boolean; rightCollapsed: boolean; editorCollapsed: boolean }) {
  try {
    localStorage.setItem(WORKSPACE_PANEL_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // ignore
  }
}

interface WorkspaceProps {
  bookId?: number | null
  bookTitle?: string
  enableVolume?: boolean
  onBack?: () => void
  onGoHome?: () => void
  onOpenSettings?: () => void
  modelConfigs?: AiModelConfig[]
  syncOutlineChapter?: boolean
  systemPrompt?: string
}

export default function Workspace({ bookId, bookTitle, enableVolume = false, onBack, onGoHome, onOpenSettings, modelConfigs = [], syncOutlineChapter = false, systemPrompt = '' }: WorkspaceProps = {}) {
  const [leftWidth, setLeftWidth] = React.useState(25)
  const [editorWidth, setEditorWidth] = React.useState(60)
  const [fullscreen, setFullscreen] = React.useState<PanelType | null>(null)

  const initialPanel = React.useMemo(loadPanelState, [])
  const [leftCollapsed, setLeftCollapsed] = React.useState(initialPanel.leftCollapsed)
  const [rightCollapsed, setRightCollapsed] = React.useState(initialPanel.rightCollapsed)
  const [editorCollapsed, setEditorCollapsed] = React.useState(initialPanel.editorCollapsed)

  React.useEffect(() => {
    savePanelState({ leftCollapsed, rightCollapsed, editorCollapsed })
  }, [leftCollapsed, rightCollapsed, editorCollapsed])

  const [writingOutlineId, setWritingOutlineId] = React.useState<number | null>(null)
  const [writingChapters, setWritingChapters] = React.useState<Chapter[]>([])

  const [activeWritingChapterId, setActiveWritingChapterId] = React.useState<number | null>(null)
  const [activeWritingChapterTitle, setActiveWritingChapterTitle] = React.useState('')

  const [outlineRefreshKey, setOutlineRefreshKey] = React.useState(0)
  const [skipAutoOpenOutlineTitle, setSkipAutoOpenOutlineTitle] = React.useState<string | null>(null)

  const containerRef = React.useRef<HTMLDivElement>(null)
  const isDraggingMain = React.useRef(false)
  const isDraggingRight = React.useRef(false)
  const refreshOutlineListRef = React.useRef<(() => void) | null>(null)

  const lexicalEditorRef = React.useRef<LexicalEditor | null>(null)
  const setLexicalEditorRef = React.useCallback((e: LexicalEditor | null) => {
    lexicalEditorRef.current = e
  }, [])

  const [workspaceSearchQuery, setWorkspaceSearchQuery] = React.useState('')
  const [workspaceSearchActiveIndex, setWorkspaceSearchActiveIndex] = React.useState(0)
  const [workspaceSearchMatchTotal, setWorkspaceSearchMatchTotal] = React.useState(0)
  const [searchContentVersion, setSearchContentVersion] = React.useState(0)
  const notifyWorkspaceSearchContentChanged = React.useCallback(() => {
    setSearchContentVersion((v) => v + 1)
  }, [])

  React.useEffect(() => {
    setWorkspaceSearchActiveIndex(0)
  }, [workspaceSearchQuery])

  const goToNextWorkspaceSearch = React.useCallback(() => {
    setWorkspaceSearchActiveIndex((i) => {
      const t = Math.max(workspaceSearchMatchTotal, 1)
      return (i + 1) % t
    })
  }, [workspaceSearchMatchTotal])

  const goToPrevWorkspaceSearch = React.useCallback(() => {
    setWorkspaceSearchActiveIndex((i) => {
      const t = Math.max(workspaceSearchMatchTotal, 1)
      return (i - 1 + t) % t
    })
  }, [workspaceSearchMatchTotal])

  React.useEffect(() => {
    if (workspaceSearchMatchTotal > 0 && workspaceSearchActiveIndex >= workspaceSearchMatchTotal) {
      setWorkspaceSearchActiveIndex(0)
    }
  }, [workspaceSearchMatchTotal, workspaceSearchActiveIndex])

  const handleMainDividerMouseDown = React.useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDraggingMain.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  const handleRightDividerMouseDown = React.useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isDraggingRight.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [])

  React.useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()

      if (isDraggingMain.current) {
        const newLeft = ((e.clientX - rect.left) / rect.width) * 100
        setLeftWidth(Math.min(Math.max(newLeft, 15), 45))
      }

      if (isDraggingRight.current) {
        const rightPanelLeft = rect.left + (rect.width * leftWidth) / 100
        const rightPanelWidth = rect.width - (rightPanelLeft - rect.left)
        const newEditor = ((e.clientX - rightPanelLeft) / rightPanelWidth) * 100
        setEditorWidth(Math.min(Math.max(newEditor, 50), 90))
      }
    }

    const onMouseUp = () => {
      isDraggingMain.current = false
      isDraggingRight.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    return () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
  }, [leftWidth])

  const loadWritingChapters = React.useCallback(async () => {
    const data = await getWritingOutlineWithChapters(bookId)
    if (!data) return
    setWritingOutlineId(data.outlineId)
    setWritingChapters(data.chapters)
  }, [bookId])

  React.useEffect(() => {
    loadWritingChapters()
  }, [loadWritingChapters])

  const toggleFullscreen = (panel: PanelType) => {
    setFullscreen((prev) => (prev === panel ? null : panel))
  }

  const handleItemCreated = React.useCallback(
    async (chapterId: number, title: string, isVolume: boolean, parentWritingChapterId: number | null) => {
      if (!isVolume) {
        setSkipAutoOpenOutlineTitle(title)
      }

      if (!enableVolume) {
        await window.electronAPI.saveOutline({
          title, type: 'chapter', book_id: bookId ?? null, writing_chapter_id: chapterId,
        })
      } else if (isVolume) {
        await window.electronAPI.saveOutline({
          title, type: 'volume', book_id: bookId ?? null, writing_chapter_id: chapterId,
        })
      } else {
        let parentOutlineId: number | null = null
        if (parentWritingChapterId != null) {
          const res = await window.electronAPI.getOutlineByWritingChapter(parentWritingChapterId)
          if (res.success && res.data) parentOutlineId = res.data.id
        }
        await window.electronAPI.saveOutline({
          title, type: 'chapter', book_id: bookId ?? null,
          writing_chapter_id: chapterId, parent_outline_id: parentOutlineId,
        })
      }
      setOutlineRefreshKey((k) => k + 1)
      refreshOutlineListRef.current?.()
    },
    [enableVolume, bookId]
  )

  React.useEffect(() => {
    if (skipAutoOpenOutlineTitle != null && activeWritingChapterTitle !== skipAutoOpenOutlineTitle) {
      setSkipAutoOpenOutlineTitle(null)
    }
  }, [activeWritingChapterTitle, skipAutoOpenOutlineTitle])

  const handleChapterOutlineDeleted = React.useCallback(async (title: string) => {
    if (!writingOutlineId) return
    const chapRes = await window.electronAPI.getChapters({ outlineId: writingOutlineId })
    if (!chapRes.success) return
    const matched = chapRes.data.find((c) => c.title === title)
    if (!matched) return
    await window.electronAPI.deleteChapter({ id: matched.id })
    loadWritingChapters()
    setActiveWritingChapterId((prev) => (prev === matched.id ? null : prev))
    setActiveWritingChapterTitle((prev) => (prev === title ? '' : prev))
  }, [writingOutlineId, loadWritingChapters])

  const handleWritingChapterDeleted = React.useCallback(async (writingChapterId: number) => {
    const res = await window.electronAPI.getOutlineByWritingChapter(writingChapterId)
    if (!res.success || !res.data) return
    await window.electronAPI.deleteOutline({ outlineId: res.data.id })
    setOutlineRefreshKey((k) => k + 1)
    refreshOutlineListRef.current?.()
  }, [])

  const handleChapterOutlineSelect = React.useCallback(
    (info: { title: string; writingChapterId?: number | null }) => {
      if (!syncOutlineChapter) return
      const wcId = info.writingChapterId
      if (wcId != null && Number.isFinite(Number(wcId))) {
        const ch = writingChapters.find((c) => c.id === Number(wcId))
        if (ch) {
          setActiveWritingChapterId(ch.id)
          setActiveWritingChapterTitle(ch.title)
          return
        }
      }
      if (!writingChapters.length) return
      const ch = writingChapters.find((c) => c.title === info.title)
      if (ch) {
        setActiveWritingChapterId(ch.id)
        setActiveWritingChapterTitle(ch.title)
      }
    },
    [syncOutlineChapter, writingChapters]
  )

  // ─── Context 方法（useCallback 保证引用稳定）──────────────────
  const handleWritingSelect = React.useCallback((id: number, title: string) => {
    setActiveWritingChapterId(id)
    setActiveWritingChapterTitle(title || '')
  }, [])

  const handleWritingChaptersChange = React.useCallback((outlineId: number, chapterList: Chapter[]) => {
    setWritingOutlineId(outlineId)
    setWritingChapters(chapterList || [])
  }, [])

  // ─── WorkspaceContext 值 ──────────────────────────────────────
  const workspaceContextValue = React.useMemo<WorkspaceContextValue>(() => ({
    bookId: bookId ?? null,
    bookTitle: bookTitle ?? '',
    enableVolume,
    syncOutlineChapter,
    activeChapterId: activeWritingChapterId,
    activeChapterTitle: activeWritingChapterTitle,
    writingChapters,
    writingOutlineId,
    setActiveChapter: handleWritingSelect,
    setChaptersData: handleWritingChaptersChange,
    loadWritingChapters,
    workspaceSearchQuery,
    setWorkspaceSearchQuery,
    workspaceSearchActiveIndex,
    setWorkspaceSearchActiveIndex,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged,
    lexicalEditorRef,
    setLexicalEditorRef,
    workspaceSearchMatchTotal,
  }), [
    bookId, bookTitle, enableVolume, syncOutlineChapter,
    activeWritingChapterId, activeWritingChapterTitle,
    writingChapters, writingOutlineId,
    handleWritingSelect, handleWritingChaptersChange, loadWritingChapters,
    workspaceSearchQuery,
    workspaceSearchActiveIndex,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged,
    workspaceSearchMatchTotal,
  ])

  React.useLayoutEffect(() => {
    const q = workspaceSearchQuery.trim()
    document.querySelectorAll('.workspace-search-hit--active').forEach((el) => {
      el.classList.remove('workspace-search-hit--active')
    })
    if (!q) {
      setWorkspaceSearchMatchTotal(0)
      return
    }
    const scope = document.getElementById('workspace-search-scope')
    if (!scope) return
    const domHits = [...scope.querySelectorAll('.workspace-search-include [data-ws-search-hit]')]
    domHits.forEach((el) => el.classList.remove('workspace-search-hit--active'))
    const lex = lexicalEditorRef.current
    let lexCount = 0
    if (lex) {
      const flat = editorStateToText(lex.getEditorState())
      lexCount = findAllMatchStarts(flat, q).length
    }
    const total = domHits.length + lexCount
    setWorkspaceSearchMatchTotal(total)
    if (total === 0) return

    const idx = ((workspaceSearchActiveIndex % total) + total) % total
    if (idx < domHits.length) {
      domHits[idx].classList.add('workspace-search-hit--active')
      domHits[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    } else if (lex) {
      selectLexicalSearchMatch(lex, q, idx - domHits.length)
    }
  }, [
    workspaceSearchQuery,
    workspaceSearchActiveIndex,
    searchContentVersion,
    outlineRefreshKey,
    activeWritingChapterId,
  ])

  // ─── 面板样式 ─────────────────────────────────────────────────
  const getLeftStyle = (): React.CSSProperties => {
    if (fullscreen === 'left') return { width: '100%', minWidth: 0 }
    if (fullscreen === 'editor' || fullscreen === 'ai') return { width: 0, overflow: 'hidden', minWidth: 0 }
    if (leftCollapsed) return { width: 0, overflow: 'hidden', minWidth: 0 }
    return { width: `${leftWidth}%`, minWidth: 0 }
  }

  const getRightStyle = (): React.CSSProperties => {
    if (fullscreen === 'left') return { width: 0, overflow: 'hidden', minWidth: 0 }
    if (fullscreen === 'editor' || fullscreen === 'ai') return { flex: 1, minWidth: 0 }
    return { flex: 1, minWidth: 0 }
  }

  const getEditorStyle = (): React.CSSProperties => {
    if (fullscreen === 'editor') return { width: '100%', minWidth: 0 }
    if (fullscreen === 'ai') return { width: 0, overflow: 'hidden', minWidth: 0 }
    if (editorCollapsed) return { width: 0, overflow: 'hidden', minWidth: 0 }
    if (rightCollapsed) return { flex: 1, minWidth: 0 }
    return { width: `${editorWidth}%`, minWidth: 0 }
  }

  const getAiStyle = (): React.CSSProperties => {
    if (fullscreen === 'ai') return { width: '100%', minWidth: 0 }
    if (fullscreen === 'editor') return { width: 0, overflow: 'hidden', minWidth: 0 }
    if (rightCollapsed) return { width: 0, overflow: 'hidden', minWidth: 0 }
    if (editorCollapsed) return { flex: 1, minWidth: 0 }
    return { flex: 1, minWidth: 0 }
  }

  return (
    <WorkspaceContext.Provider value={workspaceContextValue}>
      <AppHeader
        title={bookTitle || 'PurrTypos'}
        left={
          <>
            {onGoHome && (
              <Tooltip title="返回首页">
                <Button
                  type="text"
                  size="small"
                  icon={<HomeOutlined style={{ fontSize: 14 }} />}
                  onClick={onGoHome}
                />
              </Tooltip>
            )}
            {onBack && (
              <Tooltip title="返回书架">
                <Button
                  type="text"
                  size="small"
                  icon={<ArrowLeftOutlined style={{ fontSize: 14 }} />}
                  onClick={onBack}
                />
              </Tooltip>
            )}
          </>
        }
        showActions
        leftCollapsed={leftCollapsed}
        rightCollapsed={rightCollapsed}
        editorCollapsed={editorCollapsed}
        onToggleLeft={() => setLeftCollapsed((v) => !v)}
        onToggleRight={() => setRightCollapsed((v) => !v)}
        onToggleEditor={() => setEditorCollapsed((v) => !v)}
        onOpenSettings={onOpenSettings}
      />

      <WorkspaceSearchPanel />

      <div className="app-body" id="workspace-search-scope" ref={containerRef}>
        <div className="panel panel-left workspace-search-include" style={getLeftStyle()}>
          <Suspense fallback={<PanelFallback />}>
            <OutlinePanel
              isFullscreen={fullscreen === 'left'}
              onToggleFullscreen={() => toggleFullscreen('left')}
              onChapterOutlineDeleted={handleChapterOutlineDeleted}
              onChapterOutlineSelect={handleChapterOutlineSelect}
              skipAutoOpenForTitle={skipAutoOpenOutlineTitle}
              refreshKey={outlineRefreshKey}
              onRefreshReady={(refresh) => { refreshOutlineListRef.current = refresh }}
            />
          </Suspense>
        </div>

        {fullscreen === null && !leftCollapsed && (
          <div className="divider divider-vertical" onMouseDown={handleMainDividerMouseDown} />
        )}


        <div className="panel-right" style={getRightStyle()}>
          <div className="panel panel-editor workspace-search-include" style={getEditorStyle()}>
            <Suspense fallback={<PanelFallback />}>
              <EditorPanel
                bookTitle={bookTitle ?? ''}
                onItemCreated={handleItemCreated}
                onWritingChapterDeleted={handleWritingChapterDeleted}
                modelConfigs={modelConfigs}
                isFullscreen={fullscreen === 'editor'}
                onToggleFullscreen={() => toggleFullscreen('editor')}
                onLexicalEditor={setLexicalEditorRef}
              />
            </Suspense>
          </div>

          {fullscreen === null && !rightCollapsed && !editorCollapsed && (
            <div className="divider divider-vertical" onMouseDown={handleRightDividerMouseDown} />
          )}

          <div className="panel panel-ai" style={getAiStyle()}>
            <Suspense fallback={<PanelFallback />}>
              <AiPanel
                modelConfigs={modelConfigs}
                systemPrompt={systemPrompt}
                isFullscreen={fullscreen === 'ai'}
                onToggleFullscreen={() => toggleFullscreen('ai')}
              />
            </Suspense>
          </div>
        </div>
      </div>
    </WorkspaceContext.Provider>
  )
}

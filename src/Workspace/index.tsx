import React, { Suspense, lazy } from 'react'
import {
  ArrowLeftOutlined,
  DashboardOutlined,
  HomeOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Button, Tooltip, Spin } from 'antd'
import type { LexicalEditor } from 'lexical'
import AppHeader, { type HeaderPanelToggle } from '../components/AppHeader'
import type { Chapter, AiModelConfig, EntityId } from '../types'
import { getWritingOutlineWithChapters } from './utils'
import WorkspaceContext from './WorkspaceContext'
import type { WorkspaceContextValue } from './WorkspaceContext'
import WorkspaceSearchPanel from './WorkspaceSearchPanel'
import { DiffProvider } from './diff/DiffContext'
import { SettingDiffProvider } from './settingDiff/SettingDiffContext'
import CommandPalette, { type CommandItem } from './CommandPalette'
import FloatingPanel from './FloatingPanel'
import DockedPanel from './DockedPanel'
import { usePanelLayout } from './hooks/usePanelLayout'
import { useActiveChapter } from './hooks/useActiveChapter'
import { useWorkspaceSearch } from './hooks/useWorkspaceSearch'
import { useWorkspaceShortcuts } from './hooks/useWorkspaceShortcuts'
import { buildPaletteCommands } from './workspaceCommands'
import './FloatingPanel.scss'
import './workspaceSearch.scss'

const DirectorNotebook = lazy(() => import('./DirectorNotebook'))
const EditorPanel = lazy(() => import('./EditorPanel'))
const AiPanel = lazy(() => import('./AiPanel'))
const SettingPanel = lazy(() => import('./SettingPanel'))
const DashboardPanel = lazy(() => import('./DashboardPanel'))

const PanelFallback = () => (
  <div className="workspace-panel-fallback"><Spin size="small" /></div>
)

/**
 * AI-Centric 工作区：AI 固定居中，章节悬停/停靠在左侧，正文固定在右侧。
 *
 * 主区域规则见 hooks/usePanelLayout；本组件只负责组合：
 * - 布局状态（usePanelLayout）+ 快捷键（useWorkspaceShortcuts）
 * - 活跃章节持久化（useActiveChapter）+ 写作目录加载
 * - 全局搜索（useWorkspaceSearch）
 * - WorkspaceContext 组装与各 panel / 浮窗 / 命令面板渲染
 */
interface WorkspaceProps {
  bookId?: EntityId | null
  bookTitle?: string
  enableVolume?: boolean
  onBack?: () => void
  onGoHome?: () => void
  onOpenSettings?: () => void
  modelConfigs?: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>) => void
  syncOutlineChapter?: boolean
}

export default function Workspace({ bookId, bookTitle, enableVolume = false, onBack, onGoHome, onOpenSettings, modelConfigs = [], onUpdateModelConfig, syncOutlineChapter = false }: WorkspaceProps = {}) {
  const {
    panelState,
    updateFloating,
    toggleFloating,
    closeFloating,
  } = usePanelLayout()

  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false)
  const toggleCommandPalette = React.useCallback(
    () => setCommandPaletteOpen((open) => !open),
    [],
  )
  useWorkspaceShortcuts({ toggleCommandPalette })

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ panel?: string; open?: boolean }>).detail
      if (detail?.open === false) return
      if (detail?.panel === 'setting') {
        updateFloating('setting', { open: true })
      } else if (detail?.panel === 'dashboard') {
        updateFloating('dashboard', { open: true })
      }
    }
    window.addEventListener('workspace-open-panel', handler as EventListener)
    return () => window.removeEventListener('workspace-open-panel', handler as EventListener)
  }, [updateFloating])

  const [writingOutlineId, setWritingOutlineId] = React.useState<EntityId | null>(null)
  const [writingChapters, setWritingChapters] = React.useState<Chapter[]>([])

  const {
    activeChapterId: activeWritingChapterId,
    activeChapterTitle: activeWritingChapterTitle,
    setActiveChapter: handleWritingSelect,
  } = useActiveChapter(bookId)

  /** 本书总字数（万），与后端规则一致；null 表示尚未拉取 */
  const [bookWordWanDisplay, setBookWordWanDisplay] = React.useState<string | null>(null)

  const containerRef = React.useRef<HTMLDivElement>(null)

  const lexicalEditorRef = React.useRef<LexicalEditor | null>(null)
  const setLexicalEditorRef = React.useCallback((e: LexicalEditor | null) => {
    lexicalEditorRef.current = e
  }, [])

  const {
    workspaceSearchQuery,
    setWorkspaceSearchQuery,
    workspaceSearchActiveIndex,
    setWorkspaceSearchActiveIndex,
    workspaceSearchMatchTotal,
    searchContentVersion,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged,
  } = useWorkspaceSearch({ lexicalEditorRef, activeChapterId: activeWritingChapterId })

  const loadWritingChapters = React.useCallback(async () => {
    const data = await getWritingOutlineWithChapters(bookId)
    if (!data) return
    setWritingOutlineId(data.outlineId)
    setWritingChapters(data.chapters)
  }, [bookId])

  React.useEffect(() => {
    loadWritingChapters()
  }, [loadWritingChapters])

  React.useEffect(() => {
    if (bookId == null || String(bookId).trim() === '') {
      setBookWordWanDisplay(null)
      return
    }
    const t = window.setTimeout(() => {
      void window.electronAPI.getBookWordCount({ bookId }).then((res) => {
        if (res.success && res.data != null && typeof res.data.count === 'number') {
          setBookWordWanDisplay((res.data.count / 10000).toFixed(2))
        } else {
          setBookWordWanDisplay(null)
        }
      })
    }, 400)
    return () => clearTimeout(t)
  }, [bookId, searchContentVersion])

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ chapterId: EntityId; title: string }>).detail
      const nextId = detail?.chapterId ?? null
      const nextTitle = detail?.title ?? ''
      if (nextId == null || String(nextId).trim() === '') return
      handleWritingSelect(nextId, nextTitle)
      loadWritingChapters()
    }
    window.addEventListener('chapter-created', handler)
    return () => window.removeEventListener('chapter-created', handler)
  }, [loadWritingChapters, handleWritingSelect])

  /**
   * 删除写作章节后清理对应的 chapter / volume 大纲记录。
   *
   * 注意：后端 delete_chapter 不会级联删除 outline；旧实现误用 getOutlineByWritingChapter
   * 导致每次删章节都会把整个写作大纲删掉。这里改为按章节维度精确清理。
   */
  const handleWritingChapterDeleted = React.useCallback(async (writingChapterId: EntityId) => {
    if (bookId == null) return
    const wcKey = String(writingChapterId)
    for (const fetcher of [
      window.electronAPI.getChapterOutlines,
      window.electronAPI.getVolumeOutlines,
    ] as const) {
      const res = await fetcher(bookId)
      if (!res.success) continue
      const list = res.data ?? []
      const matched = list.find(
        (o) => o.writing_chapter_id != null && String(o.writing_chapter_id) === wcKey,
      )
      if (matched) {
        await window.electronAPI.deleteOutline({ outlineId: matched.id })
        break
      }
    }
  }, [bookId])

  const handleWritingChaptersChange = React.useCallback((outlineId: EntityId, chapterList: Chapter[]) => {
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
    setWorkspaceSearchQuery,
    workspaceSearchActiveIndex,
    setWorkspaceSearchActiveIndex,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged,
    setLexicalEditorRef,
    workspaceSearchMatchTotal,
  ])

  /**
   * 顶栏仅保留浮层入口。章节栏通过拖拽折叠与轨道 hover 控制。
   */
  const headerPanelToggles = React.useMemo<HeaderPanelToggle[]>(() => [
    {
      key: 'setting',
      icon: <TeamOutlined style={{ fontSize: 16 }} />,
      tooltip: panelState.setting.open ? '关闭设定面板' : '人物 / 故事背景 / 世界设定',
      active: panelState.setting.open,
      onClick: () => toggleFloating('setting'),
    },
    {
      key: 'dashboard',
      icon: <DashboardOutlined style={{ fontSize: 16 }} />,
      tooltip: panelState.dashboard.open ? '关闭仪表盘' : '仪表盘：故事健康 / 写作统计',
      active: panelState.dashboard.open,
      onClick: () => toggleFloating('dashboard'),
    },
  ], [panelState, toggleFloating])

  const paletteCommands = React.useMemo<CommandItem[]>(
    () =>
      buildPaletteCommands({
        panelState,
        toggleFloating,
        writingChapters,
        activeWritingChapterId,
        onChapterSelect: handleWritingSelect,
        onOpenSettings,
      }),
    [panelState, toggleFloating, writingChapters, activeWritingChapterId, handleWritingSelect, onOpenSettings],
  )

  return (
    <WorkspaceContext.Provider value={workspaceContextValue}>
    <DiffProvider>
    <SettingDiffProvider>
      <AppHeader
        title={
          bookId ? (
            <span className="app-title-with-meta">
              <span className="app-title">{bookTitle || '未命名'}</span>
              {bookWordWanDisplay != null ? (
                <span className="app-title-word-count">{bookWordWanDisplay} 万字</span>
              ) : null}
            </span>
          ) : (
            (bookTitle || 'PurrTypos')
          )
        }
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
        panelToggles={headerPanelToggles}
        onOpenSettings={onOpenSettings}
      />

      <WorkspaceSearchPanel />

      <div
        className="app-body app-body--ai-centric"
        id="workspace-search-scope"
        ref={containerRef}
      >
        {panelState.left.open && (
          <DockedPanel
            side="left"
            width={panelState.left.width}
            minWidth={220}
            maxWidth={420}
            onWidthChange={(width) => updateFloating('left', { width })}
            onCollapse={(width) => updateFloating('left', { width, open: false })}
            ariaLabel="章节列表边栏"
          >
            <div className="panel panel-left workspace-search-include">
              <Suspense fallback={<PanelFallback />}>
                <DirectorNotebook
                  bookTitle={bookTitle ?? ''}
                  onWritingChapterDeleted={handleWritingChapterDeleted}
                />
              </Suspense>
            </div>
          </DockedPanel>
        )}

        {!panelState.left.open && (
          <div
            className="workspace-chapter-rail"
            style={{ '--chapter-hover-width': `${panelState.left.width}px` } as React.CSSProperties}
            tabIndex={0}
            aria-label="悬停展开章节边栏"
          >
            <div className="workspace-chapter-hover-panel">
              <div className="panel panel-left workspace-search-include">
                <Suspense fallback={<PanelFallback />}>
                  <DirectorNotebook
                    bookTitle={bookTitle ?? ''}
                    onWritingChapterDeleted={handleWritingChapterDeleted}
                    dockCollapsed
                    onExpandDock={() => updateFloating('left', { open: true })}
                  />
                </Suspense>
              </div>
            </div>
          </div>
        )}

        {/* 中央区域永远是 AI；不再存在“切换主区域”的布局状态。 */}
        <div className="panel panel-ai panel-main panel-ai--fill workspace-primary-panel workspace-search-include">
          <Suspense fallback={<PanelFallback />}>
            <AiPanel
              modelConfigs={modelConfigs}
              onUpdateModelConfig={onUpdateModelConfig}
              conversationSidebarOpen={panelState.conversation.open}
              onConversationSidebarOpenChange={(open) => updateFloating('conversation', { open })}
            />
          </Suspense>
        </div>

        <DockedPanel
          side="right"
          width={panelState.editor.width}
          minWidth={340}
          maxWidth={640}
          onWidthChange={(width) => updateFloating('editor', { width })}
          ariaLabel="正文编辑边栏"
        >
          <div className="panel panel-editor workspace-search-include">
            <Suspense fallback={<PanelFallback />}>
              <EditorPanel
                bookTitle={bookTitle ?? ''}
                modelConfigs={modelConfigs}
                onUpdateModelConfig={onUpdateModelConfig}
                onLexicalEditor={setLexicalEditorRef}
              />
            </Suspense>
          </div>
        </DockedPanel>

        {panelState.setting.open && (
          <FloatingPanel
            side="right"
            title="设定"
            x={panelState.setting.x}
            y={panelState.setting.y}
            width={panelState.setting.width}
            onPositionChange={(p) => updateFloating('setting', p)}
            onClose={() => closeFloating('setting')}
          >
            <div className="panel panel-setting">
              <Suspense fallback={<PanelFallback />}>
                <SettingPanel bookId={bookId ?? null} />
              </Suspense>
            </div>
          </FloatingPanel>
        )}

        {panelState.dashboard.open && (
          <FloatingPanel
            side="right"
            title="仪表盘"
            x={panelState.dashboard.x}
            y={panelState.dashboard.y}
            width={panelState.dashboard.width}
            onPositionChange={(p) => updateFloating('dashboard', p)}
            onClose={() => closeFloating('dashboard')}
          >
            <div className="panel panel-dashboard">
              <Suspense fallback={<PanelFallback />}>
                <DashboardPanel bookId={bookId ?? null} />
              </Suspense>
            </div>
          </FloatingPanel>
        )}
      </div>
      <CommandPalette
        open={commandPaletteOpen}
        commands={paletteCommands}
        onClose={() => setCommandPaletteOpen(false)}
      />
    </SettingDiffProvider>
    </DiffProvider>
    </WorkspaceContext.Provider>
  )
}

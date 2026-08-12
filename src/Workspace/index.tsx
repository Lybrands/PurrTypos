import { services } from '@/services'
import React, { Suspense, lazy } from 'react'
import {
  ArrowLeftIcon,
  DashboardIcon,
  HomeIcon,
  StorySettingIcon,
} from '@/purr-components'
import { PurrButton, PurrSpin, PurrTooltip } from '@/purr-components'
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
import DockedPanel from './DockedPanel'
import WorkspaceUtilityPanel from './WorkspaceUtilityPanel'
import { usePanelLayout } from './hooks/usePanelLayout'
import { useActiveChapter } from './hooks/useActiveChapter'
import { useWorkspaceSearch } from './hooks/useWorkspaceSearch'
import { useWorkspaceShortcuts } from './hooks/useWorkspaceShortcuts'
import { buildPaletteCommands } from './workspaceCommands'
import NotebookToolbar from './DirectorNotebook/NotebookToolbar'
import type { OpenSettingPanelDetail } from './SettingPanel'
import {
  DASHBOARD_TAB,
  EDITOR_TAB_KEY,
  SETTING_TAB,
  type WorkspaceUtilityTab,
} from './utilityPanelTypes'
import './workspaceSearch.scss'

const DirectorNotebook = lazy(() => import('./DirectorNotebook'))
const EditorPanel = lazy(() => import('./EditorPanel'))
const AiPanel = lazy(() => import('./AiPanel'))

const PanelFallback = () => (
  <div className="workspace-panel-fallback"><PurrSpin size="small" /></div>
)

/**
 * AI-Centric 工作区：AI 固定居中，章节停靠在左侧，正文与辅助功能共享右侧标签面板。
 *
 * 主区域规则见 hooks/usePanelLayout；本组件只负责组合：
 * - 布局状态（usePanelLayout）+ 快捷键（useWorkspaceShortcuts）
 * - 活跃章节持久化（useActiveChapter）+ 写作目录加载
 * - 全局搜索（useWorkspaceSearch）
 * - WorkspaceContext 组装与各停靠面板 / 命令面板渲染
 */
interface WorkspaceProps {
  bookId?: EntityId | null
  bookTitle?: string
  enableVolume?: boolean
  onBack?: () => void
  onGoHome?: () => void
  onOpenSettings: () => void
  modelConfigs?: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>) => void
  syncOutlineChapter?: boolean
  onReady?: () => void
}

type WorkspaceFullscreenPanel = 'right' | null

export default function Workspace({ bookId, bookTitle, enableVolume = false, onBack, onGoHome, onOpenSettings, modelConfigs = [], onUpdateModelConfig, syncOutlineChapter = false, onReady }: WorkspaceProps) {
  const {
    panelState,
    updateFloating,
  } = usePanelLayout()

  const [fullscreenPanel, setFullscreenPanel] = React.useState<WorkspaceFullscreenPanel>(null)
  const [utilityTabs, setUtilityTabs] = React.useState<WorkspaceUtilityTab[]>([])
  const [activeRightTabKey, setActiveRightTabKey] = React.useState<string>(EDITOR_TAB_KEY)
  const activeUtilityTabKey =
    activeRightTabKey === EDITOR_TAB_KEY ? null : activeRightTabKey
  const [settingOpenRequest, setSettingOpenRequest] = React.useState<OpenSettingPanelDetail | null>(null)

  const openUtilityTab = React.useCallback((tab: WorkspaceUtilityTab) => {
    setUtilityTabs((currentTabs) => {
      const existingIndex = currentTabs.findIndex((item) => item.key === tab.key)
      if (existingIndex < 0) return [...currentTabs, tab]
      const nextTabs = [...currentTabs]
      nextTabs[existingIndex] = tab
      return nextTabs
    })
    setActiveRightTabKey(tab.key)
    updateFloating('right', { open: true })
  }, [updateFloating])

  const toggleUtilityTab = React.useCallback((tab: WorkspaceUtilityTab) => {
    if (panelState.right.open && activeRightTabKey === tab.key) {
      setActiveRightTabKey(EDITOR_TAB_KEY)
      return
    }
    openUtilityTab(tab)
  }, [activeRightTabKey, openUtilityTab, panelState.right.open])

  const closeUtilityTab = React.useCallback((key: string) => {
    setUtilityTabs((currentTabs) => {
      const closingIndex = currentTabs.findIndex((tab) => tab.key === key)
      if (closingIndex < 0) return currentTabs
      const nextTabs = currentTabs.filter((tab) => tab.key !== key)
      setActiveRightTabKey((currentKey) => {
        if (currentKey !== key) return currentKey
        return nextTabs[Math.min(closingIndex, nextTabs.length - 1)]?.key ?? EDITOR_TAB_KEY
      })
      return nextTabs
    })
  }, [])

  const collapseRightPanel = React.useCallback(() => {
    setFullscreenPanel(null)
    updateFloating('right', { open: false })
  }, [updateFloating])

  const toggleRightPanelFullscreen = React.useCallback(() => {
    setFullscreenPanel((currentPanel) => currentPanel === 'right' ? null : 'right')
  }, [])

  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false)
  const toggleCommandPalette = React.useCallback(
    () => setCommandPaletteOpen((open) => !open),
    [],
  )
  useWorkspaceShortcuts({ toggleCommandPalette })

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{
        panel?: string
        open?: boolean
        setting?: OpenSettingPanelDetail
      }>).detail
      if (detail?.open === false) return
      if (detail?.panel === 'setting') {
        if (detail.setting) setSettingOpenRequest(detail.setting)
        openUtilityTab(SETTING_TAB)
      } else if (detail?.panel === 'dashboard') {
        openUtilityTab(DASHBOARD_TAB)
      }
    }
    window.addEventListener('workspace-open-panel', handler as EventListener)
    return () => window.removeEventListener('workspace-open-panel', handler as EventListener)
  }, [openUtilityTab])

  React.useEffect(() => {
    setUtilityTabs([])
    setActiveRightTabKey(EDITOR_TAB_KEY)
    setFullscreenPanel(null)
  }, [bookId])

  React.useEffect(() => {
    if (fullscreenPanel == null) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFullscreenPanel(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [fullscreenPanel])

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
      void services.books.getBookWordCount({ bookId }).then((res) => {
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
      services.outlines.getChapterOutlines,
      services.outlines.getVolumeOutlines,
    ] as const) {
      const res = await fetcher(bookId)
      if (!res.success) continue
      const list = res.data ?? []
      const matched = list.find(
        (o) => o.writing_chapter_id != null && String(o.writing_chapter_id) === wcKey,
      )
      if (matched) {
        await services.outlines.deleteOutline({ outlineId: matched.id })
        break
      }
    }
    closeUtilityTab(`outline:chapter:${wcKey}`)
    closeUtilityTab(`outline:volume:${wcKey}`)
  }, [bookId, closeUtilityTab])

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
    utilityPanelOpen: panelState.right.open,
    activeUtilityTabKey,
    openUtilityTab,
    toggleUtilityTab,
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
    panelState.right.open, activeUtilityTabKey,
    openUtilityTab, toggleUtilityTab,
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
   * 顶栏工具统一在右侧「正文 / 功能」组合面板中打开对应标签。
   */
  const headerPanelToggles = React.useMemo<HeaderPanelToggle[]>(() => [
    {
      key: 'setting',
      icon: <StorySettingIcon style={{ fontSize: 16 }} />,
      tooltip: panelState.right.open && activeUtilityTabKey === SETTING_TAB.key
        ? '返回正文'
        : '小说背景',
      active: panelState.right.open && activeUtilityTabKey === SETTING_TAB.key,
      onClick: () => toggleUtilityTab(SETTING_TAB),
    },
    {
      key: 'dashboard',
      icon: <DashboardIcon style={{ fontSize: 16 }} />,
      tooltip: panelState.right.open && activeUtilityTabKey === DASHBOARD_TAB.key
        ? '返回正文'
        : '写作仪表盘',
      active: panelState.right.open && activeUtilityTabKey === DASHBOARD_TAB.key,
      onClick: () => toggleUtilityTab(DASHBOARD_TAB),
    },
  ], [activeUtilityTabKey, panelState.right.open, toggleUtilityTab])

  const paletteCommands = React.useMemo<CommandItem[]>(
    () =>
      buildPaletteCommands({
        settingPanelActive: panelState.right.open && activeUtilityTabKey === SETTING_TAB.key,
        dashboardPanelActive: panelState.right.open && activeUtilityTabKey === DASHBOARD_TAB.key,
        onToggleSettingPanel: () => toggleUtilityTab(SETTING_TAB),
        onToggleDashboardPanel: () => toggleUtilityTab(DASHBOARD_TAB),
        writingChapters,
        activeWritingChapterId,
        onChapterSelect: handleWritingSelect,
        onOpenSettings,
      }),
    [
      panelState.right.open,
      activeUtilityTabKey,
      toggleUtilityTab,
      writingChapters,
      activeWritingChapterId,
      handleWritingSelect,
      onOpenSettings,
    ],
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
              <PurrTooltip title="返回首页">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<HomeIcon style={{ fontSize: 14 }} />}
                  onClick={onGoHome}
                />
              </PurrTooltip>
            )}
            {onBack && (
              <PurrTooltip title="返回书架">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<ArrowLeftIcon style={{ fontSize: 14 }} />}
                  onClick={onBack}
                />
              </PurrTooltip>
            )}
          </>
        }
        showActions
        right={<NotebookToolbar />}
        panelToggles={headerPanelToggles}
        onOpenSettings={onOpenSettings}
      />

      <WorkspaceSearchPanel />

      <div
        className={`app-body app-body--ai-centric${panelState.right.open || fullscreenPanel === 'right' ? '' : ' app-body--right-panel-collapsed'}`}
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
                  onCollapseDock={() => updateFloating('left', { open: false })}
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
              onOpenModelSettings={onOpenSettings}
              conversationSidebarOpen={panelState.conversation.open}
              onConversationSidebarOpenChange={(open) => updateFloating('conversation', { open })}
              onReady={onReady}
            />
          </Suspense>
        </div>

        {panelState.right.open || fullscreenPanel === 'right' ? (
          <DockedPanel
            side="right"
            width={panelState.right.width}
            minWidth={420}
            maxWidth={760}
            onWidthChange={(width) => updateFloating('right', { width })}
            onCollapse={(width) => updateFloating('right', { width, open: false })}
            ariaLabel="正文与工作台功能面板"
            className={`workspace-dock--right-panel${fullscreenPanel === 'right' ? ' workspace-dock--fullscreen' : ''}`}
          >
            <WorkspaceUtilityPanel
              bookId={bookId ?? null}
              tabs={utilityTabs}
              activeKey={activeRightTabKey}
              onActiveKeyChange={setActiveRightTabKey}
              onCloseTab={closeUtilityTab}
              editorContent={(
                <div className="panel panel-editor workspace-search-include">
                  <Suspense fallback={<PanelFallback />}>
                    <EditorPanel
                      bookTitle={bookTitle ?? ''}
                      modelConfigs={modelConfigs}
                      onUpdateModelConfig={onUpdateModelConfig}
                      onLexicalEditor={setLexicalEditorRef}
                      fullscreen={fullscreenPanel === 'right'}
                    />
                  </Suspense>
                </div>
              )}
              onCollapse={collapseRightPanel}
              fullscreen={fullscreenPanel === 'right'}
              onToggleFullscreen={toggleRightPanelFullscreen}
              settingOpenRequest={settingOpenRequest}
            />
          </DockedPanel>
        ) : (
          <div
            className="workspace-editor-rail"
            style={{ '--editor-hover-width': `${panelState.right.width}px` } as React.CSSProperties}
            tabIndex={0}
            aria-label="悬停展开正文与工作台功能面板"
          >
            <div className="workspace-editor-hover-panel">
              <WorkspaceUtilityPanel
                bookId={bookId ?? null}
                tabs={utilityTabs}
                activeKey={activeRightTabKey}
                onActiveKeyChange={setActiveRightTabKey}
                onCloseTab={closeUtilityTab}
                editorContent={(
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
                )}
                dockCollapsed
                onExpandDock={() => updateFloating('right', { open: true })}
                settingOpenRequest={settingOpenRequest}
              />
            </div>
          </div>
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

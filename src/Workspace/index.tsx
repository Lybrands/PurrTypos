import { services } from '@/services'
import React, { Suspense, lazy } from 'react'
import type { LexicalEditor } from 'lexical'
import {
  DashboardIcon,
  LibraryIcon,
  ManuscriptIcon,
  OutlineIcon,
  StoryMemoryIcon,
  StorySettingIcon,
} from '@/purr-components'
import { PurrSpin } from '@/purr-components'
import AppHeader, { type HeaderPanelToggle } from '../components/AppHeader'
import type { AiModelConfig, EntityId } from '../types'
import WorkspaceSearchPanel from './WorkspaceSearchPanel'
import { DiffProvider } from './diff/DiffContext'
import { SettingDiffProvider } from './settingDiff/SettingDiffContext'
import CommandPalette, { type CommandItem } from './CommandPalette'
import DockedPanel from './DockedPanel'
import WorkspaceUtilityPanel from './WorkspaceUtilityPanel'
import { getWritingOutlineWithChapters } from './utils'
import { registerWritingChaptersFetcher } from '../stores/workspaceStore'
import { usePanelLayout } from './hooks/usePanelLayout'
import { useWorkspaceShortcuts } from './hooks/useWorkspaceShortcuts'
import { buildPaletteCommands } from './workspaceCommands'
import NotebookToolbar from './DirectorNotebook/NotebookToolbar'
import {
  CANON_TAB,
  DASHBOARD_TAB,
  EDITOR_TAB_KEY,
  KNOWLEDGE_TAB,
  SETTING_TAB,
} from './utilityPanelTypes'
import {
  lexicalEditorRef,
  reloadWritingChapters,
  setActiveChapter as setWorkspaceActiveChapter,
  useActiveChapterId,
  useBookWordWanDisplay,
  useSearchContentVersion,
  useWorkspaceStore,
} from '../stores/workspaceStore'
import { restoreActiveChapter } from '../stores/activeChapterStore'
import {
  updatePanelLayout,
  usePanelLayoutStore,
} from '../stores/panelLayoutStore'
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
 * 共享状态全部在 src/stores/（按字段窄订阅，替代原 WorkspaceContext）；
 * 本组件只负责组合：布局（usePanelLayout→panelLayoutStore）+ 快捷键 + 挂载各面板。
 */
interface WorkspaceProps {
  bookId?: EntityId | null
  bookTitle?: string
  enableVolume?: boolean
  creationMode?: 'original' | 'continuation'
  onBack?: () => void
  onGoHome?: () => void
  onOpenSettings: () => void
  modelConfigs?: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled' | 'reasoningEffort'>>) => void
  syncOutlineChapter?: boolean
  onReady?: () => void
}

type WorkspaceFullscreenPanel = 'right' | null

/**
 * 字数统计观察者：只有这个 null 渲染组件订阅高频的 searchContentVersion，
 * 正文输入不再把整棵工作台树拖着重渲染。
 */
function BookWordCountWatcher({ bookId }: { bookId: EntityId | null }) {
  const contentVersion = useSearchContentVersion()
  const setDisplay = useWorkspaceStore((s) => s.setBookWordWanDisplay)
  React.useEffect(() => {
    if (bookId == null || String(bookId).trim() === '') {
      setDisplay(null)
      return
    }
    const t = window.setTimeout(() => {
      void services.books.getBookWordCount({ bookId }).then((res) => {
        setDisplay(
          res.success && res.data != null && typeof res.data.count === 'number'
            ? (res.data.count / 10000).toFixed(2)
            : null,
        )
      })
    }, 400)
    return () => clearTimeout(t)
  }, [bookId, contentVersion, setDisplay])
  return null
}

export default function Workspace({ bookId, bookTitle, enableVolume = false, creationMode = 'original', onBack, onGoHome, onOpenSettings, modelConfigs = [], onUpdateModelConfig, onReady }: WorkspaceProps) {
  const {
    panelState,
    updateFloating,
  } = usePanelLayout()

  const [fullscreenPanel, setFullscreenPanel] = React.useState<WorkspaceFullscreenPanel>(null)
  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false)

  const activeRightTabKey = useWorkspaceStore((s) => s.activeRightTabKey)
  const utilityTabs = useWorkspaceStore((s) => s.utilityTabs)
  const settingOpenRequest = useWorkspaceStore((s) => s.settingOpenRequest)
  const setActiveRightTabKeyAction = useWorkspaceStore((s) => s.setActiveRightTabKey)
  const writingChapters = useWorkspaceStore((s) => s.writingChapters)
  const activeWritingChapterId = useActiveChapterId()
  const bookWordWanDisplay = useBookWordWanDisplay()
  const toggleUtilityTab = useWorkspaceStore((s) => s.toggleUtilityTab)
  const closeUtilityTabAction = useWorkspaceStore((s) => s.closeUtilityTab)
  const setBookIdentity = useWorkspaceStore((s) => s.setBookIdentity)
  const resetUtilityPanel = useWorkspaceStore((s) => s.resetUtilityPanel)
  const activeUtilityTabKey =
    activeRightTabKey === EDITOR_TAB_KEY ? null : activeRightTabKey
  const editorTabActive = panelState.right.open && activeRightTabKey === EDITOR_TAB_KEY
  const resolvedBookId = bookId ?? null

  const toggleCommandPalette = React.useCallback(
    () => setCommandPaletteOpen((open) => !open),
    [],
  )

  /** 章节栏开合切换（快捷键 / 命令面板共用） */
  const toggleChapterSidebar = React.useCallback(() => {
    const open = usePanelLayoutStore.getState().left.open
    updatePanelLayout('left', { open: !open })
  }, [])

  const collapseRightPanel = React.useCallback(() => {
    setFullscreenPanel(null)
    updatePanelLayout('right', { open: false })
  }, [])

  /** 正文面板切换：面板开且当前是正文标签则收起，否则展开并切回正文 */
  const toggleEditorPanel = React.useCallback(() => {
    const layout = usePanelLayoutStore.getState()
    const activeKey = useWorkspaceStore.getState().activeRightTabKey
    if (layout.right.open && activeKey === EDITOR_TAB_KEY) {
      setFullscreenPanel(null)
      updatePanelLayout('right', { open: false })
      return
    }
    useWorkspaceStore.getState().setActiveRightTabKey(EDITOR_TAB_KEY)
    updatePanelLayout('right', { open: true })
  }, [])

  useWorkspaceShortcuts({
    toggleCommandPalette,
    onToggleChapterSidebar: toggleChapterSidebar,
    onToggleEditorPanel: toggleEditorPanel,
  })

  React.useEffect(() => {
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

  // ── 注册目录拉取器（store 与服务层解耦，services 链留在组件层）───
  React.useEffect(() => {
    registerWritingChaptersFetcher(getWritingOutlineWithChapters)
  }, [])

  // ── 书籍身份写入 store（挂载 / 切书）────────────────────────────
  React.useEffect(() => {
    setBookIdentity({
      bookId: resolvedBookId,
      bookTitle: bookTitle ?? '',
      enableVolume,
    })
  }, [resolvedBookId, bookTitle, enableVolume, setBookIdentity])

  // ── 切书：功能标签复位 + 恢复该书上次选中的章节 + 重拉目录 ──────
  React.useEffect(() => {
    resetUtilityPanel()
    const entry = restoreActiveChapter(resolvedBookId)
    setWorkspaceActiveChapter(entry?.id ?? null, entry?.title ?? '')
    void reloadWritingChapters(resolvedBookId)
  }, [resolvedBookId, resetUtilityPanel])

  /**
   * 删除写作章节后清理对应的 chapter / volume 大纲记录。
   * 后端不会级联删除大纲，因此按 writing_chapter_id 匹配并清理。
   */
  const handleWritingChapterDeleted = React.useCallback(async (writingChapterId: EntityId) => {
    if (resolvedBookId == null) return
    const wcKey = String(writingChapterId)
    for (const fetcher of [
      services.outlines.getChapterOutlines,
      services.outlines.getVolumeOutlines,
    ] as const) {
      const res = await fetcher(resolvedBookId)
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
    closeUtilityTabAction(`outline:chapter:${wcKey}`)
    closeUtilityTabAction(`outline:volume:${wcKey}`)
  }, [resolvedBookId, closeUtilityTabAction])

  const handleLexicalEditor = React.useCallback((editor: LexicalEditor | null) => {
    lexicalEditorRef.current = editor
  }, [])

  const toggleRightPanelFullscreen = React.useCallback(() => {
    setFullscreenPanel((currentPanel) => currentPanel === 'right' ? null : 'right')
  }, [])

  const editorContent = (
    <div className="panel panel-editor workspace-search-include">
      <Suspense fallback={<PanelFallback />}>
        <EditorPanel
          bookTitle={bookTitle ?? ''}
          modelConfigs={modelConfigs}
          onUpdateModelConfig={onUpdateModelConfig}
          onLexicalEditor={handleLexicalEditor}
          fullscreen={fullscreenPanel === 'right'}
        />
      </Suspense>
    </div>
  )

  /**
   * 顶栏工具统一在右侧「正文 / 功能」组合面板中打开对应标签。
   * 正文入口固定在最左：写作者随时可以一键回到正文（被功能标签挤走也不迷路）。
   */
  const headerPanelToggles = React.useMemo<HeaderPanelToggle[]>(() => [
    {
      key: 'editor',
      icon: <ManuscriptIcon style={{ fontSize: 16 }} />,
      tooltip: editorTabActive ? '收起正文面板' : '正文',
      active: editorTabActive,
      onClick: toggleEditorPanel,
    },
    {
      key: 'chapters',
      icon: <OutlineIcon style={{ fontSize: 16 }} />,
      tooltip: panelState.left.open ? '收起章节列表' : '章节列表',
      active: panelState.left.open,
      onClick: toggleChapterSidebar,
    },
    ...(creationMode === 'continuation' ? [{
      key: 'canon',
      icon: <StoryMemoryIcon style={{ fontSize: 16 }} />,
      tooltip: panelState.right.open && activeUtilityTabKey === CANON_TAB.key
        ? '返回正文'
        : '继承正史',
      active: panelState.right.open && activeUtilityTabKey === CANON_TAB.key,
      onClick: () => toggleUtilityTab(CANON_TAB),
    }] : []),
    { key: 'knowledge', icon: <LibraryIcon style={{ fontSize: 16 }} />, tooltip: '创作资料库', active: activeUtilityTabKey === KNOWLEDGE_TAB.key, onClick: () => toggleUtilityTab(KNOWLEDGE_TAB) },
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
  ], [creationMode, editorTabActive, panelState.left.open, panelState.right.open, activeUtilityTabKey, toggleChapterSidebar, toggleEditorPanel, toggleUtilityTab])

  const paletteCommands = React.useMemo<CommandItem[]>(
    () =>
      buildPaletteCommands({
        chapterSidebarOpen: panelState.left.open,
        editorPanelActive: panelState.right.open && activeRightTabKey === EDITOR_TAB_KEY,
        onToggleChapterSidebar: toggleChapterSidebar,
        onToggleEditorPanel: toggleEditorPanel,
        settingPanelActive: panelState.right.open && activeUtilityTabKey === SETTING_TAB.key,
        dashboardPanelActive: panelState.right.open && activeUtilityTabKey === DASHBOARD_TAB.key,
        onToggleSettingPanel: () => toggleUtilityTab(SETTING_TAB),
        onToggleDashboardPanel: () => toggleUtilityTab(DASHBOARD_TAB),
        writingChapters,
        activeWritingChapterId,
        onChapterSelect: (id, title) => setWorkspaceActiveChapter(id, title),
        onOpenSettings,
      }),
    [
      panelState.left.open,
      panelState.right.open,
      activeRightTabKey,
      activeUtilityTabKey,
      toggleChapterSidebar,
      toggleEditorPanel,
      toggleUtilityTab,
      writingChapters,
      activeWritingChapterId,
      onOpenSettings,
    ],
  )

  return (
    <DiffProvider>
    <SettingDiffProvider key={String(bookId ?? 'no-book')} bookId={bookId}>
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
        navigation={{
          home: onGoHome ? { label: '返回首页', onClick: onGoHome } : undefined,
          back: onBack ? { label: '返回书架', onClick: onBack } : undefined,
        }}
        showActions
        right={<NotebookToolbar />}
        panelToggles={headerPanelToggles}
        onOpenSettings={onOpenSettings}
      />

      <WorkspaceSearchPanel />

      <div
        className={`app-body app-body--ai-centric${panelState.right.open || fullscreenPanel === 'right' ? '' : ' app-body--right-panel-collapsed'}`}
        id="workspace-search-scope"
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
            aria-label="章节列表面板（悬停展开）"
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
              bookId={resolvedBookId}
              tabs={utilityTabs}
              activeKey={activeRightTabKey}
              onActiveKeyChange={setActiveRightTabKeyAction}
              onCloseTab={closeUtilityTabAction}
              editorContent={editorContent}
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
            aria-label="正文与工作台功能面板（悬停展开）"
          >
            <div className="workspace-editor-hover-panel">
              <WorkspaceUtilityPanel
                bookId={resolvedBookId}
                tabs={utilityTabs}
                activeKey={activeRightTabKey}
                onActiveKeyChange={setActiveRightTabKeyAction}
                onCloseTab={closeUtilityTabAction}
                editorContent={editorContent}
                dockCollapsed
                onExpandDock={() => updateFloating('right', { open: true })}
                settingOpenRequest={settingOpenRequest}
              />
            </div>
          </div>
        )}

        <BookWordCountWatcher bookId={resolvedBookId} />
      </div>
      <CommandPalette
        open={commandPaletteOpen}
        commands={paletteCommands}
        onClose={() => setCommandPaletteOpen(false)}
      />
    </SettingDiffProvider>
    </DiffProvider>
  )
}

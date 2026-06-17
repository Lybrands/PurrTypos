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
import ChapterListIcon from '../icons/ChapterListIcon'
import WritingPenIcon from '../icons/WritingPenIcon'
import AiChatIcon from '../icons/AiChatIcon'
import type { Chapter, AiModelConfig, EntityId } from '../types'
import { getWritingOutlineWithChapters } from './utils'
import WorkspaceContext from './WorkspaceContext'
import type { WorkspaceContextValue } from './WorkspaceContext'
import WorkspaceSearchPanel from './WorkspaceSearchPanel'
import { DiffProvider } from './diff/DiffContext'
import { SettingDiffProvider } from './settingDiff/SettingDiffContext'
import CommandPalette, { type CommandItem } from './CommandPalette'
import FloatingPanel from './FloatingPanel'
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
 * AI-Centric 工作区：默认 AI 占满中央，「设定」「写作」以悬浮 panel 形式弹出。
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
  syncOutlineChapter?: boolean
}

export default function Workspace({ bookId, bookTitle, enableVolume = false, onBack, onGoHome, onOpenSettings, modelConfigs = [], syncOutlineChapter = false }: WorkspaceProps = {}) {
  const {
    panelState,
    mainPanel,
    updateFloating,
    toggleFloating,
    closeFloating,
    setMain,
  } = usePanelLayout()

  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false)
  const toggleCommandPalette = React.useCallback(
    () => setCommandPaletteOpen((open) => !open),
    [],
  )
  useWorkspaceShortcuts({ toggleFloating, setMain, toggleCommandPalette })

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ panel?: string; open?: boolean }>).detail
      if (detail?.open === false) return
      if (detail?.panel === 'setting') {
        updateFloating('setting', { open: true })
      } else if (detail?.panel === 'dashboard') {
        updateFloating('dashboard', { open: true })
      } else if (detail?.panel === 'ai' && mainPanel !== 'ai') {
        // AI 是主区域时本就可见；否则展开 AI 浮窗
        updateFloating('ai', { open: true })
      }
    }
    window.addEventListener('workspace-open-panel', handler as EventListener)
    return () => window.removeEventListener('workspace-open-panel', handler as EventListener)
  }, [updateFloating, mainPanel])

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
   * 顶栏面板 toggle：与左右贴线（floating-rail）等价的显式入口，解决贴线
   * 「几乎不可见」导致新用户找不到章节列表 / 编辑器的可发现性问题。
   * 已是主区域的面板按钮高亮且不可点（toggleFloating 对主区域本就是 noop）。
   */
  const headerPanelToggles = React.useMemo<HeaderPanelToggle[]>(() => [
    {
      key: 'left',
      icon: <ChapterListIcon size={16} />,
      tooltip: panelState.left.open ? '关闭章节列表（Ctrl+Shift+1）' : '章节列表（Ctrl+Shift+1）',
      active: panelState.left.open,
      onClick: () => toggleFloating('left'),
    },
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
    {
      key: 'editor',
      icon: <WritingPenIcon size={16} />,
      tooltip: mainPanel === 'editor'
        ? '写作已是主区域'
        : panelState.editor.open
          ? '关闭写作浮窗（Ctrl+Shift+3）'
          : '写作 / 正文编辑器（Ctrl+Shift+3）',
      active: mainPanel === 'editor' || panelState.editor.open,
      disabled: mainPanel === 'editor',
      onClick: () => toggleFloating('editor'),
    },
    {
      key: 'ai',
      icon: <AiChatIcon size={16} />,
      tooltip: mainPanel === 'ai'
        ? 'AI 已是主区域'
        : panelState.ai.open
          ? '收起 AI 浮窗（Ctrl+Shift+2 切回主区域）'
          : 'AI 对话浮窗（Ctrl+Shift+2 切回主区域）',
      active: mainPanel === 'ai' || panelState.ai.open,
      disabled: mainPanel === 'ai',
      onClick: () => toggleFloating('ai'),
    },
  ], [panelState, mainPanel, toggleFloating])

  const paletteCommands = React.useMemo<CommandItem[]>(
    () =>
      buildPaletteCommands({
        panelState,
        mainPanel,
        toggleFloating,
        setMain,
        writingChapters,
        activeWritingChapterId,
        onChapterSelect: handleWritingSelect,
        onOpenSettings,
      }),
    [panelState, mainPanel, toggleFloating, setMain, writingChapters, activeWritingChapterId, handleWritingSelect, onOpenSettings],
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
        {/* 主区域：mainPanel 只能是 'ai' 或 'editor'（章节列表只能浮窗） */}
        <div className={`panel panel-${mainPanel} panel-main panel-ai--fill workspace-search-include`}>
          <Suspense fallback={<PanelFallback />}>
            {mainPanel === 'ai' && (
              <AiPanel
                modelConfigs={modelConfigs}
                isMain={true}
                onSetMain={() => { /* AI 已是主区域，noop */ }}
                compact={false}
              />
            )}
            {mainPanel === 'editor' && (
              <EditorPanel
                bookTitle={bookTitle ?? ''}
                modelConfigs={modelConfigs}
                isMain={true}
                onSetMain={() => { /* 已是主区域，noop */ }}
                onLexicalEditor={setLexicalEditorRef}
              />
            )}
          </Suspense>
        </div>

        {/* 左侧边线：唤起章节列表浮窗（章节列表永远不能成为主，所以从不 disabled） */}
        <Tooltip
          title={panelState.left.open ? '关闭章节列表（Ctrl+Shift+1）' : '章节列表（Ctrl+Shift+1）'}
          placement="right"
          mouseEnterDelay={0.4}
        >
          <button
            type="button"
            className={`floating-rail floating-rail--left ${panelState.left.open ? 'is-active' : ''}`}
            onClick={() => toggleFloating('left')}
            aria-label="章节列表"
          />
        </Tooltip>

        {/* 右侧边线：当 AI 是主区域时唤起「写作」浮窗；当 Editor 是主区域时唤起「AI」浮窗 */}
        {mainPanel === 'ai' ? (
          <Tooltip
            title={panelState.editor.open ? '关闭写作浮窗（Ctrl+Shift+3）' : '写作 / 正文编辑器（Ctrl+Shift+3）'}
            placement="left"
            mouseEnterDelay={0.4}
          >
            <button
              type="button"
              className={`floating-rail floating-rail--right ${panelState.editor.open ? 'is-active' : ''}`}
              onClick={() => toggleFloating('editor')}
              aria-label="写作 / 正文编辑器"
            />
          </Tooltip>
        ) : (
          <Tooltip
            title={panelState.ai.open ? '收起 AI 浮窗（Ctrl+Shift+2 切回主区域）' : '展开 AI 浮窗（Ctrl+Shift+2 切回主区域）'}
            placement="left"
            mouseEnterDelay={0.4}
          >
            <button
              type="button"
              className={`floating-rail floating-rail--right ${panelState.ai.open ? 'is-active' : ''}`}
              onClick={() => toggleFloating('ai')}
              aria-label="AI 对话"
            />
          </Tooltip>
        )}

        {/* AI 浮窗：仅当 AI 不是主区域时显示；close 按钮真正关闭浮窗（用户可通过右侧边线 / Ctrl+Shift+2 重新唤起 / 切回主） */}
        {mainPanel !== 'ai' && panelState.ai.open && (
          <FloatingPanel
            side="right"
            title="AI 对话"
            x={panelState.ai.x}
            y={panelState.ai.y}
            width={panelState.ai.width}
            onPositionChange={(p) => updateFloating('ai', p)}
            onClose={() => closeFloating('ai')}
          >
            <div className="panel panel-ai">
              <Suspense fallback={<PanelFallback />}>
                <AiPanel
                  modelConfigs={modelConfigs}
                  isMain={false}
                  onSetMain={() => setMain('ai')}
                  compact={true}
                />
              </Suspense>
            </div>
          </FloatingPanel>
        )}

        {/* 左浮窗：章节列表（永远是浮窗，不传 onSetMain → 隐藏「扩大为主」按钮） */}
        {panelState.left.open && (
          <FloatingPanel
            side="left"
            title="章节列表"
            x={panelState.left.x}
            y={panelState.left.y}
            width={panelState.left.width}
            onPositionChange={(p) => updateFloating('left', p)}
            onClose={() => closeFloating('left')}
          >
            <div className="panel panel-left workspace-search-include">
              <Suspense fallback={<PanelFallback />}>
                <DirectorNotebook
                  bookTitle={bookTitle ?? ''}
                  onWritingChapterDeleted={handleWritingChapterDeleted}
                />
              </Suspense>
            </div>
          </FloatingPanel>
        )}

        {/* 右浮窗：写作 / 编辑器（mainPanel='editor' 时不渲染浮窗） */}
        {mainPanel !== 'editor' && panelState.editor.open && (
          <FloatingPanel
            side="right"
            title="写作 / 正文"
            x={panelState.editor.x}
            y={panelState.editor.y}
            width={panelState.editor.width}
            onPositionChange={(p) => updateFloating('editor', p)}
            onClose={() => closeFloating('editor')}
          >
            <div className="panel panel-editor workspace-search-include">
              <Suspense fallback={<PanelFallback />}>
                <EditorPanel
                  bookTitle={bookTitle ?? ''}
                  modelConfigs={modelConfigs}
                  isMain={false}
                  onSetMain={() => setMain('editor')}
                  onLexicalEditor={setLexicalEditorRef}
                />
              </Suspense>
            </div>
          </FloatingPanel>
        )}

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

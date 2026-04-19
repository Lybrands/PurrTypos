import React, { Suspense, lazy } from 'react'
import {
  ArrowLeftOutlined,
  HomeOutlined,
  ReadOutlined,
  EditOutlined,
  CommentOutlined,
  AlignLeftOutlined,
  CopyOutlined,
  BorderlessTableOutlined,
  HistoryOutlined,
  SettingOutlined,
  BookOutlined,
} from '@ant-design/icons'
import { Button, Tooltip, Spin } from 'antd'
import type { LexicalEditor } from 'lexical'
import AppHeader from '../components/AppHeader'
import type { Chapter, AiAgentMode, AiModelConfig, EntityId } from '../types'
import { editorStateToText } from './EditorPanel/LexicalEditor'
import { findAllMatchStarts, selectLexicalSearchMatch } from './search/lexicalSearch'
import { getWritingOutlineWithChapters } from './utils'
import WorkspaceContext from './WorkspaceContext'
import type { WorkspaceContextValue } from './WorkspaceContext'
import WorkspaceSearchPanel from './WorkspaceSearchPanel'
import { DiffProvider } from './diff/DiffContext'
import CommandPalette, { type CommandItem } from './CommandPalette'
import FloatingPanel from './FloatingPanel'
import './FloatingPanel.scss'
import './workspaceSearch.scss'

const DirectorNotebook = lazy(() => import('./DirectorNotebook'))
const EditorPanel = lazy(() => import('./EditorPanel'))
const AiPanel = lazy(() => import('./AiPanel'))

const PanelFallback = () => (
  <div className="workspace-panel-fallback"><Spin size="small" /></div>
)

/**
 * AI-Centric 工作区：默认 AI 占满中央，「设定」「写作」以悬浮 panel 形式弹出。
 *
 * 主区域规则：
 * - 只有 AI 与 写作（editor）能成为主区域，章节列表 (left) 永远是浮窗
 * - 切换主区域时，原主区域自动降为钉住浮窗，便于用户一键切回
 * - AI 浮窗的关闭按钮就是真正关闭（用户可以通过左/右侧边线把 AI 重新唤起：
 *   实际上 AI 没有专属侧边线，所以"关闭 AI"等价于「让 AI 退场，专心写作 / 看设定」，
 *   再次需要 AI 时可通过 Ctrl+Shift+2 / 命令面板 / 写作面板内的入口召回为主）
 *
 * 状态存于 localStorage（按 mainPanel + 三个 floatingState 持久化）。
 */
type PanelKey = 'ai' | 'left' | 'editor'
/** 能成为主区域的 panel 子集（章节列表只能浮窗） */
type MainPanelKey = 'ai' | 'editor'

interface FloatingState {
  open: boolean
  x: number
  y: number
  width: number
}

const WORKSPACE_PANEL_STORAGE_KEY = 'purrtypos_workspace_floating_state_v2'

interface PersistedPanelState {
  mainPanel: MainPanelKey
  ai: FloatingState
  left: FloatingState
  editor: FloatingState
}

/** 给 right 浮窗一个估算的 x（窗口 - 自身宽度 - 12 边距），运行时再据容器纠正 */
function defaultRightX(width: number): number {
  if (typeof window === 'undefined') return 800
  return Math.max(0, window.innerWidth - width - 12)
}

const DEFAULT_STATE: PersistedPanelState = {
  mainPanel: 'ai',
  ai: { open: false, x: defaultRightX(560), y: 8, width: 560 },
  left: { open: false, x: 16, y: 8, width: 340 },
  editor: { open: false, x: defaultRightX(620), y: 8, width: 620 },
}

function loadPanelState(): PersistedPanelState {
  try {
    const raw = localStorage.getItem(WORKSPACE_PANEL_STORAGE_KEY)
    if (raw) {
      const p = JSON.parse(raw) as Partial<PersistedPanelState>
      const mergeFloating = (key: 'ai' | 'left' | 'editor', fallback: FloatingState): FloatingState => {
        const v = p[key]
        if (!v || typeof v !== 'object') return fallback
        return {
          open: !!v.open,
          x: typeof v.x === 'number' ? v.x : fallback.x,
          y: typeof v.y === 'number' ? v.y : fallback.y,
          width: typeof v.width === 'number' ? v.width : fallback.width,
        }
      }
      const main: MainPanelKey =
        p.mainPanel === 'ai' || p.mainPanel === 'editor' ? p.mainPanel : 'ai'
      return {
        mainPanel: main,
        ai: mergeFloating('ai', DEFAULT_STATE.ai),
        left: mergeFloating('left', DEFAULT_STATE.left),
        editor: mergeFloating('editor', DEFAULT_STATE.editor),
      }
    }
  } catch {
    // ignore
  }
  return DEFAULT_STATE
}

function savePanelState(state: PersistedPanelState) {
  try {
    localStorage.setItem(WORKSPACE_PANEL_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // ignore
  }
}

// ─── 活跃章节按 bookId 持久化 ─────────────────────────────────
const ACTIVE_CHAPTER_STORAGE_KEY = 'purrtypos_active_chapter_by_book'

interface ActiveChapterEntry {
  id: EntityId
  title: string
}

function loadActiveChapterMap(): Record<string, ActiveChapterEntry> {
  try {
    const raw = localStorage.getItem(ACTIVE_CHAPTER_STORAGE_KEY)
    if (!raw) return {}
    const obj = JSON.parse(raw)
    return obj && typeof obj === 'object' ? obj : {}
  } catch {
    return {}
  }
}

function saveActiveChapterMap(map: Record<string, ActiveChapterEntry>) {
  try {
    localStorage.setItem(ACTIVE_CHAPTER_STORAGE_KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
}

interface WorkspaceProps {
  bookId?: EntityId | null
  bookTitle?: string
  enableVolume?: boolean
  onBack?: () => void
  onGoHome?: () => void
  onOpenSettings?: () => void
  modelConfigs?: AiModelConfig[]
  syncOutlineChapter?: boolean
  aiAgentMode?: AiAgentMode
}

export default function Workspace({ bookId, bookTitle, enableVolume = false, onBack, onGoHome, onOpenSettings, modelConfigs = [], syncOutlineChapter = false, aiAgentMode = 'legacy' }: WorkspaceProps = {}) {
  const initialState = React.useMemo(loadPanelState, [])
  const [panelState, setPanelState] = React.useState<PersistedPanelState>(initialState)

  React.useEffect(() => {
    savePanelState(panelState)
  }, [panelState])

  const mainPanel = panelState.mainPanel

  const updateFloating = React.useCallback(
    (key: PanelKey, patch: Partial<FloatingState>) => {
      setPanelState((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }))
    },
    [],
  )

  const toggleFloating = React.useCallback((key: PanelKey) => {
    setPanelState((prev) => {
      // 如果该 panel 已是主区域，toggle 浮窗无意义
      if (prev.mainPanel === key) return prev
      return { ...prev, [key]: { ...prev[key], open: !prev[key].open } }
    })
  }, [])

  const closeFloating = React.useCallback((key: PanelKey) => {
    setPanelState((prev) => ({ ...prev, [key]: { ...prev[key], open: false } }))
  }, [])

  /**
   * 切换主区域（仅限 AI / Editor 两者互换）：
   * - 如果 next === current，noop
   * - 原主区域自动转为浮窗（open=true）便于一键切回
   * - 新主区域的浮窗自动关闭（它现在是主，无须浮窗）
   *
   * 注：浮窗永远是「钉住」语义（不会被点击外部自动收起），
   *     所以这里不再需要单独写 pinned 字段。
   */
  const setMain = React.useCallback((next: MainPanelKey) => {
    setPanelState((prev) => {
      if (prev.mainPanel === next) return prev
      const prevMain = prev.mainPanel
      return {
        ...prev,
        mainPanel: next,
        [prevMain]: { ...prev[prevMain], open: true },
        [next]: { ...prev[next], open: false },
      }
    })
  }, [])

  const [commandPaletteOpen, setCommandPaletteOpen] = React.useState(false)

  const [writingOutlineId, setWritingOutlineId] = React.useState<EntityId | null>(null)
  const [writingChapters, setWritingChapters] = React.useState<Chapter[]>([])

  const [activeWritingChapterId, setActiveWritingChapterId] = React.useState<EntityId | null>(() => {
    if (bookId == null) return null
    const entry = loadActiveChapterMap()[String(bookId)]
    return entry?.id ?? null
  })
  const [activeWritingChapterTitle, setActiveWritingChapterTitle] = React.useState<string>(() => {
    if (bookId == null) return ''
    return loadActiveChapterMap()[String(bookId)]?.title ?? ''
  })

  /** bookId 变化时：恢复该书之前选中的章节（如 localStorage 里有） */
  React.useEffect(() => {
    if (bookId == null) return
    const entry = loadActiveChapterMap()[String(bookId)]
    if (entry?.id != null) {
      setActiveWritingChapterId(entry.id)
      setActiveWritingChapterTitle(entry.title ?? '')
    } else {
      setActiveWritingChapterId(null)
      setActiveWritingChapterTitle('')
    }
  }, [bookId])

  /** 持久化当前活跃章节 */
  React.useEffect(() => {
    if (bookId == null) return
    const map = loadActiveChapterMap()
    if (activeWritingChapterId == null) {
      delete map[String(bookId)]
    } else {
      map[String(bookId)] = { id: activeWritingChapterId, title: activeWritingChapterTitle }
    }
    saveActiveChapterMap(map)
  }, [bookId, activeWritingChapterId, activeWritingChapterTitle])

  /** 本书总字数（万），与后端规则一致；null 表示尚未拉取 */
  const [bookWordWanDisplay, setBookWordWanDisplay] = React.useState<string | null>(null)

  const containerRef = React.useRef<HTMLDivElement>(null)

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
      setActiveWritingChapterId(nextId)
      setActiveWritingChapterTitle(nextTitle)
      loadWritingChapters()
    }
    window.addEventListener('chapter-created', handler)
    return () => window.removeEventListener('chapter-created', handler)
  }, [loadWritingChapters])


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

  // ─── Context 方法（useCallback 保证引用稳定）──────────────────
  const handleWritingSelect = React.useCallback((id: EntityId, title: string) => {
    setActiveWritingChapterId(id)
    setActiveWritingChapterTitle(title || '')
  }, [])

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
    workspaceSearchActiveIndex,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged,
    workspaceSearchMatchTotal,
  ])

  /**
   * 全局快捷键体系：
   * - Ctrl/Cmd+K          命令面板
   * - Ctrl/Cmd+Shift+1    Toggle「设定」浮窗
   * - Ctrl/Cmd+Shift+2    关闭所有浮窗（聚焦 AI 主区）
   * - Ctrl/Cmd+Shift+3    Toggle「写作」浮窗
   * - Ctrl/Cmd+Shift+H    打开本章 diff 历史
   */
  React.useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isMod = e.ctrlKey || e.metaKey
      if (!isMod) return

      if (!e.shiftKey && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        setCommandPaletteOpen((open) => !open)
        return
      }

      if (!e.shiftKey) return

      const key = e.key
      const code = e.code

      if (code === 'Digit1') {
        e.preventDefault()
        toggleFloating('left')
        return
      }
      if (code === 'Digit2') {
        e.preventDefault()
        // 聚焦 AI 主区：直接 setMain('ai')，原非 ai 主区会自动转为浮窗
        setMain('ai')
        return
      }
      if (code === 'Digit3') {
        e.preventDefault()
        toggleFloating('editor')
        return
      }

      if (key === 'H' || key === 'h') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('editor-open-diff-history'))
        return
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggleFloating, setMain])

  /** 命令面板命令集合（按分类聚合） */
  const paletteCommands = React.useMemo<CommandItem[]>(() => {
    const base: CommandItem[] = [
      {
        id: 'panel:toggle-left',
        label: panelState.left.open ? '关闭章节列表' : '打开章节列表',
        hint: 'Ctrl+Shift+1',
        icon: <BookOutlined />,
        category: '浮窗',
        keywords: ['setting', 'notebook', 'left', '设定', 'chapter', '章节'],
        run: () => toggleFloating('left'),
      },
      {
        id: 'panel:focus-ai',
        label:
          mainPanel === 'ai'
            ? panelState.ai.open
              ? 'AI 已是主区域（点击聚焦）'
              : 'AI 已是主区域'
            : '切回 AI 主区域',
        hint: 'Ctrl+Shift+2',
        icon: <CommentOutlined />,
        category: '浮窗',
        keywords: ['ai', 'chat', 'director', '导演', 'focus'],
        run: () => setMain('ai'),
      },
      {
        id: 'panel:toggle-ai-floating',
        label:
          mainPanel === 'ai'
            ? 'AI 是主区域（无需浮窗）'
            : panelState.ai.open
              ? '收起 AI 浮窗'
              : '展开 AI 浮窗',
        icon: <CommentOutlined />,
        category: '浮窗',
        keywords: ['ai', 'float', '浮窗', '收起', '展开'],
        run: () => toggleFloating('ai'),
      },
      {
        id: 'panel:toggle-editor',
        label:
          mainPanel === 'editor'
            ? '写作已是主区域'
            : panelState.editor.open
              ? '关闭写作浮窗'
              : '打开写作浮窗',
        hint: 'Ctrl+Shift+3',
        icon: <EditOutlined />,
        category: '浮窗',
        keywords: ['edit', 'writer', '写作'],
        run: () => toggleFloating('editor'),
      },
      {
        id: 'panel:focus-editor',
        label: mainPanel === 'editor' ? '写作已是主区域' : '切到写作主区域',
        icon: <EditOutlined />,
        category: '浮窗',
        keywords: ['edit', 'writer', '写作', 'main', '主'],
        run: () => setMain('editor'),
      },
      {
        id: 'action:diff-history',
        label: '打开本章 diff 历史',
        hint: 'Ctrl+Shift+H · 回滚某一次 AI 改动',
        icon: <HistoryOutlined />,
        category: '动作',
        keywords: ['diff', 'history', '历史', '回滚'],
        run: () => { window.dispatchEvent(new CustomEvent('editor-open-diff-history')) },
      },
      {
        id: 'action:reformat',
        label: '一键排版正文',
        hint: '去首行空白 / 删空行',
        icon: <AlignLeftOutlined />,
        category: '动作',
        keywords: ['format', 'reformat', '排版'],
        run: () => { window.dispatchEvent(new CustomEvent('editor-reformat')) },
      },
      {
        id: 'action:copy-title',
        label: '复制章节标题',
        hint: '剔除「第 X 章」前缀',
        icon: <BorderlessTableOutlined />,
        category: '动作',
        keywords: ['copy', 'title', '标题'],
        run: () => { window.dispatchEvent(new CustomEvent('editor-copy-title')) },
      },
      {
        id: 'action:copy-content',
        label: '复制章节正文',
        icon: <CopyOutlined />,
        category: '动作',
        keywords: ['copy', 'content', '正文'],
        run: () => { window.dispatchEvent(new CustomEvent('editor-copy-content')) },
      },
    ]

    if (onOpenSettings) {
      base.push({
        id: 'action:settings',
        label: '打开设置',
        icon: <SettingOutlined />,
        category: '动作',
        keywords: ['settings', '设置', '配置'],
        run: () => onOpenSettings(),
      })
    }

    // 章节导航（动态）
    const chapterCommands: CommandItem[] = writingChapters.map((c, i) => ({
      id: `chapter:${c.id}`,
      label: c.title || `章节 ${i + 1}`,
      hint: activeWritingChapterId === c.id ? '当前章节' : undefined,
      icon: <ReadOutlined />,
      category: '章节',
      keywords: ['chapter', '章节', String(i + 1)],
      run: () => handleWritingSelect(c.id, c.title),
    }))

    return [...base, ...chapterCommands]
  }, [toggleFloating, setMain, mainPanel, handleWritingSelect, writingChapters, activeWritingChapterId, panelState.left.open, panelState.editor.open, panelState.ai.open, onOpenSettings])

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
    activeWritingChapterId,
  ])

  return (
    <WorkspaceContext.Provider value={workspaceContextValue}>
    <DiffProvider>
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
                aiAgentMode={aiAgentMode}
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
                  aiAgentMode={aiAgentMode}
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
      </div>
      <CommandPalette
        open={commandPaletteOpen}
        commands={paletteCommands}
        onClose={() => setCommandPaletteOpen(false)}
      />
    </DiffProvider>
    </WorkspaceContext.Provider>
  )
}

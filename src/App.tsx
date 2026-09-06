import { services } from '@/services'
import React, { Suspense, lazy } from 'react'
import {
  Navigate,
  Route,
  Routes,
  matchPath,
  parsePath,
  useLocation,
  useNavigate,
} from 'react-router-dom'
import { PurrSpin, usePurrToast } from '@/purr-components'
import GlobalActions from './components/GlobalActions'
import { Book, type AiModelConfig, type EntityId, type MemoryEmbeddingConfig } from './types'
import { applyModelRuntimeConfigPatch } from './modelCatalog'
import { installModelDescriptors } from './models/registry'
import './App.scss'

const HomePage = lazy(() => import('./HomePage'))
const BookshelfPage = lazy(() => import('./BookshelfPage'))
const Workspace = lazy(() => import('./Workspace'))
const SettingsPage = lazy(() => import('./SettingsPage'))
const ScreenplayAgentPage = lazy(() => import('./ScreenplayAgentPage'))
const WritingMethodsPage = lazy(() => import('./WritingMethodsPage'))
const NovelSourcesPage = lazy(() => import('./NovelSourcesPage'))
const AiDevInspector = import.meta.env.DEV
  ? lazy(() => import('./components/AiDevInspector'))
  : null

type Page = 'home' | 'screenplay' | 'bookshelf' | 'writingMethods' | 'novelSources' | 'workspace'
type BooksStatus = 'idle' | 'loading' | 'loaded'
type SettingsLocationState = { returnTo?: string }
const LAST_OPENED_BOOK_STORAGE_KEY = 'purr-typos:last-opened-book-id'

function getStoredLastOpenedBookId(): EntityId | null {
  try {
    return localStorage.getItem(LAST_OPENED_BOOK_STORAGE_KEY)
  } catch {
    return null
  }
}

function storeLastOpenedBookId(bookId: EntityId | null) {
  try {
    if (bookId == null) {
      localStorage.removeItem(LAST_OPENED_BOOK_STORAGE_KEY)
    } else {
      localStorage.setItem(LAST_OPENED_BOOK_STORAGE_KEY, bookId)
    }
  } catch {
    // 本地存储不可用时不影响书籍打开与删除。
  }
}

export default function App() {
  const appMessage = usePurrToast()
  const location = useLocation()
  const navigate = useNavigate()
  const settingsState = location.state as SettingsLocationState | null
  const showSettings = location.pathname === '/settings'
  const contentLocation = showSettings ? settingsState?.returnTo || '/' : location
  const contentPath = typeof contentLocation === 'string'
    ? parsePath(contentLocation).pathname || '/'
    : contentLocation.pathname
  const workspaceMatch = matchPath('/books/:bookId', contentPath)
  const page: Page = workspaceMatch
    ? 'workspace'
    : contentPath === '/screenplay' || contentPath.startsWith('/screenplay/')
      ? 'screenplay'
      : contentPath === '/bookshelf'
        ? 'bookshelf'
        : contentPath === '/writing-methods'
          ? 'writingMethods'
        : contentPath === '/novel-sources' || contentPath.startsWith('/novel-sources/')
          ? 'novelSources'
        : 'home'
  const [books, setBooks] = React.useState<Book[]>([])
  const [booksStatus, setBooksStatus] = React.useState<BooksStatus>('idle')
  const activeBook = workspaceMatch
    ? books.find((book) => book.id === workspaceMatch.params.bookId) ?? null
    : null
  const [workspaceReady, setWorkspaceReady] = React.useState(false)
  const [lastOpenedBookId, setLastOpenedBookId] = React.useState<EntityId | null>(
    getStoredLastOpenedBookId,
  )
  const [modelConfigs, setModelConfigs] = React.useState<AiModelConfig[]>([])
  const [memoryModelId, setMemoryModelId] = React.useState('')
  const [memoryEmbeddingConfig, setMemoryEmbeddingConfig] = React.useState<MemoryEmbeddingConfig | null>(null)
  const configuredModelConfigs = React.useMemo(
    () => modelConfigs.filter((config) => config.apiKey?.trim()),
    [modelConfigs],
  )
  const [syncOutlineChapter, setSyncOutlineChapter] = React.useState(false)

  React.useEffect(() => {
    services.settings.getSettings().then((res) => {
      if (!res.success || !res.data) return
      if (res.data.model_descriptors) installModelDescriptors(res.data.model_descriptors)
      setSyncOutlineChapter(!!res.data.sync_outline_chapter)
      if (Array.isArray(res.data.ai_model_configs)) {
        setModelConfigs(res.data.ai_model_configs)
      }
      setMemoryModelId(typeof res.data.memory_model_id === 'string' ? res.data.memory_model_id : '')
      const embedding = res.data.memory_embedding_config
      setMemoryEmbeddingConfig(
        embedding && typeof embedding === 'object'
          ? embedding as MemoryEmbeddingConfig
          : null,
      )
    })
  }, [])

  const saveModelConfigs = React.useCallback((configs: AiModelConfig[]) => {
    setModelConfigs(configs)
    services.settings.setSettings({ ai_model_configs: configs })
  }, [])

  const saveMemoryConfiguration = React.useCallback((
    modelId: string,
    embedding: MemoryEmbeddingConfig | null,
  ) => {
    setMemoryModelId(modelId)
    setMemoryEmbeddingConfig(embedding)
    void services.settings.setSettings({
      memory_model_id: modelId,
      memory_embedding_config: embedding,
    })
  }, [])

  const updateModelConfig = React.useCallback((
    id: string,
    patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled' | 'reasoningEffort' | 'modelPreferences'>>,
  ) => {
    setModelConfigs((prev) => {
      const next = prev.map((item) =>
        item.id === id
          ? applyModelRuntimeConfigPatch(item, patch)
          : item,
      )
      void services.settings.setSettings({ ai_model_configs: next })
      return next
    })
  }, [])

  const handleSyncOutlineChapterChange = React.useCallback((value: boolean) => {
    setSyncOutlineChapter(value)
    services.settings.setSettings({ sync_outline_chapter: value })
  }, [])

  const loadBooks = React.useCallback(async () => {
    setBooksStatus('loading')
    const res = await services.books.getBooks()
    if (res.success && res.data) {
      setBooks(res.data)
      setBooksStatus('loaded')
      setLastOpenedBookId((storedBookId) => {
        if (storedBookId == null || res.data.some((book) => book.id === storedBookId)) {
          return storedBookId
        }
        storeLastOpenedBookId(null)
        return null
      })
    } else {
      setBooksStatus('loaded')
    }
  }, [])

  React.useEffect(() => {
    if (page === 'bookshelf' || page === 'screenplay' || page === 'workspace' || page === 'novelSources') loadBooks()
  }, [page, loadBooks])

  const handleEnterBookshelf = React.useCallback(() => {
    navigate('/bookshelf')
  }, [navigate])

  const handleEnterScreenplayAgent = React.useCallback(() => {
    navigate('/screenplay')
  }, [navigate])

  const handleEnterWritingMethods = React.useCallback(() => {
    navigate('/writing-methods')
  }, [navigate])

  const handleEnterNovelSources = React.useCallback(() => {
    navigate('/novel-sources')
  }, [navigate])

  const handleOpenSettings = React.useCallback(() => {
    navigate('/settings', {
      state: {
        returnTo: `${location.pathname}${location.search}${location.hash}`,
      } satisfies SettingsLocationState,
    })
  }, [location.hash, location.pathname, location.search, navigate])

  const handleCloseSettings = React.useCallback(() => {
    navigate(
      settingsState?.returnTo && settingsState.returnTo !== '/settings'
        ? settingsState.returnTo
        : '/',
      { replace: true },
    )
  }, [navigate, settingsState])

  const handleOpenBook = React.useCallback((book: Book) => {
    storeLastOpenedBookId(book.id)
    setLastOpenedBookId(book.id)
    setWorkspaceReady(false)
    navigate(`/books/${encodeURIComponent(book.id)}`)
  }, [navigate])

  const handleCreateBook = React.useCallback(async (title: string, enableVolume?: boolean) => {
    const res = await services.books.createBook({ title, enableVolume })
    if (res.success) {
      await loadBooks()
    } else {
      appMessage.error('创建书籍失败')
    }
  }, [loadBooks, appMessage])

  const handleDeleteBook = React.useCallback(async (bookId: EntityId) => {
    const res = await services.books.deleteBook({ bookId })
    if (res.success) {
      await loadBooks()
      appMessage.success('书籍已删除')
    } else {
      appMessage.error('删除失败')
    }
  }, [loadBooks, appMessage])

  const handleRenameBook = React.useCallback(async (bookId: EntityId, title: string) => {
    const res = await services.books.renameBook({ bookId, title })
    if (res.success) {
      await loadBooks()
    } else {
      appMessage.error('重命名失败')
    }
  }, [loadBooks, appMessage])

  const handleBackToBookshelf = React.useCallback(() => {
    setWorkspaceReady(false)
    navigate('/bookshelf')
  }, [navigate])

  const handleBackToHome = React.useCallback(() => {
    setWorkspaceReady(false)
    navigate('/')
  }, [navigate])

  const handleWorkspaceReady = React.useCallback(() => {
    setWorkspaceReady(true)
  }, [])

  const ambientPage: Page =
    page === 'workspace' && !workspaceReady ? 'bookshelf' : page

  React.useEffect(() => {
    if (page === 'workspace' && booksStatus === 'loaded' && !activeBook) {
      navigate('/bookshelf', { replace: true })
    }
  }, [activeBook, booksStatus, navigate, page])

  React.useEffect(() => {
    setWorkspaceReady(false)
  }, [workspaceMatch?.params.bookId])

  return (
    <div className={`app-root app-root--${page} app-root--ambient-${ambientPage}`}>
      <div className="app-ambient-glow" aria-hidden="true" />
      {page === 'home' && (
        <div className="app-global-actions">
          <GlobalActions />
        </div>
      )}
      <main className="app-main">
        <Suspense fallback={<div className="app-page-loading"><PurrSpin size="large" /></div>}>
          <Routes location={contentLocation}>
            <Route path="/" element={(
              <HomePage
                onEnterScreenplayAgent={handleEnterScreenplayAgent}
                onEnterBookshelf={handleEnterBookshelf}
                onOpenSettings={handleOpenSettings}
              />
            )} />
            <Route path="/screenplay/*" element={(
              <ScreenplayAgentPage
                books={books}
                modelConfigs={configuredModelConfigs}
                onUpdateModelConfig={updateModelConfig}
                onOpenBookshelf={handleEnterBookshelf}
                onOpenSettings={handleOpenSettings}
                onBack={handleBackToHome}
              />
            )} />
            <Route path="/bookshelf" element={(
              <BookshelfPage
                books={books}
                lastOpenedBookId={lastOpenedBookId}
                onOpenBook={handleOpenBook}
                onCreateBook={handleCreateBook}
                onDeleteBook={handleDeleteBook}
                onRenameBook={handleRenameBook}
                onContinuationCreated={loadBooks}
                onOpenNovelSources={handleEnterNovelSources}
                onOpenWritingMethods={handleEnterWritingMethods}
                onBack={handleBackToHome}
              />
            )} />
            <Route path="/writing-methods" element={(
              <WritingMethodsPage onBack={handleEnterBookshelf} onHome={handleBackToHome} />
            )} />
            <Route path="/novel-sources/:workId?" element={(
              <NovelSourcesPage
                books={books}
                modelConfigs={configuredModelConfigs}
                onUpdateModelConfig={updateModelConfig}
                onBack={handleEnterBookshelf}
                onHome={handleBackToHome}
              />
            )} />
            <Route path="/books/:bookId" element={activeBook ? (
              <Workspace
                bookId={activeBook.id}
                bookTitle={activeBook.title}
                enableVolume={!!activeBook.enable_volume}
                creationMode={activeBook.creation_mode ?? 'original'}
                onBack={handleBackToBookshelf}
                onGoHome={handleBackToHome}
                onOpenSettings={handleOpenSettings}
                modelConfigs={configuredModelConfigs}
                onUpdateModelConfig={updateModelConfig}
                syncOutlineChapter={syncOutlineChapter}
                onReady={handleWorkspaceReady}
              />
            ) : <div className="app-page-loading"><PurrSpin size="large" /></div>} />
            <Route path="/settings" element={null} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </main>
      {showSettings && (
        <div className="app-settings-overlay">
          <Suspense fallback={<PurrSpin size="large" />}>
            <SettingsPage
              modelConfigs={modelConfigs}
              onSaveModelConfigs={saveModelConfigs}
              memoryModelId={memoryModelId}
              memoryEmbeddingConfig={memoryEmbeddingConfig}
              onSaveMemoryConfiguration={saveMemoryConfiguration}
              onClose={handleCloseSettings}
              syncOutlineChapter={syncOutlineChapter}
              onSyncOutlineChapterChange={handleSyncOutlineChapterChange}
            />
          </Suspense>
        </div>
      )}
      {AiDevInspector && (
        <Suspense fallback={null}>
          <AiDevInspector />
        </Suspense>
      )}
    </div>
  )
}

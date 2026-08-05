import { services } from '@/services'
import React, { Suspense, lazy } from 'react'
import { PurrSpin, usePurrToast } from '@/purr-components'
import GlobalActions from './components/GlobalActions'
import { Book, type AiModelConfig, type EntityId } from './types'
import { applyModelRuntimeConfigPatch, migrateKnownModelConfigs } from './modelCatalog'
import './App.scss'

const HomePage = lazy(() => import('./HomePage'))
const BookshelfPage = lazy(() => import('./BookshelfPage'))
const Workspace = lazy(() => import('./Workspace'))
const SettingsPage = lazy(() => import('./SettingsPage'))
const ScreenplayAgentPage = lazy(() => import('./ScreenplayAgentPage'))
const AiDevInspector = import.meta.env.DEV
  ? lazy(() => import('./components/AiDevInspector'))
  : null

type Page = 'home' | 'screenplay' | 'bookshelf' | 'workspace'
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
  const [page, setPage] = React.useState<Page>('home')
  const [books, setBooks] = React.useState<Book[]>([])
  const [activeBook, setActiveBook] = React.useState<Book | null>(null)
  const [workspaceReady, setWorkspaceReady] = React.useState(false)
  const [lastOpenedBookId, setLastOpenedBookId] = React.useState<EntityId | null>(
    getStoredLastOpenedBookId,
  )

  const [showSettings, setShowSettings] = React.useState(false)
  const [modelConfigs, setModelConfigs] = React.useState<AiModelConfig[]>([])
  const configuredModelConfigs = React.useMemo(
    () => modelConfigs.filter((config) => config.apiKey?.trim()),
    [modelConfigs],
  )
  const [syncOutlineChapter, setSyncOutlineChapter] = React.useState(false)

  React.useEffect(() => {
    services.settings.getSettings().then((res) => {
      if (!res.success || !res.data) return
      setSyncOutlineChapter(!!res.data.sync_outline_chapter)
      if (Array.isArray(res.data.ai_model_configs)) {
        const migration = migrateKnownModelConfigs(res.data.ai_model_configs)
        setModelConfigs(migration.configs)
        if (migration.changed) {
          void services.settings.setSettings({ ai_model_configs: migration.configs })
        }
      }
    })
  }, [])

  const saveModelConfigs = React.useCallback((configs: AiModelConfig[]) => {
    setModelConfigs(configs)
    services.settings.setSettings({ ai_model_configs: configs })
  }, [])

  const updateModelConfig = React.useCallback((
    id: string,
    patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
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
    const res = await services.books.getBooks()
    if (res.success && res.data) {
      setBooks(res.data)
      setLastOpenedBookId((storedBookId) => {
        if (storedBookId == null || res.data.some((book) => book.id === storedBookId)) {
          return storedBookId
        }
        storeLastOpenedBookId(null)
        return null
      })
    }
  }, [])

  React.useEffect(() => {
    if (page === 'bookshelf' || page === 'screenplay') loadBooks()
  }, [page, loadBooks])

  const handleEnterBookshelf = React.useCallback(() => {
    setPage('bookshelf')
  }, [])

  const handleEnterScreenplayAgent = React.useCallback(() => {
    setPage('screenplay')
  }, [])

  const handleOpenBook = React.useCallback((book: Book) => {
    storeLastOpenedBookId(book.id)
    setLastOpenedBookId(book.id)
    setWorkspaceReady(false)
    setActiveBook(book)
    setPage('workspace')
  }, [])

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
    setActiveBook(null)
    setPage('bookshelf')
  }, [])

  const handleBackToHome = React.useCallback(() => {
    setWorkspaceReady(false)
    setPage('home')
  }, [])

  const handleWorkspaceReady = React.useCallback(() => {
    setWorkspaceReady(true)
  }, [])

  const ambientPage: Page =
    page === 'workspace' && !workspaceReady ? 'bookshelf' : page

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
          {page === 'home' && (
            <HomePage
              onEnterScreenplayAgent={handleEnterScreenplayAgent}
              onEnterBookshelf={handleEnterBookshelf}
              onOpenSettings={() => setShowSettings(true)}
            />
          )}
          {page === 'screenplay' && (
            <ScreenplayAgentPage
              books={books}
              modelConfigs={configuredModelConfigs}
              onUpdateModelConfig={updateModelConfig}
              onOpenBookshelf={handleEnterBookshelf}
              onOpenSettings={() => setShowSettings(true)}
              onBack={handleBackToHome}
            />
          )}
          {page === 'bookshelf' && (
            <BookshelfPage
              books={books}
              lastOpenedBookId={lastOpenedBookId}
              onOpenBook={handleOpenBook}
              onCreateBook={handleCreateBook}
              onDeleteBook={handleDeleteBook}
              onRenameBook={handleRenameBook}
              onBack={handleBackToHome}
            />
          )}
          {page === 'workspace' && activeBook && (
            <Workspace
              bookId={activeBook.id}
              bookTitle={activeBook.title}
              enableVolume={!!activeBook.enable_volume}
              onBack={handleBackToBookshelf}
              onGoHome={handleBackToHome}
              onOpenSettings={() => setShowSettings(true)}
              modelConfigs={configuredModelConfigs}
              onUpdateModelConfig={updateModelConfig}
              syncOutlineChapter={syncOutlineChapter}
              onReady={handleWorkspaceReady}
            />
          )}
        </Suspense>
      </main>
      <footer className="app-footer">
        © 2026 Liu Yubin (PurrTypos). Powered by AI.
      </footer>

      {showSettings && (
        <div className="app-settings-overlay">
          <Suspense fallback={<PurrSpin size="large" />}>
            <SettingsPage
            modelConfigs={modelConfigs}
            onSaveModelConfigs={saveModelConfigs}
            onClose={() => setShowSettings(false)}
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

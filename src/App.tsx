import React, { Suspense, lazy } from 'react'
import { App as AntdApp, Spin } from 'antd'
import GlobalActions from './components/GlobalActions'
import { Book, type AiModelConfig, type EntityId } from './types'
import './App.scss'

const HomePage = lazy(() => import('./HomePage'))
const BookshelfPage = lazy(() => import('./BookshelfPage'))
const Workspace = lazy(() => import('./Workspace'))
const SettingsPage = lazy(() => import('./SettingsPage'))

type Page = 'home' | 'bookshelf' | 'workspace'

export default function App() {
  const { message: appMessage } = AntdApp.useApp()
  const [page, setPage] = React.useState<Page>('home')
  const [books, setBooks] = React.useState<Book[]>([])
  const [activeBook, setActiveBook] = React.useState<Book | null>(null)

  const [showSettings, setShowSettings] = React.useState(false)
  const [modelConfigs, setModelConfigs] = React.useState<AiModelConfig[]>([])
  const [syncOutlineChapter, setSyncOutlineChapter] = React.useState(false)

  React.useEffect(() => {
    window.electronAPI.getSettings().then((res) => {
      if (!res.success || !res.data) return
      setSyncOutlineChapter(!!res.data.sync_outline_chapter)
      if (Array.isArray(res.data.ai_model_configs)) {
        setModelConfigs(res.data.ai_model_configs)
      }
    })
  }, [])

  const saveModelConfigs = React.useCallback((configs: AiModelConfig[]) => {
    setModelConfigs(configs)
    window.electronAPI.setSettings({ ai_model_configs: configs })
  }, [])

  const updateModelConfig = React.useCallback((
    id: string,
    patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
  ) => {
    setModelConfigs((prev) => {
      const next = prev.map((item) =>
        item.id === id
          ? {
              ...item,
              ...patch,
              supportsThinking: patch.thinkingEnabled ?? item.supportsThinking,
              thinkingOnly: false,
            }
          : item,
      )
      void window.electronAPI.setSettings({ ai_model_configs: next })
      return next
    })
  }, [])

  const handleSyncOutlineChapterChange = React.useCallback((value: boolean) => {
    setSyncOutlineChapter(value)
    window.electronAPI.setSettings({ sync_outline_chapter: value })
  }, [])

  const loadBooks = React.useCallback(async () => {
    const res = await window.electronAPI.getBooks()
    if (res.success && res.data) setBooks(res.data)
  }, [])

  React.useEffect(() => {
    if (page === 'bookshelf') loadBooks()
  }, [page, loadBooks])

  const handleEnterBookshelf = React.useCallback(() => {
    setPage('bookshelf')
  }, [])

  const handleOpenBook = React.useCallback((book: Book) => {
    setActiveBook(book)
    setPage('workspace')
  }, [])

  const handleCreateBook = React.useCallback(async (title: string, enableVolume?: boolean) => {
    const res = await window.electronAPI.createBook({ title, enableVolume })
    if (res.success) {
      await loadBooks()
    } else {
      appMessage.error('创建书籍失败')
    }
  }, [loadBooks, appMessage])

  const handleDeleteBook = React.useCallback(async (bookId: EntityId) => {
    const res = await window.electronAPI.deleteBook({ bookId })
    if (res.success) {
      await loadBooks()
      appMessage.success('书籍已删除')
    } else {
      appMessage.error('删除失败')
    }
  }, [loadBooks, appMessage])

  const handleRenameBook = React.useCallback(async (bookId: EntityId, title: string) => {
    const res = await window.electronAPI.renameBook({ bookId, title })
    if (res.success) {
      await loadBooks()
    } else {
      appMessage.error('重命名失败')
    }
  }, [loadBooks, appMessage])

  const handleBackToBookshelf = React.useCallback(() => {
    setActiveBook(null)
    setPage('bookshelf')
  }, [])

  const handleBackToHome = React.useCallback(() => {
    setPage('home')
  }, [])

  return (
    <div className="app-root">
      {page === 'home' && (
        <div className="app-global-actions">
          <GlobalActions />
        </div>
      )}
      <main className="app-main">
        <Suspense fallback={<div className="app-page-loading"><Spin size="large" /></div>}>
          {page === 'home' && (
            <HomePage
              onEnterBookshelf={handleEnterBookshelf}
              onOpenSettings={() => setShowSettings(true)}
            />
          )}
          {page === 'bookshelf' && (
            <BookshelfPage
              books={books}
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
              modelConfigs={modelConfigs}
              onUpdateModelConfig={updateModelConfig}
              syncOutlineChapter={syncOutlineChapter}
            />
          )}
        </Suspense>
      </main>
      <footer className="app-footer">
        © 2026 Liu Yubin (PurrTypos). Powered by AI.
      </footer>

      {showSettings && (
        <div className="app-settings-overlay">
          <Suspense fallback={<Spin size="large" />}>
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
    </div>
  )
}

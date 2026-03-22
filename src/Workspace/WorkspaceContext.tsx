import React from 'react'
import type { LexicalEditor } from 'lexical'
import type { Chapter } from '../types'

export interface WorkspaceContextValue {
  bookId: number | null
  bookTitle: string
  enableVolume: boolean
  syncOutlineChapter: boolean
  activeChapterId: number | null
  activeChapterTitle: string
  writingChapters: Chapter[]
  writingOutlineId: number | null
  setActiveChapter: (id: number, title: string) => void
  setChaptersData: (outlineId: number, chapters: Chapter[]) => void
  loadWritingChapters: () => Promise<void>
  /** 工作台搜索：关键字（大纲 / 小说背景 / 写作区） */
  workspaceSearchQuery: string
  setWorkspaceSearchQuery: (q: string) => void
  workspaceSearchActiveIndex: number
  setWorkspaceSearchActiveIndex: (n: number) => void
  goToNextWorkspaceSearch: () => void
  goToPrevWorkspaceSearch: () => void
  /** 当前关键字下的匹配总数（DOM + 写作区 Lexical） */
  workspaceSearchMatchTotal: number
  /** 内容变化后通知以刷新匹配数（写作区、大纲 Markdown 等） */
  notifyWorkspaceSearchContentChanged: () => void
  lexicalEditorRef: React.MutableRefObject<LexicalEditor | null>
  setLexicalEditorRef: (editor: LexicalEditor | null) => void
}

const WorkspaceContext = React.createContext<WorkspaceContextValue | null>(null)

export function useWorkspace(): WorkspaceContextValue {
  const ctx = React.useContext(WorkspaceContext)
  if (!ctx) throw new Error('useWorkspace must be used within WorkspaceContext.Provider')
  return ctx
}

export default WorkspaceContext

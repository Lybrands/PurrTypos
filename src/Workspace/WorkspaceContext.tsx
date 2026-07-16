import React from 'react'
import type { LexicalEditor } from 'lexical'
import type { Chapter, EntityId } from '../types'
import type { WorkspaceUtilityTab } from './utilityPanelTypes'

export interface WorkspaceContextValue {
  bookId: EntityId | null
  bookTitle: string
  enableVolume: boolean
  syncOutlineChapter: boolean
  activeChapterId: EntityId | null
  activeChapterTitle: string
  writingChapters: Chapter[]
  writingOutlineId: EntityId | null
  utilityPanelOpen: boolean
  activeUtilityTabKey: string | null
  openUtilityTab: (tab: WorkspaceUtilityTab) => void
  toggleUtilityTab: (tab: WorkspaceUtilityTab) => void
  setActiveChapter: (id: EntityId, title: string) => void
  setChaptersData: (outlineId: EntityId, chapters: Chapter[]) => void
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

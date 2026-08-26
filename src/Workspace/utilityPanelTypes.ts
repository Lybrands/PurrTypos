import type { Chapter } from '../types'
import type { ChapterOutlineTarget } from './DirectorNotebook/ChapterOutlineModal'

export const EDITOR_TAB_KEY = 'editor'

export type WorkspaceUtilityTabKind =
  | 'outline'
  | 'memory'
  | 'writingMethods'
  | 'canon'
  | 'setting'
  | 'dashboard'

export interface WorkspaceUtilityTab {
  key: string
  kind: WorkspaceUtilityTabKind
  title: string
  outlineTarget?: ChapterOutlineTarget
}

export const GLOBAL_OUTLINE_TAB: WorkspaceUtilityTab = {
  key: 'outline:global',
  kind: 'outline',
  title: '总纲',
  outlineTarget: { mode: 'global' },
}

export const MEMORY_TAB: WorkspaceUtilityTab = {
  key: 'memory',
  kind: 'memory',
  title: '记忆 / 伏笔',
}

export const WRITING_METHODS_TAB: WorkspaceUtilityTab = {
  key: 'writing-methods',
  kind: 'writingMethods',
  title: '写作方法',
}

export const CANON_TAB: WorkspaceUtilityTab = {
  key: 'continuation-canon',
  kind: 'canon',
  title: '继承正史',
}

export const SETTING_TAB: WorkspaceUtilityTab = {
  key: 'setting',
  kind: 'setting',
  title: '小说设定',
}

export const DASHBOARD_TAB: WorkspaceUtilityTab = {
  key: 'dashboard',
  kind: 'dashboard',
  title: '仪表盘',
}

export function createOutlineUtilityTab(
  mode: 'chapter' | 'volume',
  chapter: Chapter,
): WorkspaceUtilityTab {
  return {
    key: `outline:${mode}:${String(chapter.id)}`,
    kind: 'outline',
    title: `${mode === 'volume' ? '卷大纲' : '章节大纲'} · ${chapter.title}`,
    outlineTarget: { mode, chapter },
  }
}

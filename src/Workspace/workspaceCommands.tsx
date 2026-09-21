import {
  AlignLeftIcon,
  CopyIcon,
  CopyTitleIcon,
  DashboardIcon,
  FileTextIcon,
  HistoryIcon,
  LibraryIcon,
  ManuscriptIcon,
  SettingsIcon,
  StorySettingIcon,
} from '@/purr-components'
import type { Chapter, EntityId } from '../types'
import type { CommandItem } from './CommandPalette'
import { runEditorCommand } from '../stores/editorCommandStore'

export interface BuildPaletteCommandsDeps {
  chapterSidebarOpen: boolean
  editorPanelActive: boolean
  onToggleChapterSidebar: () => void
  onToggleEditorPanel: () => void
  settingPanelActive: boolean
  dashboardPanelActive: boolean
  onToggleSettingPanel: () => void
  onToggleDashboardPanel: () => void
  writingChapters: Chapter[]
  activeWritingChapterId: EntityId | null
  onChapterSelect: (id: EntityId, title: string) => void
  onOpenSettings?: () => void
}

/** 命令面板命令集合（面板控制 / 编辑动作 / 章节导航） */
export function buildPaletteCommands({
  chapterSidebarOpen,
  editorPanelActive,
  onToggleChapterSidebar,
  onToggleEditorPanel,
  settingPanelActive,
  dashboardPanelActive,
  onToggleSettingPanel,
  onToggleDashboardPanel,
  writingChapters,
  activeWritingChapterId,
  onChapterSelect,
  onOpenSettings,
}: BuildPaletteCommandsDeps): CommandItem[] {
  const base: CommandItem[] = [
    {
      id: 'panel:toggle-chapters',
      label: chapterSidebarOpen ? '收起章节列表' : '展开章节列表',
      hint: 'Ctrl+B',
      icon: <LibraryIcon />,
      category: '面板',
      keywords: ['chapter', 'sidebar', 'chapters', '章节', '目录', '列表'],
      run: onToggleChapterSidebar,
    },
    {
      id: 'panel:toggle-editor',
      label: editorPanelActive ? '收起正文面板' : '切回正文',
      hint: 'Ctrl+E',
      icon: <ManuscriptIcon />,
      category: '面板',
      keywords: ['editor', 'manuscript', '正文', '写作'],
      run: onToggleEditorPanel,
    },
    {
      id: 'panel:toggle-setting',
      label: settingPanelActive ? '收起小说设定' : '打开小说设定',
      icon: <StorySettingIcon />,
      category: '面板',
      keywords: ['setting', 'character', 'background', '人物', '背景', '设定', '世界'],
      run: onToggleSettingPanel,
    },
    {
      id: 'panel:toggle-dashboard',
      label: dashboardPanelActive ? '收起仪表盘' : '打开仪表盘',
      hint: '故事健康 / 写作统计',
      icon: <DashboardIcon />,
      category: '面板',
      keywords: ['dashboard', 'stats', 'health', '仪表盘', '统计', '伏笔', '健康'],
      run: onToggleDashboardPanel,
    },
    {
      id: 'action:diff-history',
      label: '打开本章 diff 历史',
      hint: 'Ctrl+Shift+H · 回滚某一次 AI 改动',
      icon: <HistoryIcon />,
      category: '动作',
      keywords: ['diff', 'history', '历史', '回滚'],
      run: () => { runEditorCommand('openDiffHistory') },
    },
    {
      id: 'action:reformat',
      label: '一键排版正文',
      hint: '去首行空白 / 删空行',
      icon: <AlignLeftIcon />,
      category: '动作',
      keywords: ['format', 'reformat', '排版'],
      run: () => { runEditorCommand('reformat') },
    },
    {
      id: 'action:copy-title',
      label: '复制章节标题',
      hint: '剔除「第 X 章」前缀',
      icon: <CopyTitleIcon />,
      category: '动作',
      keywords: ['copy', 'title', '标题'],
      run: () => { runEditorCommand('copyTitle') },
    },
    {
      id: 'action:copy-content',
      label: '复制章节正文',
      icon: <CopyIcon />,
      category: '动作',
      keywords: ['copy', 'content', '正文'],
      run: () => { runEditorCommand('copyContent') },
    },
  ]

  if (onOpenSettings) {
    base.push({
      id: 'action:settings',
      label: '打开设置',
      icon: <SettingsIcon />,
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
    icon: <FileTextIcon />,
    category: '章节',
    keywords: ['chapter', '章节', String(i + 1)],
    run: () => onChapterSelect(c.id, c.title),
  }))

  return [...base, ...chapterCommands]
}

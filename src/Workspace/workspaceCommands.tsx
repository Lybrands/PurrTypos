import {
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
import type { Chapter, EntityId } from '../types'
import type { CommandItem } from './CommandPalette'
import type {
  MainPanelKey,
  PanelKey,
  PersistedPanelState,
} from './hooks/usePanelLayout'

export interface BuildPaletteCommandsDeps {
  panelState: PersistedPanelState
  mainPanel: MainPanelKey
  toggleFloating: (key: PanelKey) => void
  setMain: (key: MainPanelKey) => void
  writingChapters: Chapter[]
  activeWritingChapterId: EntityId | null
  onChapterSelect: (id: EntityId, title: string) => void
  onOpenSettings?: () => void
}

/** 命令面板命令集合（浮窗控制 / 编辑动作 / 章节导航） */
export function buildPaletteCommands({
  panelState,
  mainPanel,
  toggleFloating,
  setMain,
  writingChapters,
  activeWritingChapterId,
  onChapterSelect,
  onOpenSettings,
}: BuildPaletteCommandsDeps): CommandItem[] {
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
      id: 'panel:toggle-setting',
      label: panelState.setting.open ? '关闭设定面板' : '打开设定面板',
      icon: <ReadOutlined />,
      category: '浮窗',
      keywords: ['setting', 'character', 'background', '人物', '背景', '设定'],
      run: () => toggleFloating('setting'),
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
    run: () => onChapterSelect(c.id, c.title),
  }))

  return [...base, ...chapterCommands]
}

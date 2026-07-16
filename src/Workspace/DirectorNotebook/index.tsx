import React from 'react'
import { DoubleRightOutlined } from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import type { EntityId } from '../../types'
import ChapterSection from './ChapterSection'
import './index.scss'

/**
 * 兼容旧外层调用：原 OutlineSection 中用于「同步章节-大纲选中」的 payload。
 * 大纲分组已合并进章节分组（辅助面板形式），此类型保留为空壳。
 */
export interface ChapterOutlineSelectInfo {
  title: string
  writingChapterId?: EntityId | null
}

export interface DirectorNotebookProps {
  bookTitle: string
  /** 把悬停预览固定展开；固定边栏本身不显示收起按钮。 */
  onExpandDock?: () => void
  /** 当前是否处于窄轨道触发的悬停预览。 */
  dockCollapsed?: boolean
  onItemCreated?: (chapterId: EntityId, title: string, isVolume: boolean, parentWritingChapterId: EntityId | null) => void
  onWritingChapterDeleted?: (writingChapterId: EntityId) => void
}

/**
 * 左栏：章节列表（曾名「导演笔记本」，v3 起精简）。
 *
 * 设计理念：
 * - 主体直接就是章节列表，不再嵌套「分组 / 折叠头」结构，最纯净。
 * - 人物 / 故事背景 / 世界设定统一由工作台顶栏的「小说设定」进入。
 * - 记忆 · 伏笔 / 风格基调放在工作台顶栏的独立入口。
 * - 「总纲」放在工作台顶栏；卷大纲 / 章节大纲保留行内入口。
 */
export default function DirectorNotebook({
  bookTitle,
  onExpandDock,
  dockCollapsed = false,
  onItemCreated,
  onWritingChapterDeleted,
}: DirectorNotebookProps) {
  return (
    <div className="director-notebook director-notebook--list">
      <div className="director-notebook-header">
        <span className="director-notebook-title">章节列表</span>
        <div className="director-notebook-header-right">
          {dockCollapsed && onExpandDock ? (
            <Tooltip title="固定展开章节边栏">
              <Button
                type="text"
                size="small"
                icon={<DoubleRightOutlined style={{ fontSize: 14 }} />}
                onClick={onExpandDock}
                className="director-notebook-fullscreen-btn"
              />
            </Tooltip>
          ) : null}
        </div>
      </div>

      <div className="director-notebook-body">
        <ChapterSection
          bookTitle={bookTitle}
          onItemCreated={onItemCreated}
          onWritingChapterDeleted={onWritingChapterDeleted}
        />
      </div>
    </div>
  )
}

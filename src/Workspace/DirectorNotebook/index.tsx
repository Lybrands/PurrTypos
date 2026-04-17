import React from 'react'
import { ExpandOutlined, CompressOutlined } from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import type { EntityId } from '../../types'
import ChapterSection from './ChapterSection'
import NotebookToolbar from './NotebookToolbar'
import './index.scss'

/**
 * 兼容旧外层调用：原 OutlineSection 中用于「同步章节-大纲选中」的 payload。
 * 大纲分组已合并进章节分组（弹窗形式），此类型保留为空壳。
 */
export interface ChapterOutlineSelectInfo {
  title: string
  writingChapterId?: EntityId | null
}

export interface DirectorNotebookProps {
  bookTitle: string
  /**
   * 是否为当前主区域。
   * 注意：在新的 AI-Centric 工作区里章节列表只能浮窗，不再升格为主区域，
   * 因此该值在外部基本恒为 false，但保留 prop 以兼容旧调用与未来变化。
   */
  isMain?: boolean
  /**
   * 点击 ⤢ 时回调：未提供则隐藏「扩大为主」按钮（用于章节列表浮窗模式）。
   */
  onSetMain?: () => void
  onItemCreated?: (chapterId: EntityId, title: string, isVolume: boolean, parentWritingChapterId: EntityId | null) => void
  onWritingChapterDeleted?: (writingChapterId: EntityId) => void
}

/**
 * 左栏：章节列表（曾名「导演笔记本」，v3 起精简）。
 *
 * 设计理念：
 * - 主体直接就是章节列表，不再嵌套「分组 / 折叠头」结构，最纯净。
 * - 「人物 / 故事背景 / 记忆 · 伏笔 / 风格基调」收成头部右侧的图标按钮，
 *   点击通过弹窗展示，不打扰章节列表的浏览。
 * - 「总纲 / 卷大纲 / 章节大纲」通过章节内联或行内的图标入口弹窗承载。
 */
export default function DirectorNotebook({
  bookTitle,
  isMain = false,
  onSetMain,
  onItemCreated,
  onWritingChapterDeleted,
}: DirectorNotebookProps) {
  return (
    <div className={`director-notebook director-notebook--list ${isMain ? 'panel-main' : ''}`}>
      <div className="director-notebook-header">
        <span className="director-notebook-title">章节列表</span>
        <div className="director-notebook-header-right">
          <NotebookToolbar />
          {onSetMain ? (
            <Tooltip title={isMain ? '已是主区域' : '扩大此区域为主'}>
              <Button
                type="text"
                size="small"
                icon={isMain ? <CompressOutlined style={{ fontSize: 14 }} /> : <ExpandOutlined style={{ fontSize: 14 }} />}
                onClick={onSetMain}
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

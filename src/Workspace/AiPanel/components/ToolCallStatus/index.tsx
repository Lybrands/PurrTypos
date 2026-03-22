import React from 'react'
import {
  EditOutlined,
  CheckCircleOutlined,
  FileSearchOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import './index.scss'

function getRunningText(label: string): string {
  const isEditing = label.startsWith('编辑章节')
  const isSearchMemory = label === '长期记忆'
  const isAddMemory = label === '添加记忆'
  const isAddForeshadowing = label === '添加伏笔'
  if (isEditing) return `正在${label}`
  if (isSearchMemory) return `正在查找${label}`
  if (isAddMemory || isAddForeshadowing) return `正在${label}`
  return `正在查看${label}`
}

function getDoneText(label: string): string {
  if (label.startsWith('编辑章节')) return `已完成${label}`
  if (label === '长期记忆') return '已查询长期记忆'
  if (label === '添加记忆') return '已添加记忆'
  if (label === '添加伏笔') return '已添加伏笔'
  return `已完成：${label}`
}

function getPendingText(label: string): string {
  return `待执行：${label}`
}

export interface ToolCallStatusProps {
  /** 工具调用名称列表（如「编辑章节」「长期记忆」等） */
  labels: string[]
  /**
   * 本段内已执行完成的工具数量（0..labels.length）。
   * 等于 labels.length 表示本段全部完成。
   */
  completedToolCount: number
}

type RowPhase = 'done' | 'running' | 'pending'

function rowPhase(
  idx: number,
  completedToolCount: number,
  labelCount: number,
): RowPhase {
  if (idx < completedToolCount) return 'done'
  if (idx === completedToolCount && completedToolCount < labelCount) return 'running'
  return 'pending'
}

/** 工具调用状态提示：按执行顺序展示每条完成 / 进行中 / 待执行 */
export default function ToolCallStatus({
  labels,
  completedToolCount,
}: ToolCallStatusProps) {
  const n = labels.length
  const done = Math.min(Math.max(0, completedToolCount), n)

  return (
    <div className="bubble-tool-calls">
      {labels.map((label, idx) => {
        const phase = rowPhase(idx, done, n)
        const isEditing = label.startsWith('编辑章节')
        const statusText =
          phase === 'done'
            ? getDoneText(label)
            : phase === 'running'
              ? getRunningText(label)
              : getPendingText(label)
        const flicker = phase === 'running'
        return (
          <div
            key={idx}
            className={`bubble-tool-call-line ${flicker ? 'a-flicker-opacity' : ''} bubble-tool-call-line--${phase}`}
          >
            {phase === 'done' ? (
              <CheckCircleOutlined className="bubble-tool-call-icon" />
            ) : phase === 'running' ? (
              isEditing ? (
                <EditOutlined className="bubble-tool-call-icon" />
              ) : (
                <FileSearchOutlined className="bubble-tool-call-icon" />
              )
            ) : (
              <ClockCircleOutlined className="bubble-tool-call-icon bubble-tool-call-icon--pending" />
            )}
            <span>{statusText}</span>
          </div>
        )
      })}
    </div>
  )
}

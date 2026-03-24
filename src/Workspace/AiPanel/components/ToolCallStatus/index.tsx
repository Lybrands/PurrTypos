import React from 'react'
import { Collapse } from 'antd'
import {
  EditOutlined,
  CheckCircleOutlined,
  FileSearchOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import './index.scss'

function getRunningText(label: string): string {
  return `正在执行 ${label}`
}

function getDoneText(label: string): string {
  return `已完成 ${label}`
}

function getPendingText(label: string): string {
  return `待执行 ${label}`
}

export interface ToolCallStatusProps {
  /** 工具调用名称列表（如「编辑章节」「长期记忆」等） */
  labels: string[]
  /**
   * 本段内已执行完成的工具数量（0..labels.length）。
   * 等于 labels.length 表示本段全部完成。
   */
  completedToolCount: number
  trace?: {
    insertedByDag?: number
    insertedSkillNames?: string[]
    plannedToolNames?: string[]
    repairedRounds?: number
    repairReasons?: string[]
  }
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
  trace,
}: ToolCallStatusProps) {
  const n = labels.length
  const done = Math.min(Math.max(0, completedToolCount), n)
  const hasTrace =
    Boolean(trace?.insertedByDag) ||
    Boolean(trace?.repairedRounds) ||
    Boolean(trace?.insertedSkillNames?.length) ||
    Boolean(trace?.repairReasons?.length)

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
      {hasTrace ? (
        <Collapse
          size="small"
          ghost
          className="bubble-tool-trace"
          items={[
            {
              key: 'trace',
              label: '查看执行轨迹',
              children: (
                <div className="bubble-tool-trace-content">
                  {trace?.insertedByDag ? (
                    <div>自动补前置：{trace.insertedByDag} 个</div>
                  ) : null}
                  {trace?.insertedSkillNames?.length ? (
                    <div>补齐步骤：{trace.insertedSkillNames.join('、')}</div>
                  ) : null}
                  {trace?.repairedRounds ? (
                    <div>自动修复：{trace.repairedRounds} 次</div>
                  ) : null}
                  {trace?.repairReasons?.length ? (
                    <div>修复原因：{trace.repairReasons.join('；')}</div>
                  ) : null}
                </div>
              ),
            },
          ]}
        />
      ) : null}
    </div>
  )
}

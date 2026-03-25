import React from 'react'
import { Collapse } from 'antd'
import {
  EditOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
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

export type ToolCallLabelOutcome = 'ok' | 'context_error'

export interface ToolCallStatusProps {
  /** 工具调用名称列表（如「编辑章节」「长期记忆」等） */
  labels: string[]
  /** 与 labels 等长；context_error 表示目录/参数与当前书籍不一致，按失败展示 */
  labelOutcomes?: ToolCallLabelOutcome[]
  /** 与 labels 等长；为 true 时表示该次调用命中会话内只读缓存，不展示本行 */
  cachedFlags?: boolean[]
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

type TraceData = NonNullable<ToolCallStatusProps['trace']>

/** 执行轨迹单独维护展开状态，避免父组件高频重绘导致 Collapse 被重置收起 */
function ExecutionTracePanel({ trace }: { trace: TraceData }) {
  const [activeKey, setActiveKey] = React.useState<string | string[]>([])
  const items = React.useMemo(
    () => [
      {
        key: 'trace',
        label: '查看执行轨迹',
        children: (
          <div className="bubble-tool-trace-content">
            {trace.insertedByDag ? (
              <div>自动补前置：{trace.insertedByDag} 个</div>
            ) : null}
            {trace.insertedSkillNames?.length ? (
              <div>补齐步骤：{trace.insertedSkillNames.join('、')}</div>
            ) : null}
            {trace.repairedRounds ? (
              <div>自动修复：{trace.repairedRounds} 次</div>
            ) : null}
            {trace.repairReasons?.length ? (
              <div>修复原因：{trace.repairReasons.join('；')}</div>
            ) : null}
          </div>
        ),
      },
    ],
    [
      trace.insertedByDag,
      trace.repairedRounds,
      trace.insertedSkillNames,
      trace.repairReasons,
    ],
  )
  return (
    <Collapse
      size="small"
      ghost
      className="bubble-tool-trace"
      activeKey={activeKey}
      onChange={setActiveKey}
      items={items}
    />
  )
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
  labelOutcomes,
  cachedFlags,
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
        const outcome = labelOutcomes?.[idx] ?? 'ok'
        if (outcome === 'context_error') {
          return (
            <div
              key={idx}
              className="bubble-tool-call-line bubble-tool-call-line--error"
            >
              <CloseCircleOutlined className="bubble-tool-call-icon" />
              <span>
                失败：{label}
                — 信息有误（当前书籍章节目录中无对应章节或工具参数无效）
              </span>
            </div>
          )
        }
        const phase = rowPhase(idx, done, n)
        if (cachedFlags?.[idx]) {
          return null
        }
        const isEditing = label.startsWith('编辑')
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
      {hasTrace && trace ? <ExecutionTracePanel trace={trace} /> : null}
    </div>
  )
}

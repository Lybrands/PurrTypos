import React from 'react'
import {
  CheckCircleIcon,
  ClockIcon,
  EditIcon,
  FileSearchIcon,
} from '@/purr-components'
import type { ToolCallLabelOutcome } from '../../../agent-runtime/contracts'
import {
  buildToolCallRows,
  type ToolCallRow,
} from './presentation'
import './index.scss'

export interface ToolCallStatusProps {
  labels: string[]
  labelOutcomes?: ToolCallLabelOutcome[]
  cachedFlags?: boolean[]
  completedToolCount: number
  itemDurationsMs?: Array<number | null>
  activeItemStartedAt?: number
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`
  const totalSeconds = Math.max(1, Math.round(ms / 1000))
  if (totalSeconds < 60) return `${totalSeconds}秒`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分钟`
}

function rowDuration(row: ToolCallRow, now: number): number | undefined {
  if (row.durationMs != null) return row.durationMs
  if (row.startedAt == null) return undefined
  return Math.max(0, now - row.startedAt)
}

function renderDuration(row: ToolCallRow, now: number) {
  const durationMs = rowDuration(row, now)
  return durationMs == null ? null : (
    <span className="bubble-tool-call-duration">· {formatDuration(durationMs)}</span>
  )
}

function renderToolRow(row: ToolCallRow, now: number) {
  if (row.outcome === 'context_error') {
    // 静默处理失败：不展示红色图标，行尾以普通文字标注（与成功行同色）
    return (
      <div
        key={row.index}
        className="bubble-tool-call-line bubble-tool-call-line--failed"
      >
        <span className="bubble-tool-call-spacer" aria-hidden />
        <span>{row.text}</span>
        {renderDuration(row, now)}
        <span className="bubble-tool-call-failed-tag">调用失败</span>
      </div>
    )
  }

  const running = row.phase === 'running'
  return (
    <div
      key={row.index}
      className={`bubble-tool-call-line ${running ? 'a-flicker-opacity' : ''} bubble-tool-call-line--${row.phase}`}
    >
      {row.phase === 'done' ? (
        <CheckCircleIcon className="bubble-tool-call-icon" />
      ) : running ? (
        row.label.startsWith('编辑') ? (
          <EditIcon className="bubble-tool-call-icon" />
        ) : (
          <FileSearchIcon className="bubble-tool-call-icon" />
        )
      ) : (
        <ClockIcon className="bubble-tool-call-icon bubble-tool-call-icon--pending" />
      )}
      <span>{row.text}</span>
      {renderDuration(row, now)}
    </div>
  )
}

/** 工具调用叶子步骤，由外层连续操作组统一负责展开。 */
export default function ToolCallStatus(props: ToolCallStatusProps) {
  const rows = buildToolCallRows(props)
  const ticking = rows.some((row) => row.startedAt != null)
  const [now, setNow] = React.useState(() => performance.now())
  React.useEffect(() => {
    if (!ticking) return
    setNow(performance.now())
    const timer = window.setInterval(() => setNow(performance.now()), 500)
    return () => window.clearInterval(timer)
  }, [ticking])
  if (rows.length === 0) return null
  return (
    <div className="bubble-tool-call-details">
      {rows.map((row) => renderToolRow(row, now))}
    </div>
  )
}

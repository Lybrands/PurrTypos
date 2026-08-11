import {
  CheckCircleIcon,
  ClockIcon,
  CloseCircleIcon,
  EditIcon,
  FileSearchIcon,
} from '@/purr-components'
import type { ToolCallLabelOutcome } from '../../hooks/chat.types'
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
}

function renderToolRow(row: ToolCallRow) {
  if (row.outcome === 'context_error') {
    return (
      <div
        key={row.index}
        className="bubble-tool-call-line bubble-tool-call-line--error"
      >
        <CloseCircleIcon className="bubble-tool-call-icon" />
        <span>{row.text}</span>
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
    </div>
  )
}

/** 工具调用叶子步骤，由外层连续操作组统一负责展开。 */
export default function ToolCallStatus(props: ToolCallStatusProps) {
  const rows = buildToolCallRows(props)
  if (rows.length === 0) return null
  return (
    <div className="bubble-tool-call-details">
      {rows.map(renderToolRow)}
    </div>
  )
}

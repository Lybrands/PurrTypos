import { RollbackOutlined } from '@ant-design/icons'
import { Button, Spin, Tag, Tooltip } from 'antd'
import type { HistoryViewModel } from './SettingHistoryAdapter'

interface SettingHistoryItemProps {
  item: HistoryViewModel
  expanded: boolean
  detail: HistoryViewModel | null
  detailLoading: boolean
  restoring: boolean
  restoreTooltip?: string
  onToggle: () => void
  onRestore: () => void
}

const SOURCE_LABEL: Record<string, { color: string; text: string }> = {
  user: { color: 'blue', text: '手动编辑' },
  ai_tool: { color: 'purple', text: 'AI 修改' },
  ai_tool_edit: { color: 'purple', text: 'AI 修改' },
}

function renderSourceTag(source: string) {
  if (source.startsWith('rollback_of:')) return <Tag color="orange">回退</Tag>
  const metadata = SOURCE_LABEL[source]
  return metadata
    ? <Tag color={metadata.color}>{metadata.text}</Tag>
    : <Tag>{source || 'unknown'}</Tag>
}

function previewText(text: string, maxLength = 200): string {
  const trimmedText = (text || '').trim()
  if (!trimmedText) return '（空）'
  return trimmedText.length > maxLength
    ? `${trimmedText.slice(0, maxLength)}…`
    : trimmedText
}

export default function SettingHistoryItem({
  item,
  expanded,
  detail,
  detailLoading,
  restoring,
  restoreTooltip,
  onToggle,
  onRestore,
}: SettingHistoryItemProps) {
  const restoreButton = (
    <Button
      type="text"
      size="small"
      danger
      icon={<RollbackOutlined />}
      loading={restoring}
      onClick={onRestore}
    >
      回退到此版本
    </Button>
  )

  return (
    <div className="setting-history-item">
      <div className="setting-history-item-header">
        <div className="setting-history-item-meta">
          <span>#{item.id}</span>
          {renderSourceTag(item.source)}
          <span>接受 {item.acceptedSegments} / 拒绝 {item.rejectedSegments}</span>
        </div>
        <span className="setting-history-item-time">
          {item.createTime ? new Date(item.createTime).toLocaleString() : '—'}
        </span>
      </div>
      <div className="setting-history-item-preview">{previewText(item.previewContent)}</div>
      <div className="setting-history-item-actions">
        <Button type="text" size="small" onClick={onToggle}>
          {expanded ? '收起' : '查看完整内容'}
        </Button>
        {restoreTooltip ? (
          <Tooltip title={restoreTooltip}>{restoreButton}</Tooltip>
        ) : restoreButton}
      </div>
      {expanded && detailLoading && !detail ? <Spin size="small" /> : null}
      {expanded && detail?.id === item.id ? (
        <pre className="setting-history-item-full">{detail.beforeContent || '（空）'}</pre>
      ) : null}
    </div>
  )
}

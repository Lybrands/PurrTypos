import React from 'react'
import { Button, Card, Space, Tag } from 'antd'
import {
  CheckOutlined,
  CloseOutlined,
  EyeOutlined,
} from '@ant-design/icons'
import type { SettingDiffCardState } from '../../../../types'
import { useSettingDiff } from '../../../settingDiff/SettingDiffContext'
import { countByStatus } from '../../../diff/paragraphDiff'
import './SettingDiffCard.scss'

interface SettingDiffCardProps {
  card: SettingDiffCardState
}

export default function SettingDiffCard({ card }: SettingDiffCardProps) {
  const diff = useSettingDiff()
  const session = diff.getSession(card.sessionKey)
  const resolved = diff.getResolvedCard(card.sessionKey)
  const display = resolved || (card.status !== 'pending' ? card : null)

  const stats = session
    ? {
        ...countByStatus(session.profileOps),
        metaPending: session.metaOps.filter((op) => op.status === 'pending').length,
      }
    : null
  const pendingCount = stats ? stats.pending + stats.metaPending : 0
  const diffCount = stats ? stats.total + session!.metaOps.length : 0

  if (display && display.status !== 'pending') {
    return (
      <Card size="small" className="setting-diff-card setting-diff-card--resolved">
        <div className="setting-diff-card-title">
          {display.status === 'committed' ? '已应用设定修改' : '已拒绝设定修改'}
          <span className="setting-diff-card-subtitle"> · {display.title}</span>
        </div>
        {display.status === 'committed' ? (
          <div className="setting-diff-card-meta">
            接受 {display.acceptedSegments ?? 0} 段，拒绝 {display.rejectedSegments ?? 0} 段
          </div>
        ) : null}
      </Card>
    )
  }

  if (!session) {
    return (
      <Card size="small" className="setting-diff-card">
        <div className="setting-diff-card-title">设定修改 · {card.title}</div>
        <div className="setting-diff-card-meta">会话已结束</div>
      </Card>
    )
  }

  return (
    <Card size="small" className="setting-diff-card">
      <div className="setting-diff-card-header">
        <div>
          <div className="setting-diff-card-title">
            提议修改{session.kind === 'character' ? '人物' : '故事背景'}
            <span className="setting-diff-card-subtitle"> · {card.title}</span>
          </div>
          <Space size={4} wrap className="setting-diff-card-tags">
            {diffCount > 0 ? <Tag>{diffCount} 段差异</Tag> : null}
            {pendingCount > 0 ? <Tag color="warning">待处理 {pendingCount}</Tag> : null}
          </Space>
        </div>
      </div>
      <Space size="small" wrap className="setting-diff-card-actions">
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => diff.openPanelForSession(card.sessionKey)}
        >
          在设定面板审阅
        </Button>
        <Button
          size="small"
          icon={<CheckOutlined />}
          onClick={() => {
            diff.acceptAllPending(card.sessionKey)
            void diff.commit(card.sessionKey)
          }}
          disabled={session.computing}
        >
          全部接受
        </Button>
        <Button
          size="small"
          icon={<CloseOutlined />}
          onClick={() => diff.exitDiff(card.sessionKey)}
        >
          拒绝
        </Button>
      </Space>
    </Card>
  )
}

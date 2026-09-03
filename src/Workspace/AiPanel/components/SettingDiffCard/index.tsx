import { PurrButton, PurrCard, PurrSpace, PurrTag } from '@/purr-components'
import {
  CheckIcon,
  CloseIcon,
  EyeIcon,
} from '@/purr-components'
import type { SettingDiffCardState } from '../../../../types'
import { useSettingDiff } from '../../../settingDiff/SettingDiffContext'
import { countByStatus } from '../../../diff/paragraphDiff'
import './SettingDiffCard.scss'

interface SettingDiffCardProps {
  card: SettingDiffCardState
}

export default function SettingDiffCard({ card }: SettingDiffCardProps) {
  const diff = useSettingDiff()
  const candidate = diff.getSession(card.sessionKey)
  const session = candidate?.proposalId === card.proposalId
    ? candidate
    : undefined
  const resolved = diff.getResolvedCard(card.proposalId)
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
      <PurrCard size="small" className="setting-diff-card setting-diff-card--resolved">
        <div className="setting-diff-card-title">
          {display.status === 'committed' ? '已应用设定修改' : '已拒绝设定修改'}
          <span className="setting-diff-card-subtitle"> · {display.title}</span>
        </div>
        {display.status === 'committed' ? (
          <div className="setting-diff-card-meta">
            接受 {display.acceptedSegments ?? 0} 段，拒绝 {display.rejectedSegments ?? 0} 段
          </div>
        ) : null}
      </PurrCard>
    )
  }

  if (!session) {
    return (
      <PurrCard size="small" className="setting-diff-card">
        <div className="setting-diff-card-title">设定修改 · {card.title}</div>
        <div className="setting-diff-card-meta">会话已结束</div>
      </PurrCard>
    )
  }

  return (
    <PurrCard size="small" className="setting-diff-card">
      <div className="setting-diff-card-header">
        <div>
          <div className="setting-diff-card-title">
            提议修改{session.kind === 'character' ? '人物' : session.kind === 'entity' ? '世界设定' : '故事背景'}
            <span className="setting-diff-card-subtitle"> · {card.title}</span>
          </div>
          <PurrSpace size={4} wrap className="setting-diff-card-tags">
            {diffCount > 0 ? <PurrTag>{diffCount} 段差异</PurrTag> : null}
            {pendingCount > 0 ? <PurrTag color="warning">待处理 {pendingCount}</PurrTag> : null}
          </PurrSpace>
        </div>
      </div>
      <PurrSpace size="small" wrap className="setting-diff-card-actions">
        <PurrButton
          size="small"
          icon={<EyeIcon />}
          onClick={() => diff.openPanelForSession(card.sessionKey)}
        >
          在设定面板审阅
        </PurrButton>
        <PurrButton
          size="small"
          icon={<CheckIcon />}
          onClick={() => {
            diff.acceptAllPending(card.sessionKey)
            void diff.commit(card.sessionKey)
          }}
          disabled={session.computing || session.committing}
        >
          全部接受
        </PurrButton>
        <PurrButton
          size="small"
          icon={<CloseIcon />}
          onClick={() => void diff.exitDiff(card.sessionKey).catch(() => undefined)}
          disabled={session.committing}
        >
          拒绝
        </PurrButton>
      </PurrSpace>
    </PurrCard>
  )
}

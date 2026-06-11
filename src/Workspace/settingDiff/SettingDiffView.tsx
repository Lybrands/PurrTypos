import React from 'react'
import { Button, Dropdown, Input, Modal, Space, Tag, Tooltip } from 'antd'
import type { MenuProps } from 'antd'
import {
  CheckOutlined,
  CloseOutlined,
  DownOutlined,
  RollbackOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { useSettingDiff, type MetaDiffOp, type SettingDiffSession } from './SettingDiffContext'
import { countByStatus, type DiffOp } from '../diff/paragraphDiff'
import '../diff/diff.scss'
import './settingDiff.scss'

interface SettingDiffViewProps {
  sessionKey: string
  title?: string
  compact?: boolean
}

export default function SettingDiffView({ sessionKey, title, compact = false }: SettingDiffViewProps) {
  const diff = useSettingDiff()
  const session = diff.getSession(sessionKey)
  const [modal, modalCtx] = Modal.useModal()
  const [committing, setCommitting] = React.useState(false)

  if (!session) return null

  const profileStats = countByStatus(session.profileOps)
  const metaPending = session.metaOps.filter((op) => op.status === 'pending').length
  const metaAccepted = session.metaOps.filter((op) => op.status === 'accepted').length
  const metaRejected = session.metaOps.filter((op) => op.status === 'rejected').length
  const pendingTotal = profileStats.pending + metaPending
  const acceptedTotal = profileStats.accepted + metaAccepted
  const rejectedTotal = profileStats.rejected + metaRejected
  const diffCount = profileStats.total + session.metaOps.length
  const displayTitle = title || (
    session.kind === 'character'
      ? session.characterName || '人物设定'
      : '故事背景'
  )

  const handleAcceptAll = () => diff.acceptAllPending(sessionKey)
  const handleRejectAll = () => diff.rejectAllPending(sessionKey)
  const handleExit = () => {
    if (acceptedTotal > 0 || rejectedTotal > 0) {
      modal.confirm({
        title: '退出 diff 会话',
        content: '已处理的接受/拒绝将丢失，设定回到 diff 开始前的状态。继续？',
        okText: '退出',
        cancelText: '继续编辑',
        okButtonProps: { danger: true },
        onOk: () => diff.exitDiff(sessionKey),
      })
    } else {
      diff.exitDiff(sessionKey)
    }
  }
  const handleCommit = async () => {
    setCommitting(true)
    try {
      await diff.commit(sessionKey)
    } catch (e) {
      console.error('[SettingDiffView] commit failed', e)
    } finally {
      setCommitting(false)
    }
  }

  return (
    <div className={`setting-diff-view ${compact ? 'setting-diff-view--compact' : ''}`}>
      {modalCtx}
      <div className="diff-overlay-toolbar setting-diff-toolbar">
        <div className="diff-overlay-title">
          <span className="diff-badge">AI diff</span>
          <span className="diff-overlay-chapter">{displayTitle}</span>
          {diffCount > 0 ? <Tag>{diffCount} 段差异</Tag> : null}
          {pendingTotal > 0 ? <Tag color="warning">待处理 {pendingTotal}</Tag> : null}
          {acceptedTotal > 0 ? <Tag color="success">接受 {acceptedTotal}</Tag> : null}
          {rejectedTotal > 0 ? <Tag>拒绝 {rejectedTotal}</Tag> : null}
        </div>
        <Space size="small" wrap>
          <Button size="small" icon={<CheckOutlined />} onClick={handleAcceptAll} disabled={pendingTotal === 0}>
            全部接受
          </Button>
          <Button size="small" icon={<CloseOutlined />} onClick={handleRejectAll} disabled={pendingTotal === 0}>
            全部拒绝
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<SaveOutlined />}
            loading={committing}
            disabled={session.computing}
            onClick={handleCommit}
          >
            {session.computing
              ? '计算中…'
              : pendingTotal === 0
                ? '应用并保存'
                : `应用（剩 ${pendingTotal} 段保留原文）`}
          </Button>
          <Button size="small" icon={<RollbackOutlined />} danger onClick={handleExit}>
            退出
          </Button>
        </Space>
      </div>

      <div className="setting-diff-body">
        {session.computing ? (
          <div className="diff-overlay-empty">
            <span className="diff-overlay-spinner" /> 正在后台计算段落差异…
          </div>
        ) : (
          <>
            {session.metaOps.map((op) => (
              <MetaDiffRow
                key={`meta-${op.field}-${op.index}`}
                op={op}
                onAccept={() => diff.setMetaOpStatus(sessionKey, op.index, 'accepted')}
                onReject={() => diff.setMetaOpStatus(sessionKey, op.index, 'rejected')}
                onPending={() => diff.setMetaOpStatus(sessionKey, op.index, 'pending')}
              />
            ))}
            {profileStats.total === 0 && session.metaOps.length === 0 ? (
              <div className="diff-overlay-empty">AI 输出与当前设定完全一致，没有可应用的差异。</div>
            ) : (
              <div className="diff-overlay-list">
                {session.profileOps.map((op) => (
                  <DiffParagraphRow
                    key={op.index}
                    op={op}
                    onAccept={() => diff.setOpStatus(sessionKey, op.index, 'accepted')}
                    onReject={(reason) => diff.setOpStatus(sessionKey, op.index, 'rejected', reason)}
                    onPending={() => diff.setOpStatus(sessionKey, op.index, 'pending')}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function MetaDiffRow({
  op,
  onAccept,
  onReject,
  onPending,
}: {
  op: MetaDiffOp
  onAccept: () => void
  onReject: () => void
  onPending: () => void
}) {
  const label = op.field === 'name' ? '姓名' : '标签'
  const cls = ['diff-row', 'diff-row-replace', `diff-status-${op.status}`].join(' ')
  return (
    <div className={`setting-diff-meta-row ${cls}`}>
      <div className="diff-row-marker">↻</div>
      <div className="diff-row-content">
        <div className="setting-diff-meta-label">{label}</div>
        <p className="diff-text-before">{op.before || '（空）'}</p>
        <p className="diff-text-after">{op.after || '（空）'}</p>
      </div>
      <div className="diff-row-actions">
        {op.status === 'pending' ? (
          <>
            <Tooltip title="接受">
              <Button type="text" size="small" icon={<CheckOutlined />} onClick={onAccept} />
            </Tooltip>
            <Tooltip title="拒绝">
              <Button type="text" size="small" icon={<CloseOutlined />} onClick={onReject} />
            </Tooltip>
          </>
        ) : (
          <Button type="text" size="small" onClick={onPending}>
            {op.status === 'accepted' ? '已接受' : '已拒绝'}
          </Button>
        )}
      </div>
    </div>
  )
}

function DiffParagraphRow({
  op,
  onAccept,
  onReject,
  onPending,
}: {
  op: DiffOp
  onAccept: () => void
  onReject: (reason?: string) => void
  onPending: () => void
}) {
  const [reasonModalOpen, setReasonModalOpen] = React.useState(false)
  const [reasonDraft, setReasonDraft] = React.useState('')

  if (op.kind === 'equal') {
    return (
      <div className="diff-row diff-row-equal">
        <div className="diff-row-marker">＝</div>
        <div className="diff-row-content">
          <p className="diff-text-equal">{op.before || <span className="diff-empty">（空行）</span>}</p>
        </div>
      </div>
    )
  }

  const cls = ['diff-row', `diff-row-${op.kind}`, `diff-status-${op.status}`].join(' ')
  const rejectMenuItems: MenuProps['items'] = [
    { key: 'discard', label: '直接放弃（保留原文）', onClick: () => onReject(undefined) },
    {
      key: 'reason',
      label: '说明理由…',
      onClick: () => {
        setReasonDraft(op.rejectReason ?? '')
        setReasonModalOpen(true)
      },
    },
  ]

  return (
    <div className={cls}>
      <div className="diff-row-marker">
        {op.kind === 'insert' ? '＋' : op.kind === 'delete' ? '－' : '↻'}
      </div>
      <div className="diff-row-content">
        {(op.kind === 'delete' || op.kind === 'replace') && (
          <p className="diff-text-before">{op.before || <span className="diff-empty">（空段）</span>}</p>
        )}
        {(op.kind === 'insert' || op.kind === 'replace') && (
          <p className="diff-text-after">{op.after || <span className="diff-empty">（空段）</span>}</p>
        )}
      </div>
      <div className="diff-row-actions">
        {op.status === 'pending' ? (
          <>
            <Tooltip title="接受此段">
              <Button type="text" size="small" icon={<CheckOutlined />} onClick={onAccept} />
            </Tooltip>
            <Dropdown.Button
              menu={{ items: rejectMenuItems }}
              size="small"
              type="text"
              trigger={['click']}
              icon={<DownOutlined />}
              onClick={() => onReject(undefined)}
            >
              <CloseOutlined />
            </Dropdown.Button>
          </>
        ) : (
          <Button type="text" size="small" onClick={onPending}>
            {op.status === 'accepted' ? '已接受' : '已拒绝'}
          </Button>
        )}
      </div>
      <Modal
        title="说明拒绝理由"
        open={reasonModalOpen}
        onCancel={() => setReasonModalOpen(false)}
        onOk={() => {
          onReject(reasonDraft.trim() || undefined)
          setReasonModalOpen(false)
        }}
        okText="提交并拒绝"
        cancelText="取消"
        destroyOnHidden
      >
        <Input.TextArea
          autoFocus
          rows={3}
          value={reasonDraft}
          onChange={(e) => setReasonDraft(e.target.value)}
          placeholder="例如：与人物性格不符 / 偏离世界观设定…"
        />
      </Modal>
    </div>
  )
}

export function useActiveSettingDiffSession(
  kind: 'character' | 'background',
  entityId: number | string | null | undefined,
): SettingDiffSession | undefined {
  const diff = useSettingDiff()
  if (entityId == null) return undefined
  const key = kind === 'character'
    ? `character:${entityId}`
    : `background:${entityId}`
  return diff.getSession(key)
}

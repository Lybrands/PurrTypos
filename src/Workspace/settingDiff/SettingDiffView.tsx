import React from 'react'
import { PurrButton, PurrDropdown, PurrInput, PurrModal, PurrSpace, PurrTag, PurrTooltip, usePurrConfirm, type PurrDropdownItem } from '@/purr-components'
import {
  CheckIcon,
  CloseIcon,
  ChevronDownIcon,
  SaveIcon,
} from '@/purr-components'
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
  const confirm = usePurrConfirm()
  const committing = Boolean(session?.committing)

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
      : session.kind === 'entity'
        ? session.entityName || '世界设定'
        : '故事背景'
  )

  const handleAcceptAll = () => diff.acceptAllPending(sessionKey)
  const handleRejectAll = () => diff.rejectAllPending(sessionKey)
  const handleExit = () => {
    if (acceptedTotal > 0 || rejectedTotal > 0) {
      void confirm({
        title: '退出 diff 会话',
        content: '已处理的接受/拒绝将丢失，设定回到 diff 开始前的状态。继续？',
        confirmText: '退出',
        cancelText: '继续编辑',
        confirmVariant: 'danger',
      }).then((result) => {
        if (result === 'confirm') void diff.exitDiff(sessionKey).catch(() => undefined)
      })
    } else {
      void diff.exitDiff(sessionKey).catch(() => undefined)
    }
  }
  const handleCommit = async () => {
    try {
      await diff.commit(sessionKey)
    } catch (e) {
      console.error('[SettingDiffView] commit failed', e)
    }
  }

  return (
    <div className={`setting-diff-view ${compact ? 'setting-diff-view--compact' : ''}`}>
      <div className="diff-overlay-toolbar setting-diff-toolbar">
        <div className="diff-overlay-title">
          <span className="diff-badge">AI diff</span>
          <span className="diff-overlay-chapter">{displayTitle}</span>
          {diffCount > 0 ? <PurrTag>{diffCount} 段差异</PurrTag> : null}
          {pendingTotal > 0 ? <PurrTag color="warning">待处理 {pendingTotal}</PurrTag> : null}
          {acceptedTotal > 0 ? <PurrTag color="success">接受 {acceptedTotal}</PurrTag> : null}
          {rejectedTotal > 0 ? <PurrTag>拒绝 {rejectedTotal}</PurrTag> : null}
        </div>
        <PurrSpace size="small" wrap>
          <PurrButton size="small" icon={<CheckIcon />} onClick={handleAcceptAll} disabled={committing || pendingTotal === 0}>
            全部接受
          </PurrButton>
          <PurrButton size="small" icon={<CloseIcon />} onClick={handleRejectAll} disabled={committing || pendingTotal === 0}>
            全部拒绝
          </PurrButton>
          <PurrButton
            size="small"
            type="primary"
            icon={<SaveIcon />}
            loading={committing}
            disabled={session.computing || committing}
            onClick={handleCommit}
          >
            {session.computing
              ? '计算中…'
              : pendingTotal === 0
                ? '应用并保存'
                : `应用（剩 ${pendingTotal} 段保留原文）`}
          </PurrButton>
          <PurrButton size="small" icon={<CloseIcon />} danger onClick={handleExit} disabled={committing}>
            退出
          </PurrButton>
        </PurrSpace>
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
                disabled={committing}
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
                    disabled={committing}
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
  disabled,
}: {
  op: MetaDiffOp
  onAccept: () => void
  onReject: () => void
  onPending: () => void
  disabled?: boolean
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
            <PurrTooltip title="接受">
              <PurrButton type="text" size="small" icon={<CheckIcon />} onClick={onAccept} disabled={disabled} />
            </PurrTooltip>
            <PurrTooltip title="拒绝">
              <PurrButton type="text" size="small" icon={<CloseIcon />} onClick={onReject} disabled={disabled} />
            </PurrTooltip>
          </>
        ) : (
          <PurrButton type="text" size="small" onClick={onPending} disabled={disabled}>
            {op.status === 'accepted' ? '已接受' : '已拒绝'}
          </PurrButton>
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
  disabled,
}: {
  op: DiffOp
  onAccept: () => void
  onReject: (reason?: string) => void
  onPending: () => void
  disabled?: boolean
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
  const rejectMenuItems: PurrDropdownItem[] = [
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
            <PurrTooltip title="接受此段">
              <PurrButton type="text" size="small" icon={<CheckIcon />} onClick={onAccept} disabled={disabled} />
            </PurrTooltip>
            <PurrDropdown.Button
              menu={{ items: rejectMenuItems }}
              size="small"
              type="text"
              trigger={['click']}
              icon={<ChevronDownIcon />}
              onClick={() => onReject(undefined)}
              disabled={disabled}
            >
              <CloseIcon />
            </PurrDropdown.Button>
          </>
        ) : (
          <PurrButton type="text" size="small" onClick={onPending} disabled={disabled}>
            {op.status === 'accepted' ? '已接受' : '已拒绝'}
          </PurrButton>
        )}
      </div>
      <PurrModal
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
        <PurrInput.TextArea
          autoFocus
          rows={3}
          value={reasonDraft}
          onChange={(e) => setReasonDraft(e.target.value)}
          placeholder="例如：与人物性格不符 / 偏离世界观设定…"
        />
      </PurrModal>
    </div>
  )
}

export function useActiveSettingDiffSession(
  kind: 'character' | 'background' | 'entity',
  entityId: number | string | null | undefined,
): SettingDiffSession | undefined {
  const diff = useSettingDiff()
  if (entityId == null) return undefined
  return diff.getSession(`${kind}:${entityId}`)
}

import React from 'react'
import { Button, Dropdown, Input, Modal, Space, Tag, Tooltip, useConfirm, type DropdownItem } from '../../ui'
import {
  CheckOutlined,
  CloseOutlined,
  DownOutlined,
  RollbackOutlined,
  SaveOutlined,
} from '../../ui'
import type { EntityId } from '../../types'
import { useDiff } from './DiffContext'
import { countByStatus, type DiffOp } from './paragraphDiff'
import './diff.scss'

interface DiffOverlayProps {
  chapterId: EntityId
  chapterTitle: string
}

export default function DiffOverlay({ chapterId, chapterTitle }: DiffOverlayProps) {
  const diff = useDiff()
  const session = diff.getSession(chapterId)
  const confirm = useConfirm()
  const [committing, setCommitting] = React.useState(false)

  if (!session) return null

  const stats = countByStatus(session.ops)
  const allHandled = stats.pending === 0
  const noPendingOps = stats.total === 0 // diff 没有任何待操作段（before == after）

  const handleAcceptAll = () => {
    diff.acceptAllPending(chapterId)
  }
  const handleRejectAll = () => {
    diff.rejectAllPending(chapterId)
  }
  const handleExit = () => {
    if (stats.accepted > 0 || stats.rejected > 0) {
      void confirm({
        title: '退出 diff 会话',
        content: '已处理的接受/拒绝将丢失，正文回到 diff 开始前的状态。继续？',
        confirmText: '退出',
        cancelText: '继续编辑',
        confirmVariant: 'danger',
      }).then((result) => {
        if (result === 'confirm') diff.exitDiff(chapterId)
      })
    } else {
      diff.exitDiff(chapterId)
    }
  }
  const handleCommit = async () => {
    setCommitting(true)
    try {
      await diff.commit(chapterId)
    } catch (e) {
      // commit 内部已经 throw；此处用 console 兜底
      console.error('[DiffOverlay] commit failed', e)
    } finally {
      setCommitting(false)
    }
  }

  return (
    <div className="diff-overlay">
      {/* ── 顶部 toolbar ──────────────────────────────────────── */}
      <div className="diff-overlay-toolbar">
        <div className="diff-overlay-title">
          <span className="diff-badge">AI diff</span>
          <span className="diff-overlay-chapter">{chapterTitle || '当前章节'}</span>
          <Tag color="blue">{stats.total} 段差异</Tag>
          {stats.pending > 0 && <Tag color="warning">待处理 {stats.pending}</Tag>}
          {stats.accepted > 0 && <Tag color="success">接受 {stats.accepted}</Tag>}
          {stats.rejected > 0 && <Tag color="default">拒绝 {stats.rejected}</Tag>}
        </div>
        <Space size="small">
          <Tooltip title="把所有未处理段标记为「接受」（你也可以单段操作）">
            <Button
              size="small"
              icon={<CheckOutlined />}
              onClick={handleAcceptAll}
              disabled={stats.pending === 0}
            >
              全部接受
            </Button>
          </Tooltip>
          <Tooltip title="把所有未处理段标记为「拒绝」">
            <Button
              size="small"
              icon={<CloseOutlined />}
              onClick={handleRejectAll}
              disabled={stats.pending === 0}
            >
              全部拒绝
            </Button>
          </Tooltip>
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
              : allHandled
                ? '应用并保存'
                : `应用（剩 ${stats.pending} 段保留原文）`}
          </Button>
          <Tooltip title="放弃整次 diff，正文不变">
            <Button
              size="small"
              icon={<RollbackOutlined />}
              danger
              onClick={handleExit}
            >
              退出
            </Button>
          </Tooltip>
        </Space>
      </div>

      {/* ── 段落对比列表 ───────────────────────────────────────── */}
      <div className="diff-overlay-body">
        {session.computing ? (
          <div className="diff-overlay-empty">
            <span className="diff-overlay-spinner" /> 正在后台计算段落差异…
          </div>
        ) : noPendingOps ? (
          <div className="diff-overlay-empty">
            AI 输出与当前正文完全一致，没有可应用的差异。
          </div>
        ) : (
          <div className="diff-overlay-list">
            {session.ops.map((op) => (
              <DiffParagraphRow
                key={op.index}
                op={op}
                onAccept={() => diff.setOpStatus(chapterId, op.index, 'accepted')}
                onReject={(reason) => diff.setOpStatus(chapterId, op.index, 'rejected', reason)}
                onPending={() => diff.setOpStatus(chapterId, op.index, 'pending')}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

interface DiffParagraphRowProps {
  op: DiffOp
  onAccept: () => void
  /** reason 留空表示直接放弃；非空表示带理由 */
  onReject: (reason?: string) => void
  onPending: () => void
}

function DiffParagraphRow({ op, onAccept, onReject, onPending }: DiffParagraphRowProps) {
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

  const cls = [
    'diff-row',
    `diff-row-${op.kind}`,
    `diff-status-${op.status}`,
  ].join(' ')

  const rejectMenuItems: DropdownItem[] = [
    {
      key: 'discard',
      label: '直接放弃（保留原文）',
      onClick: () => onReject(undefined),
    },
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
          <p className="diff-text-before">
            {op.before || <span className="diff-empty">（空段）</span>}
          </p>
        )}
        {(op.kind === 'insert' || op.kind === 'replace') && (
          <p className="diff-text-after">
            {op.after || <span className="diff-empty">（空段）</span>}
          </p>
        )}
        {op.status === 'rejected' && op.rejectReason ? (
          <div className="diff-row-reject-reason">
            <span className="diff-row-reject-reason-label">理由</span>
            <span>{op.rejectReason}</span>
          </div>
        ) : null}
      </div>
      <div className="diff-row-actions">
        {op.status === 'pending' ? (
          <>
            <Tooltip title="接受此段">
              <Button
                type="text"
                size="small"
                icon={<CheckOutlined />}
                onClick={onAccept}
                className="diff-action-accept"
              />
            </Tooltip>
            <Dropdown.Button
              menu={{ items: rejectMenuItems }}
              size="small"
              type="text"
              trigger={['click']}
              icon={<DownOutlined />}
              onClick={() => onReject(undefined)}
              className="diff-action-reject-dropdown"
            >
              <CloseOutlined />
            </Dropdown.Button>
          </>
        ) : (
          <Tooltip title={`已${op.status === 'accepted' ? '接受' : '拒绝'} · 点击撤销`}>
            <Button
              type="text"
              size="small"
              onClick={onPending}
              className={`diff-action-revert diff-action-revert-${op.status}`}
            >
              {op.status === 'accepted' ? (
                <><CheckOutlined /> 接受</>
              ) : (
                <><CloseOutlined /> 拒绝</>
              )}
            </Button>
          </Tooltip>
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
        <p style={{ marginBottom: 8, color: 'var(--text-muted)' }}>
          描述这段为什么不合适（一句话即可），将来可作为再生成时给 AI 的反馈。
        </p>
        <Input.TextArea
          autoFocus
          rows={3}
          value={reasonDraft}
          onChange={(e) => setReasonDraft(e.target.value)}
          placeholder="例如：太啰嗦 / 与人物性格不符 / 偏离主线…"
        />
      </Modal>
    </div>
  )
}

import React from 'react'
import {
  AlertCircleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  PurrAlert,
  PurrButton,
  PurrCheckbox,
  PurrDropdown,
  PurrInput,
  PurrModal,
} from '@/purr-components'
import type {
  ScreenplayV2ReviewFindingStatus,
  ScreenplayV2ReviewState,
} from '../types'
import {
  batchDecisionLabel,
  finalizationDisabledReason,
  pendingReviewFindingIds,
  reviewPrimaryAction,
  reviewRequiresRerun,
  reviewVersionLabel,
  type ReviewBatchDecisionStatus,
} from './reviewAdjudicationModel'

interface PendingDecision {
  issueIds: string[]
  status: ScreenplayV2ReviewFindingStatus
}

interface ReviewAdjudicationPanelProps {
  modal?: boolean
  review: ScreenplayV2ReviewState
  readOnly: boolean
  busy: boolean
  onDecide: (
    issueIds: string[],
    status: ScreenplayV2ReviewFindingStatus,
    note: string,
  ) => Promise<boolean>
  onStartReview: () => void
  onStartRevision: () => void
  onFinalize: () => void
}

const STATUS_LABELS: Record<ScreenplayV2ReviewFindingStatus, string> = {
  pending: '待处理',
  planned: '待修订',
  resolved: '已解决',
  dismissed: '不成立',
  riskAccepted: '接受风险',
}

const SEVERITY_LABELS = {
  critical: '严重',
  major: '主要',
  minor: '次要',
} as const

const RECOMMENDATION_LABELS = {
  ready: 'Agent 建议可以定稿',
  revise: 'Agent 建议修订后再定稿',
  major_rework: 'Agent 建议进行较大修订',
} as const

const DECISION_STATUSES: ScreenplayV2ReviewFindingStatus[] = [
  'planned',
  'resolved',
  'dismissed',
  'riskAccepted',
  'pending',
]

const BATCH_STATUSES: ReviewBatchDecisionStatus[] = [
  'planned',
  'resolved',
  'dismissed',
  'riskAccepted',
]

export default function ReviewAdjudicationPanel({
  modal = false,
  review,
  readOnly,
  busy,
  onDecide,
  onStartReview,
  onStartRevision,
  onFinalize,
}: ReviewAdjudicationPanelProps) {
  const [selectedIds, setSelectedIds] = React.useState<string[]>([])
  const [pendingDecision, setPendingDecision] = React.useState<PendingDecision | null>(null)
  const [decisionNote, setDecisionNote] = React.useState('')

  React.useEffect(() => {
    const currentIds = new Set(review.findings.map((finding) => finding.id))
    setSelectedIds((current) => current.filter((id) => currentIds.has(id)))
  }, [review.findings])

  const pendingIds = pendingReviewFindingIds(review.findings)
  const allPendingSelected = pendingIds.length > 0
    && pendingIds.every((id) => selectedIds.includes(id))
  const selectedSet = new Set(selectedIds)
  const action = reviewPrimaryAction(review)
  const finalizeDisabledReason = finalizationDisabledReason(review)
  const reviewInvalid = reviewRequiresRerun(review)
  const versionLabel = reviewVersionLabel(review.reviewRevisionId)

  const openDecision = (
    issueIds: string[],
    status: ScreenplayV2ReviewFindingStatus,
  ) => {
    if (readOnly || busy || issueIds.length === 0) return
    setPendingDecision({ issueIds, status })
    setDecisionNote('')
  }

  const submitDecision = async () => {
    if (!pendingDecision) return
    const applied = await onDecide(
      pendingDecision.issueIds,
      pendingDecision.status,
      decisionNote.trim(),
    )
    if (!applied) return
    setSelectedIds((current) => current.filter(
      (id) => !pendingDecision.issueIds.includes(id),
    ))
    setPendingDecision(null)
    setDecisionNote('')
  }

  return (
    <section
      className={`screenplay-review-adjudication ${modal ? 'is-modal' : ''}`}
      aria-label={modal ? '审阅与定稿' : undefined}
      aria-labelledby={modal ? undefined : 'screenplay-review-title'}
    >
      <header className="screenplay-review-adjudication__header">
        <div>
          {!modal && (
            <>
              <span className="screenplay-source-eyebrow">REVIEW &amp; FINALIZE</span>
              <h2 id="screenplay-review-title">审阅与定稿</h2>
            </>
          )}
          <p>
            {review.recommendation
              ? RECOMMENDATION_LABELS[review.recommendation]
              : '等待生成当前剧本的审阅报告'}
            {versionLabel ? ` · ${versionLabel}` : ''}
          </p>
        </div>
        <div className="screenplay-review-adjudication__summary" aria-label="审阅处理进度">
          <span>待处理 <strong>{review.counts.pending}</strong></span>
          <span>待修订 <strong>{review.counts.planned}</strong></span>
          <span>已解决 <strong>{review.counts.resolved}</strong></span>
          <span>不成立 <strong>{review.counts.dismissed}</strong></span>
          <span>接受风险 <strong>{review.counts.riskAccepted}</strong></span>
        </div>
      </header>

      {review.hardChecks.map((check) => (
        <PurrAlert
          key={check.code}
          type="error"
          showIcon
          message="当前不能定稿"
          description={check.message}
        />
      ))}

      {review.findings.length > 0 ? (
        <>
          <div className="screenplay-review-adjudication__toolbar">
            <PurrCheckbox
              checked={allPendingSelected}
              indeterminate={!allPendingSelected && pendingIds.some((id) => selectedSet.has(id))}
              disabled={readOnly || busy || pendingIds.length === 0}
              onChange={(event) => {
                if (event.target.checked) {
                  setSelectedIds((current) => [...new Set([...current, ...pendingIds])])
                } else {
                  setSelectedIds((current) => current.filter((id) => !pendingIds.includes(id)))
                }
              }}
            >
              选择全部待处理项
            </PurrCheckbox>
            <PurrDropdown
              trigger={['click']}
              placement="bottomRight"
              disabled={readOnly || busy || selectedIds.length === 0}
              menu={{
                items: BATCH_STATUSES.map((status) => ({
                  key: status,
                  label: batchDecisionLabel(status, selectedIds.length),
                  onClick: () => openDecision(selectedIds, status),
                })),
              }}
            >
              <PurrButton
                size="small"
                icon={<ChevronDownIcon />}
                disabled={readOnly || busy || selectedIds.length === 0}
              >
                批量处理{selectedIds.length > 0 ? `（${selectedIds.length}）` : ''}
              </PurrButton>
            </PurrDropdown>
          </div>

          <div className="screenplay-review-adjudication__findings">
            {review.findings.map((finding) => (
              <article
                key={finding.id}
                className={`screenplay-review-finding is-${finding.status}`}
              >
                <PurrCheckbox
                  checked={selectedSet.has(finding.id)}
                  disabled={readOnly || busy}
                  onChange={(event) => setSelectedIds((current) => (
                    event.target.checked
                      ? [...new Set([...current, finding.id])]
                      : current.filter((id) => id !== finding.id)
                  ))}
                />
                <div className="screenplay-review-finding__content">
                  <div className="screenplay-review-finding__meta">
                    <span className={`is-${finding.severity}`}>
                      {SEVERITY_LABELS[finding.severity]}
                    </span>
                    <span>{finding.id}</span>
                    {finding.sceneIds.length > 0 && (
                      <span>涉及 {finding.sceneIds.length} 个场景</span>
                    )}
                  </div>
                  <p>{finding.description}</p>
                  {finding.note && (
                    <div className="screenplay-review-finding__note">
                      处理说明：{finding.note}
                    </div>
                  )}
                </div>
                <PurrDropdown
                  trigger={['click']}
                  placement="bottomRight"
                  disabled={readOnly || busy}
                  menu={{
                    items: DECISION_STATUSES.map((status) => ({
                      key: status,
                      label: status === 'pending' ? '重新设为待处理' : STATUS_LABELS[status],
                      disabled: finding.status === status,
                      onClick: () => openDecision([finding.id], status),
                    })),
                  }}
                >
                  <PurrButton
                    size="small"
                    disabled={readOnly || busy}
                    icon={<ChevronDownIcon />}
                  >
                    {STATUS_LABELS[finding.status]}
                  </PurrButton>
                </PurrDropdown>
              </article>
            ))}
          </div>
        </>
      ) : reviewInvalid ? (
        <div className="screenplay-review-adjudication__empty is-failed">
          <AlertCircleIcon />
          <div>
            <strong>当前审阅报告不可用于定稿</strong>
            <span>{review.hardChecks[0]?.message || '请基于当前剧本正文重新审阅。'}</span>
          </div>
        </div>
      ) : review.reviewRevisionId ? (
        <div className="screenplay-review-adjudication__empty">
          <CheckCircleIcon />
          <div>
            <strong>Agent 没有提出审阅意见</strong>
            <span>仍需由你确认当前版本是否定稿。</span>
          </div>
        </div>
      ) : null}

      <footer className="screenplay-review-adjudication__footer">
        <div>
          {action.kind === 'processFindings' && <AlertCircleIcon />}
          {action.kind === 'completed' && <CheckCircleIcon />}
          <span>
            {action.kind === 'completed'
              ? review.completionSource === 'legacyAgentVerdict'
                ? '该项目由旧版审阅流程完成'
                : '用户已经确认当前版本定稿'
              : finalizeDisabledReason || '所有意见已处理，可以确认定稿'}
          </span>
        </div>
        {action.kind === 'startRevision' && (
          <PurrButton
            type="primary"
            disabled={readOnly || busy}
            onClick={onStartRevision}
          >
            开始修订
          </PurrButton>
        )}
        {action.kind === 'startReview' && (
          <PurrButton
            type="primary"
            disabled={readOnly || busy}
            onClick={onStartReview}
          >
            {action.label}
          </PurrButton>
        )}
        {action.kind === 'finalize' && (
          <PurrButton
            type="primary"
            disabled={readOnly || busy || Boolean(finalizeDisabledReason)}
            onClick={onFinalize}
          >
            确认定稿
          </PurrButton>
        )}
      </footer>

      <PurrModal
        open={pendingDecision != null}
        title={pendingDecision
          ? pendingDecision.status === 'pending'
            ? `将 ${pendingDecision.issueIds.length} 条意见重新设为待处理`
            : batchDecisionLabel(
                pendingDecision.status,
                pendingDecision.issueIds.length,
              )
          : '处理审阅意见'}
        width={480}
        destroyOnHidden
        confirmLoading={busy}
        okText="确认处理"
        onCancel={() => {
          if (busy) return
          setPendingDecision(null)
          setDecisionNote('')
        }}
        onOk={() => void submitDecision()}
      >
        <div className="screenplay-review-decision-modal">
          <p>
            这项操作会记录为用户裁决，不会修改 Agent 的原始审阅报告。
          </p>
          <label>
            <span>处理说明（可选）</span>
            <PurrInput.TextArea
              value={decisionNote}
              maxLength={2000}
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder="说明为什么作出这个决定"
              onChange={(event) => setDecisionNote(event.target.value)}
            />
          </label>
        </div>
      </PurrModal>
    </section>
  )
}

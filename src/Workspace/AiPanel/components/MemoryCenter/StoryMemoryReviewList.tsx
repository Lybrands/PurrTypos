import React from 'react'
import { Button, Empty, Radio, Select, Space, Spin, Switch, Tag, toast } from '../../../../ui'
import type {
  EntityId,
  StoryMemoryEvolutionDecision,
  StoryMemoryEvolutionReview,
} from '../../../../types'

interface StoryMemoryReviewListProps {
  bookId: EntityId | null
}

type Resolution = 'accepted' | 'rejected'
type ReviewStatus = StoryMemoryEvolutionReview['status']

const CLASSIFICATION_LABEL: Record<StoryMemoryEvolutionDecision['classification'], string> = {
  addition: '新增',
  update: '更新',
  duplicate: '重复',
  conflict: '冲突',
  supersession: '替代',
}

const KIND_LABEL: Record<StoryMemoryEvolutionDecision['kind'], string> = {
  character_state: '人物状态',
  relationship_state: '人物关系',
  world_fact: '世界事实',
  timeline_event: '时间线事件',
  plot_thread: '剧情线',
}

const RISK_LABEL: Record<StoryMemoryEvolutionDecision['risk'], string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}

const STATUS_OPTIONS: Array<{ value: ReviewStatus; label: string }> = [
  { value: 'open', label: '待处理' },
  { value: 'resolved', label: '已处理' },
  { value: 'stale', label: '已失效' },
]

function suggestedResolutions(review: StoryMemoryEvolutionReview): Record<string, Resolution> {
  const result: Record<string, Resolution> = {}
  review.decisions.forEach((decision) => {
    if (decision.recommendation === 'apply') result[decision.target_key] = 'accepted'
    if (decision.recommendation === 'reject') result[decision.target_key] = 'rejected'
  })
  return result
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export default function StoryMemoryReviewList({ bookId }: StoryMemoryReviewListProps) {
  const [status, setStatus] = React.useState<ReviewStatus>('open')
  const [reviews, setReviews] = React.useState<StoryMemoryEvolutionReview[]>([])
  const [loading, setLoading] = React.useState(false)
  const [submittingId, setSubmittingId] = React.useState<string | null>(null)
  const [resolutions, setResolutions] = React.useState<Record<string, Record<string, Resolution>>>({})
  const [analysisEnabled, setAnalysisEnabled] = React.useState(false)
  const [autoApplyEnabled, setAutoApplyEnabled] = React.useState(false)
  const [minConfidence, setMinConfidence] = React.useState(0.95)
  const [settingsSaving, setSettingsSaving] = React.useState(false)

  const load = React.useCallback(async () => {
    if (bookId == null) {
      setReviews([])
      return
    }
    setLoading(true)
    const res = await window.electronAPI.listStoryMemoryEvolutionReviews({
      bookId,
      statuses: [status],
    })
    setLoading(false)
    if (!res.success || !Array.isArray(res.data)) {
      setReviews([])
      toast.error(res.error || '读取故事演化审查失败')
      return
    }
    setReviews(res.data)
    setResolutions((previous) => {
      const next = { ...previous }
      res.data.forEach((review) => {
        if (!next[review.delta_id]) next[review.delta_id] = suggestedResolutions(review)
      })
      return next
    })
  }, [bookId, status])

  React.useEffect(() => {
    void load()
  }, [load])

  React.useEffect(() => {
    let cancelled = false
    window.electronAPI.getSettings().then((res) => {
      if (cancelled || !res.success) return
      setAnalysisEnabled(!!res.data?.story_memory_analysis_enabled)
      setAutoApplyEnabled(!!res.data?.story_memory_auto_apply_enabled)
      const confidence = Number(res.data?.story_memory_auto_apply_min_confidence)
      setMinConfidence(Number.isFinite(confidence) ? confidence : 0.95)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const saveSettings = React.useCallback(async (patch: {
    story_memory_analysis_enabled?: boolean
    story_memory_auto_apply_enabled?: boolean
    story_memory_auto_apply_min_confidence?: number
  }) => {
    setSettingsSaving(true)
    const res = await window.electronAPI.setSettings(patch)
    setSettingsSaving(false)
    if (!res.success) toast.error(res.error || '保存故事演化设置失败')
    return res.success
  }, [])

  const resolveReview = React.useCallback(async (review: StoryMemoryEvolutionReview) => {
    const selected = resolutions[review.delta_id] || {}
    const complete = review.decisions.every((decision) => !!selected[decision.target_key])
    if (!complete) {
      toast.warning('请先处理所有需要人工判断的候选')
      return
    }
    setSubmittingId(review.delta_id)
    const res = await window.electronAPI.resolveStoryMemoryEvolutionReview({
      deltaId: review.delta_id,
      resolutions: selected,
    })
    setSubmittingId(null)
    if (!res.success) {
      toast.error(res.error || '提交故事演化决议失败')
      await load()
      return
    }
    toast.success(res.data?.applied_delta_id ? '已应用接受的故事设定' : '候选已全部拒绝')
    await load()
  }, [load, resolutions])

  const updateResolution = React.useCallback((
    deltaId: string,
    targetKey: string,
    value: Resolution,
  ) => {
    setResolutions((previous) => ({
      ...previous,
      [deltaId]: {
        ...(previous[deltaId] || {}),
        [targetKey]: value,
      },
    }))
  }, [])

  const renderDecision = (review: StoryMemoryEvolutionReview, decision: StoryMemoryEvolutionDecision) => {
    const selected = resolutions[review.delta_id]?.[decision.target_key]
    return (
      <div className="story-memory-decision" key={decision.target_key}>
        <div className="story-memory-decision-header">
          <Space size={6} wrap>
            <Tag>{KIND_LABEL[decision.kind]}</Tag>
            <Tag>{CLASSIFICATION_LABEL[decision.classification]}</Tag>
            <Tag>{RISK_LABEL[decision.risk]}</Tag>
            <span className="story-memory-confidence">
              可信度 {Math.round(decision.candidate_confidence * 100)}%
            </span>
          </Space>
          {review.status === 'open' ? (
            <Radio.Group
              size="small"
              value={selected}
              onChange={(event) => updateResolution(
                review.delta_id,
                decision.target_key,
                event.target.value as Resolution,
              )}
            >
              <Radio.Button value="accepted">接受</Radio.Button>
              <Radio.Button value="rejected">拒绝</Radio.Button>
            </Radio.Group>
          ) : (
            <Tag>{decision.resolution === 'accepted' ? '已接受' : decision.resolution === 'rejected' ? '已拒绝' : '未处理'}</Tag>
          )}
        </div>

        <div className="story-memory-rationale">{decision.rationale}</div>
        <div className="story-memory-target-key">{decision.target_key}</div>

        <div className="story-memory-payload">
          {Object.entries(decision.candidate_payload || {}).map(([key, value]) => (
            <div className="story-memory-payload-row" key={key}>
              <span>{key}</span>
              <code>{formatValue(value)}</code>
            </div>
          ))}
        </div>

        {decision.field_changes.length ? (
          <div className="story-memory-field-changes">
            {decision.field_changes.map((change) => (
              <div key={change.field}>
                <strong>{change.field}</strong>
                <span>{formatValue(change.before)}</span>
                <span>→</span>
                <span>{formatValue(change.after)}</span>
              </div>
            ))}
          </div>
        ) : null}

        {decision.source_excerpt ? (
          <blockquote className="story-memory-evidence">{decision.source_excerpt}</blockquote>
        ) : null}
      </div>
    )
  }

  return (
    <div className="story-memory-review-center">
      <div className="story-memory-policy-grid">
        <div className="memory-center-intelligence">
          <div>
            <div className="memory-center-intelligence-title">章节故事状态分析</div>
            <div className="memory-center-intelligence-desc">
              开启后，用户确认的 AI 正文改动会生成带原文证据的结构化候选。
            </div>
          </div>
          <Switch
            checked={analysisEnabled}
            loading={settingsSaving}
            onChange={async (checked) => {
              if (await saveSettings({ story_memory_analysis_enabled: checked })) {
                setAnalysisEnabled(checked)
              }
            }}
          />
        </div>

        <div className="memory-center-intelligence">
          <div>
            <div className="memory-center-intelligence-title">低风险自动应用</div>
            <div className="memory-center-intelligence-desc">
              仅自动接受白名单中的低风险新增；更新、冲突、替代和高风险候选始终人工处理。
            </div>
          </div>
          <Space size={8}>
            <Select
              size="small"
              value={minConfidence}
              disabled={!autoApplyEnabled || settingsSaving}
              options={[
                { value: 0.9, label: '≥ 90%' },
                { value: 0.95, label: '≥ 95%' },
                { value: 0.98, label: '≥ 98%' },
              ]}
              onChange={async (value) => {
                if (await saveSettings({ story_memory_auto_apply_min_confidence: value })) {
                  setMinConfidence(value)
                }
              }}
            />
            <Switch
              checked={autoApplyEnabled}
              loading={settingsSaving}
              onChange={async (checked) => {
                if (await saveSettings({ story_memory_auto_apply_enabled: checked })) {
                  setAutoApplyEnabled(checked)
                }
              }}
            />
          </Space>
        </div>
      </div>

      <div className="story-memory-review-toolbar">
        <Select value={status} options={STATUS_OPTIONS} onChange={setStatus} />
        <Button onClick={() => void load()} loading={loading}>刷新</Button>
      </div>

      <Spin spinning={loading}>
        {reviews.length ? (
          <div className="story-memory-review-list">
            {reviews.map((review) => (
              <article className="story-memory-review-item" key={review.delta_id}>
              <div className="story-memory-review-card">
                <div className="story-memory-review-header">
                  <div>
                    <div className="story-memory-review-title">章节 {review.chapter_id}</div>
                    <div className="story-memory-review-summary">
                      新增 {review.summary.addition} · 更新 {review.summary.update} ·
                      冲突 {review.summary.conflict} · 替代 {review.summary.supersession}
                    </div>
                  </div>
                  <Tag>{STATUS_OPTIONS.find((item) => item.value === review.status)?.label || review.status}</Tag>
                </div>

                <div className="story-memory-decision-list">
                  {review.decisions.map((decision) => renderDecision(review, decision))}
                </div>

                {review.status === 'open' ? (
                  <div className="story-memory-review-footer">
                    <span>接受的候选会确认为正式设定；拒绝的候选仅保留审计记录。</span>
                    <Button
                      type="primary"
                      loading={submittingId === review.delta_id}
                      onClick={() => void resolveReview(review)}
                    >
                      提交本组决议
                    </Button>
                  </div>
                ) : null}
              </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={status === 'open' ? '暂无待处理故事演化' : '暂无审查记录'}
          />
        )}
      </Spin>
    </div>
  )
}

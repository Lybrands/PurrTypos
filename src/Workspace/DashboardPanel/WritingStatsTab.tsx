import React from 'react'
import {
  AimOutlined,
  CheckOutlined,
  EditOutlined,
  FireOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { Button, Empty, InputNumber, Progress, Spin, Tooltip } from 'antd'
import type { EntityId, WritingStatsData } from '../../types'
import { useAntdApp } from '../../hooks/useAntdApp'
import { formatWords } from './dashboardFormatters'

interface WritingStatsTabProps {
  bookId: EntityId
}

export default function WritingStatsTab({ bookId }: WritingStatsTabProps) {
  const { message } = useAntdApp()
  const [loading, setLoading] = React.useState(true)
  const [data, setData] = React.useState<WritingStatsData | null>(null)
  const [editingGoal, setEditingGoal] = React.useState(false)
  const [goalDraft, setGoalDraft] = React.useState<number>(0)
  const [savingGoal, setSavingGoal] = React.useState(false)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const res = await window.electronAPI.getWritingStats({ bookId })
      if (res.success && res.data) setData(res.data)
      else message.error(res.error || '加载写作统计失败')
    } finally {
      setLoading(false)
    }
  }, [bookId, message])

  React.useEffect(() => { reload() }, [reload])

  const saveGoal = React.useCallback(async () => {
    setSavingGoal(true)
    try {
      const res = await window.electronAPI.setWritingGoal({ bookId, dailyWords: goalDraft || 0 })
      if (res.success) {
        setEditingGoal(false)
        reload()
      } else {
        message.error(res.error || '保存目标失败')
      }
    } finally {
      setSavingGoal(false)
    }
  }, [bookId, goalDraft, reload, message])

  if (loading && !data) {
    return <div className="dashboard-loading"><Spin /></div>
  }
  if (!data) return <Empty description="暂无数据" />

  const goalPercent = data.goalWords > 0
    ? Math.min(100, Math.round((data.todayWords / data.goalWords) * 100))
    : 0
  const maxDaily = Math.max(...data.daily.map((d) => d.words), 1)
  const writtenChapters = data.chapters.filter((c) => c.words > 0)
  const maxChapterWords = Math.max(...writtenChapters.map((c) => c.words), 1)

  return (
    <div className="dashboard-scroll">
      <div className="dashboard-goal-card">
        <div className="dashboard-goal-main">
          <div className="dashboard-goal-today">
            <span className="dashboard-goal-words">{data.todayWords}</span>
            <span className="dashboard-goal-unit">字 · 今日</span>
          </div>
          {data.goalWords > 0 ? (
            <Progress
              percent={goalPercent}
              size="small"
              status={goalPercent >= 100 ? 'success' : 'active'}
              format={() => `${goalPercent}%`}
            />
          ) : (
            <div className="dashboard-goal-none">未设置每日目标</div>
          )}
        </div>
        <div className="dashboard-goal-side">
          {editingGoal ? (
            <span className="dashboard-goal-edit">
              <InputNumber
                size="small"
                min={0}
                max={100000}
                step={500}
                value={goalDraft}
                onChange={(v) => setGoalDraft(Number(v) || 0)}
                placeholder="每日字数"
              />
              <Button
                type="text"
                size="small"
                icon={<CheckOutlined />}
                loading={savingGoal}
                onClick={saveGoal}
                title="保存目标"
              />
            </span>
          ) : (
            <Button
              type="text"
              size="small"
              icon={data.goalWords > 0 ? <EditOutlined /> : <AimOutlined />}
              onClick={() => { setGoalDraft(data.goalWords || 2000); setEditingGoal(true) }}
            >
              {data.goalWords > 0 ? `目标 ${data.goalWords} 字/天` : '设定目标'}
            </Button>
          )}
          <Tooltip title="刷新统计">
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              onClick={reload}
              loading={loading}
            />
          </Tooltip>
        </div>
      </div>

      <div className="dashboard-summary-row">
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">
            <FireOutlined className="dashboard-streak-icon" /> {data.streakDays}
            <span className="dashboard-stat-sub"> 天</span>
          </div>
          <div className="dashboard-stat-label">{data.goalWords > 0 ? '连续达标' : '连续有更新'}</div>
        </div>
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">{formatWords(data.totalWords)}</div>
          <div className="dashboard-stat-label">正文总字数</div>
        </div>
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">{formatWords(data.avgChapterWords)}</div>
          <div className="dashboard-stat-label">平均每章</div>
        </div>
      </div>

      <div className="dashboard-section">
        <div className="dashboard-section-title">近 30 天日更</div>
        {data.daily.length === 0 ? (
          <div className="dashboard-empty-line">还没有写作记录，从今天开始吧。</div>
        ) : (
          <div className="dashboard-daily-chart">
            {data.daily.map((d) => {
              const met = data.goalWords > 0 && d.words >= data.goalWords
              return (
                <Tooltip key={d.date} title={`${d.date}：${d.words} 字`}>
                  <div className="dashboard-daily-col">
                    <div
                      className={`dashboard-daily-bar${met ? ' is-met' : ''}${d.words === 0 ? ' is-zero' : ''}`}
                      style={{ height: `${Math.max(2, Math.round((d.words / maxDaily) * 100))}%` }}
                    />
                  </div>
                </Tooltip>
              )
            })}
          </div>
        )}
      </div>

      <div className="dashboard-section">
        <div className="dashboard-section-title">章节字数分布</div>
        {writtenChapters.length === 0 ? (
          <div className="dashboard-empty-line">还没有已写正文的章节。</div>
        ) : (
          <div className="dashboard-list">
            {data.chapters.map((c) => (
              <div key={c.id} className="dashboard-chapter-item">
                <span className="dashboard-chapter-title">
                  {c.index}. {c.title}
                  {c.volumeTitle ? <span className="dashboard-chapter-volume">（{c.volumeTitle}）</span> : null}
                </span>
                <span className="dashboard-chapter-bar-wrap">
                  <span
                    className="dashboard-chapter-bar"
                    style={{ width: `${Math.round((c.words / maxChapterWords) * 100)}%` }}
                  />
                </span>
                <span className="dashboard-chapter-words">{c.words}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

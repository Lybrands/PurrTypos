import React from 'react'
import { ReloadOutlined } from '@ant-design/icons'
import { Button, Empty, Spin, Tag, Tooltip } from 'antd'
import type { EntityId, StoryHealthData } from '../../types'
import { useAntdApp } from '../../hooks/useAntdApp'
import { formatWords } from './dashboardFormatters'

interface StoryHealthTabProps {
  bookId: EntityId
}

export default function StoryHealthTab({ bookId }: StoryHealthTabProps) {
  const { message } = useAntdApp()
  const [loading, setLoading] = React.useState(true)
  const [data, setData] = React.useState<StoryHealthData | null>(null)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const res = await window.electronAPI.getStoryHealth({ bookId })
      if (res.success && res.data) setData(res.data)
      else message.error(res.error || '加载故事健康数据失败')
    } finally {
      setLoading(false)
    }
  }, [bookId, message])

  React.useEffect(() => { reload() }, [reload])

  if (loading && !data) {
    return <div className="dashboard-loading"><Spin /></div>
  }
  if (!data) return <Empty description="暂无数据" />

  const fs = data.foreshadowing

  return (
    <div className="dashboard-scroll">
      <div className="dashboard-summary-row">
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">
            {data.writtenChapters}<span className="dashboard-stat-sub"> / {data.totalChapters} 章</span>
          </div>
          <div className="dashboard-stat-label">已动笔章节</div>
        </div>
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">{formatWords(data.totalWords)}</div>
          <div className="dashboard-stat-label">正文总字数</div>
        </div>
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">
            {fs.unresolvedCount}
            {fs.overdueCount > 0 ? (
              <span className="dashboard-stat-sub dashboard-stat-sub--danger"> 逾期 {fs.overdueCount}</span>
            ) : null}
          </div>
          <div className="dashboard-stat-label">未回收伏笔</div>
        </div>
        <div className="dashboard-stat-card">
          <div className="dashboard-stat-value">{fs.resolvedCount}</div>
          <div className="dashboard-stat-label">已回收伏笔</div>
        </div>
        <Tooltip title="重新统计">
          <Button
            type="text"
            size="small"
            icon={<ReloadOutlined />}
            onClick={reload}
            loading={loading}
            className="dashboard-refresh-btn"
          />
        </Tooltip>
      </div>

      <div className="dashboard-section">
        <div className="dashboard-section-title">伏笔回收预警</div>
        {fs.unresolved.length === 0 ? (
          <div className="dashboard-empty-line">没有未回收的伏笔，一切尽在掌握。</div>
        ) : (
          <div className="dashboard-list">
            {fs.unresolved.map((f) => (
              <div key={f.id} className="dashboard-foreshadow-item">
                <div className="dashboard-foreshadow-head">
                  {f.overdue ? (
                    <Tag color="error">已逾期</Tag>
                  ) : f.dueSoon ? (
                    <Tag color="warning">临近回收</Tag>
                  ) : (
                    <Tag>{f.type || '悬念'}</Tag>
                  )}
                  <span className="dashboard-foreshadow-content">{f.content}</span>
                </div>
                <div className="dashboard-foreshadow-meta">
                  {f.chapterIndex != null ? (
                    <span>埋设于第 {f.chapterIndex} 章{f.chapterTitle ? `「${f.chapterTitle}」` : ''}</span>
                  ) : (
                    <span>埋设章节未记录</span>
                  )}
                  {f.expectedChapterIndex != null ? (
                    <span>
                      · 预期第 {f.expectedChapterIndex} 章
                      {f.expectedChapterTitle ? `「${f.expectedChapterTitle}」` : ''}回收
                    </span>
                  ) : (
                    <span>· 未设置预期回收章节</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="dashboard-section">
        <div className="dashboard-section-title">
          人物出场
          <span className="dashboard-section-hint">
            按姓名在正文中出现的章数统计；超过 {data.gapWarnThreshold} 章未出场会标黄
          </span>
        </div>
        {data.characters.length === 0 ? (
          <div className="dashboard-empty-line">还没有录入人物。</div>
        ) : (
          <div className="dashboard-list">
            {data.characters.map((c) => {
              const gapWarn = c.gapChapters != null && c.gapChapters >= data.gapWarnThreshold
              return (
                <div key={c.id} className="dashboard-character-item">
                  <span className="dashboard-character-name">{c.name}</span>
                  <span className="dashboard-character-meta">
                    {c.appearChapters > 0 ? (
                      <>
                        出场 {c.appearChapters} 章 · 最近在第 {c.lastChapterIndex} 章
                        {c.gapChapters != null && c.gapChapters > 0 ? `（已隔 ${c.gapChapters} 章）` : '（最新章在场）'}
                      </>
                    ) : '尚未出场'}
                  </span>
                  {gapWarn ? <Tag color="warning">久未出场</Tag> : null}
                  {c.appearChapters === 0 ? <Tag>未出场</Tag> : null}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

import { services } from '@/services'
import React from 'react'
import { RefreshIcon } from '@/purr-components'
import { PurrButton, PurrEmpty, PurrModal, PurrSpin, PurrTag, PurrTooltip } from '@/purr-components'
import type { CharacterAppearanceItem, EntityId, StoryHealthData } from '../../types'
import { useAppFeedback } from '../../hooks/useAppFeedback'
import { formatWords } from './dashboardFormatters'

interface StoryHealthTabProps {
  bookId: EntityId
}

function CharacterAppearanceRow({
  character,
  gapWarnThreshold,
}: {
  character: CharacterAppearanceItem
  gapWarnThreshold: number
}) {
  const [detailOpen, setDetailOpen] = React.useState(false)
  const gapWarn = character.gapChapters != null && character.gapChapters >= gapWarnThreshold
  const refs = character.chapterRefs ?? []
  const topRefs = [...refs].sort((a, b) => b.mentions - a.mentions).slice(0, 3)
  return (
    <div className="dashboard-character-item">
      <div className="dashboard-character-main">
        <span className="dashboard-character-name">{character.name}</span>
        <span className="dashboard-character-meta">
          {character.appearChapters > 0 ? (
            <>
              出场 {character.appearChapters} 章 · 最近在第 {character.lastChapterIndex} 章
              {character.gapChapters != null && character.gapChapters > 0 ? `（已隔 ${character.gapChapters} 章）` : '（最新章在场）'}
            </>
          ) : '尚未出场'}
        </span>
        {gapWarn ? <PurrTag color="warning">久未出场</PurrTag> : null}
        {character.appearChapters === 0 ? <PurrTag>未出场</PurrTag> : null}
        {refs.length > 0 ? (
          <PurrButton
            type="text"
            size="small"
            onClick={() => setDetailOpen(true)}
          >
            出场章 {refs.length}
          </PurrButton>
        ) : null}
      </div>
      <PurrModal
        title={`${character.name} · 出场章节`}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        destroyOnHidden
        width={560}
      >
        <div className="dashboard-chapter-modal">
          {topRefs.length > 0 ? (
            <div className="dashboard-chapter-modal-top">
              <span className="dashboard-chapter-modal-label">戏份最多</span>
              <div className="dashboard-chapter-modal-top-tags">
                {topRefs.map((r) => (
                  <PurrTooltip key={r.chapterId} title={`第 ${r.index} 章「${r.title}」 · 提及 ${r.mentions} 次`}>
                    <PurrTag color="blue">第 {r.index} 章 · {r.mentions} 次</PurrTag>
                  </PurrTooltip>
                ))}
              </div>
            </div>
          ) : null}
          <div className="dashboard-chapter-modal-list">
            {refs.map((r) => (
              <div key={r.chapterId} className="dashboard-chapter-modal-item">
                <span className="dashboard-chapter-modal-index">第 {r.index} 章</span>
                <span className="dashboard-chapter-modal-title">{r.title || '未命名章节'}</span>
                <span className="dashboard-chapter-modal-mentions">提及 {r.mentions} 次</span>
              </div>
            ))}
          </div>
        </div>
      </PurrModal>
    </div>
  )
}

export default function StoryHealthTab({ bookId }: StoryHealthTabProps) {
  const { message } = useAppFeedback()
  const [loading, setLoading] = React.useState(true)
  const [data, setData] = React.useState<StoryHealthData | null>(null)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const res = await services.dashboard.getStoryHealth({ bookId })
      if (res.success && res.data) setData(res.data)
      else message.error(res.error || '加载故事健康数据失败')
    } finally {
      setLoading(false)
    }
  }, [bookId, message])

  React.useEffect(() => { reload() }, [reload])

  if (loading && !data) {
    return <div className="dashboard-loading"><PurrSpin /></div>
  }
  if (!data) return <PurrEmpty description="暂无数据" />

  const fs = data.foreshadowing

  return (
    <div className="dashboard-scroll">
      <div className="dashboard-summary">
        <div className="dashboard-summary-header">
          <span>统计概览</span>
          <PurrTooltip title="重新统计">
            <PurrButton
              type="text"
              size="small"
              icon={<RefreshIcon />}
              onClick={reload}
              loading={loading}
              className="dashboard-refresh-btn"
            />
          </PurrTooltip>
        </div>
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
        </div>
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
                    <PurrTag color="error">已逾期</PurrTag>
                  ) : f.dueSoon ? (
                    <PurrTag color="warning">临近回收</PurrTag>
                  ) : (
                    <PurrTag>{f.type || '悬念'}</PurrTag>
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
            {data.characters.map((c) => (
              <CharacterAppearanceRow
                key={c.id}
                character={c}
                gapWarnThreshold={data.gapWarnThreshold}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

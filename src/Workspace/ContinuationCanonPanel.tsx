import React from 'react'
import Markdown from '../components/Markdown'
import './ContinuationPanels.scss'
import { services } from '@/services'
import { PurrSpin, usePurrToast } from '@/purr-components'
import type { ContinuationWorkspace, EntityId } from '../types'

const fieldLabels: Record<string, string> = {
  description: '描述', name: '名称', status: '状态', location: '地点', time: '时间',
  content: '内容', summary: '概述', relationship: '关系', target: '对象', reason: '原因',
  character: '人物', participants: '参与者', outcome: '结果', evidence: '依据',
}
function FactValue({ value }: { value: unknown }): React.ReactNode {
  if (value == null) return <span>未注明</span>
  if (Array.isArray(value)) return <ul>{value.map((item, index) => <li key={index}><FactValue value={item} /></li>)}</ul>
  if (typeof value === 'object') return <dl className="canon-fact-fields">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{fieldLabels[key] || key}</dt><dd><FactValue value={item} /></dd></div>)}</dl>
  return <Markdown>{typeof value === 'boolean' ? (value ? '是' : '否') : String(value)}</Markdown>
}
const kindLabels: Record<string, string> = { character_identity: '人物身份', character_state: '人物状态', character_knowledge: '人物知情', background: '故事背景', location: '地点', faction: '势力', item: '物品', unresolved_plot: '未决情节', character: '人物', relationship: '关系', event: '事件', timeline: '时间线', world_rule: '世界规则', setting: '设定', foreshadowing: '伏笔', unresolved_thread: '未决线索' }

export default function ContinuationCanonPanel({ bookId }: { bookId: EntityId | null }) {
  const toast = usePurrToast()
  const [data, setData] = React.useState<ContinuationWorkspace | null>(null)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    if (bookId == null) return
    let active = true
    setLoading(true)
    void services.continuations.get({ bookId: String(bookId) }).then((result) => {
      if (!active) return
      if (result.success && result.data) setData(result.data)
      else toast.error(result.error || '加载继承正史失败')
      setLoading(false)
    })
    return () => { active = false }
  }, [bookId, toast])

  if (loading) return <div className="workspace-utility-loading"><PurrSpin size="small" /></div>
  if (!data) return <div className="notebook-placeholder">续写绑定不可用</div>
  return <div className="continuation-canon-panel">
    <header><h2>继承正史</h2><p>截至分叉章节的原作事实，作为本书的只读基线。</p></header>
    <dl>
      <dt>来源</dt><dd>{data.binding.sourceTitle}</dd>
      <dt>分叉点</dt><dd>{data.binding.forkSectionTitle}</dd>
    </dl>
    <div className="continuation-canon-records">
      {data.canonRecords.length === 0 ? <p>此分叉点没有可继承的硬事实。</p> : data.canonRecords.map((record) => (
        <article key={record.sourceFactId}>
          <strong>{record.subjectKey} · {record.predicate}</strong>
          <span className="canon-fact-kind">{kindLabels[record.factKind] || "原作事实"}</span>
          <FactValue value={record.value} />
        </article>
      ))}
    </div>
  </div>
}

import React from 'react'
import { PurrCollapse } from '@/purr-components'
import './ContinuationPanels.scss'
import { apiGet } from '../services/httpClient'
import Markdown from '../components/Markdown'
import type { EntityId } from '../types'

export default function InheritedPlotMaterials({bookId}: {bookId: EntityId | null}) {
  const [items, setItems] = React.useState<Array<{sourceKey: string; body: string}>>([])
  const [error, setError] = React.useState('')
  React.useEffect(() => {
    let current = true
    setItems([]); setError('')
    if (bookId == null) return
    void apiGet<Array<{sourceKey: string; body: string}>>(`/continuations/${bookId}/plot-materials`).then(result => {
      if (current) { setItems(result.data || []); setError(result.success ? '' : result.error || '继承情节资料读取失败') }
    })
    return () => {current = false}
  }, [bookId])
  if (!items.length && !error) return null
  return <section className="inherited-plot-materials" aria-label="原作事件与伏笔"><h3>原作时间线与伏笔</h3>
    <p>原作基线只读；后续发展记录在本书资料中。</p>{error && <p role="alert">{error}</p>}
    <PurrCollapse size="small" items={items.map(item => ({key: item.sourceKey, label: item.sourceKey, children: <Markdown>{item.body}</Markdown>}))} />
  </section>
}

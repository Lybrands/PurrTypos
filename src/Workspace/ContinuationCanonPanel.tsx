import React from 'react'
import { services } from '@/services'
import { PurrSpin, usePurrToast } from '@/purr-components'
import type { ContinuationWorkspace, EntityId } from '../types'

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
    <header><h2>继承正史</h2><p>只读冻结快照。写作时与本书 Story Memory 组合召回，冲突时继承正史优先。</p></header>
    <dl>
      <dt>来源</dt><dd>{data.binding.sourceTitle}</dd>
      <dt>分叉点</dt><dd>{data.binding.forkSectionTitle}</dd>
      <dt>来源版本</dt><dd>{data.binding.sourceRevisionId}</dd>
      <dt>正史快照</dt><dd>{data.binding.canonSnapshotDigest}</dd>
    </dl>
    <div className="continuation-canon-records">
      {data.canonRecords.length === 0 ? <p>此分叉点没有可继承的硬事实。</p> : data.canonRecords.map((record) => (
        <article key={record.sourceFactId}>
          <strong>{record.subjectKey} · {record.predicate}</strong>
          <span>{record.factKind}</span>
          <pre>{JSON.stringify(record.value, null, 2)}</pre>
        </article>
      ))}
    </div>
  </div>
}

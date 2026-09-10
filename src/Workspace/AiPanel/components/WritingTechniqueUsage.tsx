import React from 'react'
import { services } from '@/services'
import type { TechniqueUsage } from '../../../services/writingTechniques'

export default function WritingTechniqueUsage({ runId, running }: {runId?: string; running: boolean}) {
  const [items, setItems] = React.useState<TechniqueUsage[]>([])
  React.useEffect(() => {
    setItems([])
    if (!runId || running) return
    let active = true
    void services.writingTechniques.runUsage(runId).then(result => {
      if (active && result.success && result.data) setItems(result.data)
    }).catch(() => {})
    return () => { active = false }
  }, [runId, running])
  if (!items.length) return null
  return <details className="writing-technique-usage"><summary>本轮已读取的写作技法与方案 · {items.length}</summary>
    {items.map(item => <div key={`${item.ref.kind}:${item.ref.id}:${item.ref.versionId}`}>
      <strong>{item.name}</strong> · {item.source === 'manual' ? '手动指定' : '自动选用'}
      <ul>{item.files.map(file => <li key={`${file.ref.id}:${file.path}`}>{file.path}</li>)}</ul>
    </div>)}
  </details>
}

import React from 'react'
import { PurrButton, PurrCollapse } from '@/purr-components'
import { novelKnowledge, type KnowledgeMaterialsStatus } from '../../services/novelKnowledge'

type Status = KnowledgeMaterialsStatus

export default function SharedMaterials({ book, onChanged }: { book: string; onChanged: () => Promise<void> }) {
  const [status, setStatus] = React.useState<Status>()
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  React.useEffect(() => {
    let alive = true
    setStatus(undefined); setError('')
    void novelKnowledge.materialsStatus(book).then(r => { if (alive) { if (r.success) setStatus(r.data); else setError(r.error || '无法读取资料存储状态') } })
    return () => { alive = false }
  }, [book])
  const restore = async (id: string) => {
    setBusy(true); setError('')
    try {
      const r = await novelKnowledge.restoreMaterial(book, id)
      if (!r.success) throw new Error(r.error || '恢复失败')
      setStatus(r.data)
      await onChanged()
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  const openObsidian = async () => {
    if (!window.purrDesktop?.openNovelKnowledgeLibrary) {
      setError(window.purrDesktop ? '请完全退出并重新打开 PurrTypos，以加载资料库打开功能。' : '请在桌面版中打开 Obsidian 资料库。')
      return
    }
    setBusy(true); setError('')
    try {
      const result = await window.purrDesktop.openNovelKnowledgeLibrary(book)
      if (!result.success) throw new Error(result.error || '无法打开 Obsidian')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return <div className="knowledge-shared-materials">
    {status?.mode === 'markdown' ? <>
      <div className="knowledge-shared-materials__heading">
        <p>与 Obsidian 共用同一份创作资料。</p>
        <PurrButton size="small" disabled={busy} onClick={() => void openObsidian()}>在 Obsidian 打开资料库</PurrButton>
      </div>
      <code title={status.directory}>{status.directory}</code>
    </> : status ? <p role="alert">资料目录未初始化，请检查作品数据或从完整备份恢复。</p> : null}
    {!!status?.deleted?.length && <PurrCollapse size="small" items={[{ key: 'deleted', label: '已删除的共享资料', children: status.deleted.map(item => <p key={item.id}>{item.name} <PurrButton disabled={busy} onClick={() => void restore(item.id)}>恢复</PurrButton></p>) }]} />}
    {error && <p role="alert">{error}</p>}
  </div>
}

import React from 'react'
import './ContinuationHistory.scss'
import { PurrButton } from '@/purr-components'
import { continuationHistory, type SourceSectionNode } from '../services/continuationHistory'
import { useWorkspace } from './WorkspaceContext'

export default function ContinuationHistory({onCount}: {onCount: (count: number) => void}) {
  const { bookId, openUtilityTab } = useWorkspace()
  const [items, setItems] = React.useState<SourceSectionNode[]>([])
  const [next, setNext] = React.useState<number | null>(null)
  const [error, setError] = React.useState('')
  const generation = React.useRef(0)
  const load = React.useCallback(async (offset = 0) => {
    if (bookId == null) return
    const current = generation.current
    const result = await continuationHistory.list(String(bookId), offset)
    if (current !== generation.current) return
    if (!result.success || !result.data) { setError(result.error || '历史目录读取失败'); return }
    setItems(previous => offset ? [...previous, ...result.data!.items] : result.data!.items)
    setNext(result.data.nextOffset); onCount(result.data.chapterCount); setError('')
  }, [bookId, onCount])
  React.useEffect(() => { generation.current++; setItems([]); setNext(null); onCount(0); void load(); return () => { generation.current++ } }, [load, onCount])
  const exportBook = async (includeHistory: boolean) => {
    const result = await continuationHistory.export(String(bookId), includeHistory)
    if (!result.success || !result.data) { setError(result.error || '导出失败'); return }
    const url = URL.createObjectURL(new Blob([result.data.entries.map(item => item.text.trimStart().split("\n")[0].replace(/^#{1,6}\s+/, "").trim() === item.title ? item.text : `# ${item.title}\n\n${item.text}`).join('\n\n')], {type: 'text/markdown;charset=utf-8'}))
    const link = document.createElement('a'); link.href = url; link.download = includeHistory ? '完整作品.md' : '续写部分.md'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  if (!items.length && !error) return null
  return <section className="continuation-history" aria-label="原作历史章节">
    <strong>原作历史 · 只读</strong>
    {error && <p role="alert">{error}<PurrButton onClick={() => void load()}>重试</PurrButton></p>}
    {items.map((item, index) => <React.Fragment key={item.sectionId}>
      {item.sectionType !== 'volume' && item.locator.volumeTitle && item.locator.volumeId !== items[index - 1]?.locator.volumeId && <h4>{item.locator.volumeTitle}</h4>}
      <PurrButton type="text" className={`continuation-history-chapter${item.sectionType === 'volume' ? ' is-volume' : item.locator.volumeId ? ' in-volume' : ''}`} onClick={() => openUtilityTab({key: `source:${bookId}:${item.sectionId}`, kind: 'source', title: `${item.title} · 只读`, sourceSectionId: item.sectionId})}>{item.title} · 只读</PurrButton>
    </React.Fragment>)}
    {next != null && <PurrButton onClick={() => void load(next)}>加载更多历史章节</PurrButton>}
    <PurrButton onClick={() => void exportBook(true)}>导出完整作品</PurrButton>
    <PurrButton onClick={() => void exportBook(false)}>仅导出续写</PurrButton>
  </section>
}

export function SourceSectionReader({bookId, sectionId}: {bookId: string; sectionId: string}) {
  const [text, setText] = React.useState('读取中…')
  React.useEffect(() => {
    let current = true
    setText('读取中…')
    void continuationHistory.read(bookId, sectionId).then(result => { if (current) setText(result.success && result.data ? result.data.text : result.error || '原作读取失败') })
    return () => { current = false }
  }, [bookId, sectionId])
  return <article aria-label="原作正文（只读）" style={{whiteSpace: 'pre-wrap', padding: 24, overflow: 'auto', height: '100%'}}>{text}</article>
}

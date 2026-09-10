import { useState } from 'react'
import Markdown from '../components/Markdown'
import KnowledgeMarkdownEditor from '../components/KnowledgeMarkdownEditor'
import { CloseIcon, EditIcon, PurrCollapse, PurrButton, PurrTooltip, SaveIcon } from '@/purr-components'
import type { NovelAnalysisFact } from '../types'

export const materialGroups = [
  { key: 'characters', label: '人物资料', kinds: ['character_identity', 'character_state', 'character_knowledge', 'relationship', 'character_summary'] },
  { key: 'background', label: '故事背景', kinds: ['background'] },
  { key: 'world', label: '世界设定', kinds: ['world_rule', 'setting', 'location', 'faction', 'item'] },
  { key: 'outline', label: '故事大纲', kinds: ['event', 'timeline', 'story_summary'] },
  { key: 'threads', label: '剧情线与伏笔', kinds: ['unresolved_plot', 'foreshadowing'] },
  { key: 'unclassified', label: '待分类资料', kinds: [] },
]

export const belongsToMaterialGroup = (kind: string, group: typeof materialGroups[number]) => group.key === 'unclassified'
  ? !materialGroups.some(item => item.kinds.includes(kind)) : group.kinds.includes(kind)

export default function AnalysisMaterials({ facts, onEvidence, onChange }: {
  facts: NovelAnalysisFact[]
  onEvidence: (fact: NovelAnalysisFact) => void
  onChange: (previous: NovelAnalysisFact, next: NovelAnalysisFact) => void
}) {
  const [editing, setEditing] = useState<NovelAnalysisFact | null>(null)
  const [value, setValue] = useState('')
  const [editingBackground, setEditingBackground] = useState(false)
  const groups = new Map<string, NovelAnalysisFact[]>()
  for (const fact of facts) groups.set(fact.subjectKey, [...(groups.get(fact.subjectKey) ?? []), fact])
  if (!facts.length) return <p className="novel-analysis-result-empty">当前来源范围未提取到此类资料。</p>
  if (facts.every(fact => fact.factKind === 'background') && !editingBackground) {
    const body = [...groups].map(([name, items]) => {
      const paragraphs = items.map(fact => `${fact.claimNature === 'inference' ? '> 推断，非原文明示\n\n' : ''}${typeof fact.value === 'string' ? fact.value : JSON.stringify(fact.value, null, 2)}`)
      return `${groups.size > 1 ? `## ${name}\n\n` : ''}${paragraphs.join('\n\n')}`
    }).join('\n\n')
    const evidence = facts.flatMap(fact => fact.evidence)
    return <article className="novel-analysis-background-document">
      <div className="novel-technique-file-actions"><PurrTooltip title="编辑资料"><PurrButton type="text" size="small" icon={<EditIcon />} aria-label="编辑故事背景" onClick={() => setEditingBackground(true)} /></PurrTooltip></div>
      <Markdown>{body}</Markdown>
      <PurrCollapse size="small" items={[{ key: 'sources', label: '来源依据', children:
        <PurrButton type="text" size="small" onClick={() => onEvidence({ ...facts[0], subjectKey: '故事背景', predicate: '', value: body, evidence })}>查看汇总依据</PurrButton>
      }]} />
    </article>
  }
  return <div className="novel-analysis-result-list novel-analysis-materials">{editingBackground && <PurrButton type="text" size="small" onClick={() => { setEditing(null); setEditingBackground(false) }}>返回背景文档</PurrButton>}{[...groups].map(([name, items]) => <article key={name}>
    <strong>{name}</strong>
    {items.map((fact, index) => <section key={fact.id ?? index}>
      <header><p>{fact.claimNature === 'inference' && <small>推断 · </small>}{fact.predicate}{fact.lifecycleStatus && fact.lifecycleStatus !== 'active' && <small> · {({ resolved: '已解决', unresolved: '未决', disputed: '存在分歧', superseded: '历史状态', inactive: '历史状态' } as Record<string, string>)[fact.lifecycleStatus] ?? fact.lifecycleStatus}</small>}</p>
        {editing !== fact && typeof fact.value === 'string' && <div className="novel-technique-file-actions"><PurrTooltip title="编辑资料"><PurrButton type="text" size="small" icon={<EditIcon />} aria-label={`编辑${name}资料`} onClick={() => { setEditing(fact); setValue(fact.value as string) }} /></PurrTooltip></div>}
      </header>
      {editing === fact ? <>
        <KnowledgeMarkdownEditor documentKey={fact.id ?? `${name}:${index}`} value={value} onChange={setValue} ariaLabel={`${name}资料正文`} />
        <div className="novel-technique-file-actions">
          <PurrTooltip title="取消编辑"><PurrButton type="text" size="small" icon={<CloseIcon />} aria-label="取消编辑资料" onClick={() => setEditing(null)} /></PurrTooltip>
          <PurrTooltip title="保存修改"><PurrButton type="text" size="small" icon={<SaveIcon />} aria-label="保存资料修改" disabled={!value.trim()} onClick={() => { onChange(fact, { ...fact, value }); setEditing(null) }} /></PurrTooltip>
        </div>
      </> : <>
        <Markdown>{typeof fact.value === 'string' ? fact.value : JSON.stringify(fact.value, null, 2)}</Markdown>
      </>}
      <details><summary>来源依据</summary><PurrButton type="text" size="small" onClick={() => onEvidence(fact)}>查看原文</PurrButton></details>
    </section>)}
  </article>)}</div>
}

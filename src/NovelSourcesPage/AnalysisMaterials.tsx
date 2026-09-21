import { useMemo, useState } from 'react'
import Markdown from '../components/Markdown'
import KnowledgeMarkdownEditor from '../components/KnowledgeMarkdownEditor'
import { CloseIcon, EditIcon, PurrButton, PurrCollapse, PurrEmpty, PurrInput, PurrModal, PurrTag, PurrTooltip, SaveIcon } from '@/purr-components'
import type { NovelAnalysisFact, SettingEntityType } from '../types'
import MaterialProfileEditorModal, { MATERIAL_ENTITY_TYPE_OPTIONS } from '../Workspace/OutlinePanel/MaterialProfileEditorModal'
import '../Workspace/OutlinePanel/CharacterTab.scss'

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

type CharacterDraft = { name: string; tags: string; profile_md: string }
type EntityDraft = CharacterDraft & { entity_type: SettingEntityType }

function objectValue(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function splitTags(value: string) {
  return value.split(/[,，]/).map(item => item.trim()).filter(Boolean)
}

function materialMarkdown(fact: NovelAnalysisFact) {
  const value = objectValue(fact.value)
  return String(value?.content ?? value?.profile_md ?? (typeof fact.value === 'string' ? fact.value : JSON.stringify(fact.value, null, 2)))
}

function StructuredMaterial({ fact, index, onChange, expanded, onExpandedChange }: {
  fact: NovelAnalysisFact
  index?: number
  onChange: (previous: NovelAnalysisFact, next: NovelAnalysisFact) => void
  expanded: boolean
  onExpandedChange: (expanded: boolean) => void
}) {
  const raw = objectValue(fact.value)!
  const isCharacter = fact.factKind === 'character_summary'
  const isBackground = fact.factKind === 'background'
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(String(raw.name ?? fact.subjectKey))
  const [tags, setTags] = useState(splitTags(String(raw.tags ?? '')))
  const [profile, setProfile] = useState(String(raw.content ?? raw.profile_md ?? ''))
  const [entityType, setEntityType] = useState<SettingEntityType>(String(raw.entity_type ?? 'other') as SettingEntityType)

  const beginEdit = () => {
    setName(String(raw.name ?? fact.subjectKey))
    setTags(splitTags(String(raw.tags ?? '')))
    setProfile(String(raw.content ?? raw.profile_md ?? ''))
    setEntityType(String(raw.entity_type ?? 'other') as SettingEntityType)
    onExpandedChange(true)
    setOpen(true)
  }
  const save = () => {
    const trimmedName = name.trim()
    if ((!isBackground && !trimmedName) || !profile.trim()) return
    const value = isBackground
      ? { content: profile }
      : isCharacter
        ? { name: trimmedName, tags: tags.join(', '), profile_md: profile } satisfies CharacterDraft
        : { entity_type: entityType, name: trimmedName, tags: tags.join(', '), profile_md: profile } satisfies EntityDraft
    const factKind = !isBackground && !isCharacter
      ? entityType === 'other'
        ? (fact.factKind === 'world_rule' ? 'world_rule' : 'setting')
        : entityType
      : fact.factKind
    onChange(fact, { ...fact, factKind, subjectKey: isBackground ? fact.subjectKey : trimmedName, value })
    setOpen(false)
  }

  if (isBackground) return <article className="novel-analysis-material-card is-background">
      <header className="novel-analysis-material-card-header">
        <div><strong>故事背景</strong><span>作为续写的全局背景资料</span></div>
        <div className="novel-technique-file-actions"><PurrTooltip title="编辑资料"><PurrButton type="text" size="small" icon={<EditIcon />} aria-label="编辑故事背景" onClick={beginEdit} /></PurrTooltip></div>
      </header>
      <div className="novel-analysis-material-card-body"><Markdown>{profile}</Markdown></div>
      {open && <PurrModal title="编辑故事背景" open onOk={save} onCancel={() => setOpen(false)} okText="保存" cancelText="取消" okButtonProps={{ disabled: !profile.trim() }} width={680} destroyOnHidden className="character-edit-modal"><KnowledgeMarkdownEditor documentKey={`analysis-material:${fact.id ?? fact.subjectKey}`} value={profile} onChange={setProfile} ariaLabel="故事背景" className="character-edit-profile" /></PurrModal>}
    </article>

  const itemKey = fact.id ?? `${fact.factKind}:${fact.subjectKey}`
  const label = <div className="novel-analysis-material-card-identity">
          {index != null && <span className="novel-analysis-material-card-index">{String(index + 1).padStart(2, '0')}</span>}
          <div className="novel-analysis-material-card-title">
            <strong>{String(raw.name ?? fact.subjectKey)}</strong>
          {!isCharacter && <PurrTag>{MATERIAL_ENTITY_TYPE_OPTIONS.find(item => item.value === raw.entity_type)?.label ?? '其他'}</PurrTag>}
            {String(raw.tags ?? '') && <div className="novel-analysis-material-card-tags">{splitTags(String(raw.tags)).map(tag => <PurrTag key={tag} variant="filled" color="default">{tag}</PurrTag>)}</div>}
          </div>
        </div>
  return <>
    <PurrCollapse
      className="novel-analysis-material-collapse"
      size="small"
      activeKeys={expanded ? [itemKey] : []}
      onChange={keys => onExpandedChange(keys.includes(itemKey))}
      items={[{
        key: itemKey,
        label,
        extra: <div className="novel-technique-file-actions"><PurrTooltip title="编辑资料"><PurrButton type="text" size="small" icon={<EditIcon />} aria-label={`编辑${fact.subjectKey}资料`} onClick={beginEdit} /></PurrTooltip></div>,
        children: expanded ? <div className="novel-analysis-material-collapse-body"><Markdown>{profile}</Markdown></div> : null,
      }]}
    />
    {open && <MaterialProfileEditorModal kind={isCharacter ? 'character' : 'entity'} open name={name} tags={tags} profileMd={profile} documentKey={`analysis-material:${fact.id ?? fact.subjectKey}`} onNameChange={setName} onTagsChange={setTags} onProfileChange={setProfile} onOk={save} onCancel={() => setOpen(false)} entityType={entityType} onEntityTypeChange={setEntityType} />}
  </>
}

export default function AnalysisMaterials({ facts, onChange }: {
  facts: NovelAnalysisFact[]
  onChange: (previous: NovelAnalysisFact, next: NovelAnalysisFact) => void
}) {
  const [editing, setEditing] = useState<NovelAnalysisFact | null>(null)
  const [value, setValue] = useState('')
  const [query, setQuery] = useState('')
  const [expandedKeys, setExpandedKeys] = useState<string[]>([])
  const structured = useMemo(() => facts.filter(fact => {
    const value = objectValue(fact.value)
    return Boolean(value && (typeof value.content === 'string' || typeof value.profile_md === 'string'))
  }), [facts])
  const plainFacts = facts.filter(fact => !structured.includes(fact))
  const groups = new Map<string, NovelAnalysisFact[]>()
  for (const fact of plainFacts) groups.set(fact.subjectKey, [...(groups.get(fact.subjectKey) ?? []), fact])
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const matches = (items: NovelAnalysisFact[]) => !normalizedQuery || items.some(fact => {
    const value = objectValue(fact.value)
    return [
      fact.subjectKey,
      fact.predicate,
      String(value?.name ?? ''),
      String(value?.tags ?? ''),
      materialMarkdown(fact),
    ].join('\n').toLocaleLowerCase().includes(normalizedQuery)
  })
  const visibleStructured = structured.filter(fact => matches([fact]))
  const visibleGroups = [...groups].filter(([, items]) => matches(items))
  const visibleCount = visibleStructured.length + visibleGroups.length
  const setExpanded = (key: string, expanded: boolean) => {
    setExpandedKeys(current => expanded
      ? current.includes(key) ? current : [...current, key]
      : current.filter(item => item !== key))
  }
  if (!facts.length) return <PurrEmpty image={false} className="novel-analysis-result-empty" description="当前来源范围未提取到此类资料" />
  if (facts.every(fact => fact.factKind === 'background')) {
    if (structured.length) return <StructuredMaterial
      fact={structured[0]}
      index={0}
      onChange={onChange}
      expanded
      onExpandedChange={() => {}}
    />
    const body = [...groups].map(([name, items]) => {
      const paragraphs = items.map(fact => `${fact.claimNature === 'inference' ? '> 推断，非原文明示\n\n' : ''}${materialMarkdown(fact)}`)
      return `${groups.size > 1 ? `## ${name}\n\n` : ''}${paragraphs.join('\n\n')}`
    }).join('\n\n')
    return <article className="novel-analysis-material-card is-background is-readonly">
      <header className="novel-analysis-material-card-header"><div><strong>故事背景</strong><span>当前结果为只读资料</span></div></header>
      <div className="novel-analysis-material-card-body"><Markdown>{body}</Markdown></div>
    </article>
  }
  return <div className="novel-analysis-material-browser">
    <div className="novel-analysis-material-toolbar">
      <PurrInput.Search
        className="novel-analysis-material-search"
        value={query}
        onChange={event => setQuery(event.target.value)}
        placeholder="搜索名称、标签或资料内容"
        allowClear
        aria-label="搜索分析资料"
      />
      {expandedKeys.length > 0 && <PurrButton type="text" size="small" onClick={() => setExpandedKeys([])}>全部收起</PurrButton>}
    </div>
    {visibleCount === 0
      ? <PurrEmpty image={false} className="novel-analysis-result-empty" description="没有匹配的资料" />
      : <div className="novel-analysis-result-list novel-analysis-materials">
        {visibleStructured.map((fact, index) => {
          const key = fact.id ?? `${fact.factKind}:${fact.subjectKey}`
          return <StructuredMaterial key={key} fact={fact} index={index} onChange={onChange}
            expanded={expandedKeys.includes(key)} onExpandedChange={expanded => setExpanded(key, expanded)} />
        })}
        {visibleGroups.map(([name, items], index) => {
          const key = `group:${name}`
          const expanded = expandedKeys.includes(key)
          return <PurrCollapse key={key} className="novel-analysis-material-collapse"
            size="small"
            activeKeys={expanded ? [key] : []} onChange={keys => setExpanded(key, keys.includes(key))}
            items={[{
              key,
              label: <div className="novel-analysis-material-card-identity"><span className="novel-analysis-material-card-index">{String(visibleStructured.length + index + 1).padStart(2, '0')}</span><div className="novel-analysis-material-card-title"><strong>{name}</strong><PurrTag variant="filled" color="default">{items.length} 条</PurrTag></div></div>,
              children: expanded ? <div className="novel-analysis-material-collapse-body">{items.map((fact, index) => <section key={fact.id ?? index}>
                <header><p>{fact.claimNature === 'inference' && <small>推断 · </small>}{fact.predicate}{fact.lifecycleStatus && fact.lifecycleStatus !== 'active' && <small> · {({ resolved: '已解决', unresolved: '未决', disputed: '存在分歧', superseded: '历史状态', inactive: '历史状态' } as Record<string, string>)[fact.lifecycleStatus] ?? fact.lifecycleStatus}</small>}</p>
                  {editing !== fact && typeof fact.value === 'string' && <div className="novel-technique-file-actions"><PurrTooltip title="编辑资料"><PurrButton type="text" size="small" icon={<EditIcon />} aria-label={`编辑${name}资料`} onClick={() => { setEditing(fact); setValue(fact.value as string) }} /></PurrTooltip></div>}
                </header>
                {editing === fact ? <>
                  <KnowledgeMarkdownEditor documentKey={fact.id ?? `${name}:${index}`} value={value} onChange={setValue} ariaLabel={`${name}资料正文`} />
                  <div className="novel-technique-file-actions">
                    <PurrTooltip title="取消编辑"><PurrButton type="text" size="small" icon={<CloseIcon />} aria-label="取消编辑资料" onClick={() => setEditing(null)} /></PurrTooltip>
                    <PurrTooltip title="保存修改"><PurrButton type="text" size="small" icon={<SaveIcon />} aria-label="保存资料修改" disabled={!value.trim()} onClick={() => { onChange(fact, { ...fact, value }); setEditing(null) }} /></PurrTooltip>
                  </div>
                </> : <Markdown>{materialMarkdown(fact)}</Markdown>}
              </section>)}</div> : null,
            }]} />
        })}
      </div>}
  </div>
}

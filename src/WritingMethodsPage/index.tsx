import React from 'react'
import { useLocation } from 'react-router-dom'
import { services } from '@/services'
import { ArrowRightIcon, FileTextIcon, ImportIcon, ReadIcon, PlusIcon, PurrButton, PurrEmpty, PurrInput, PurrModal, PurrSegmented, PurrSpin, usePurrToast } from '@/purr-components'
import AppHeader from '../components/AppHeader'
import { techniqueOperationId, type TechniqueKind, type TechniqueObject, type SchemeBundle } from '../services/writingTechniques'
import TechniqueEditor from './TechniqueEditor'
import SchemeEditor from './SchemeEditor'
import { requireTechniqueData } from './operations'
import './index.scss'

export default function WritingMethodsPage({ onBack, onHome, onOpenSettings }: { onBack: () => void; onHome: () => void; onOpenSettings: () => void }) {
  const toast = usePurrToast()
  const location = useLocation()
  const locateId = typeof location.state?.locateTechniqueId === 'string' ? location.state.locateTechniqueId : ''
  const [highlightedId, setHighlightedId] = React.useState('')
  const locatedCard = React.useRef<HTMLButtonElement>(null)
  const locatedKey = React.useRef('')
  const [kind, setKind] = React.useState<TechniqueKind>('technique')
  const [libraryStatus, setLibraryStatus] = React.useState<'active' | 'archived'>('active')
  const [objects, setObjects] = React.useState<TechniqueObject[]>([])
  const [techniques, setTechniques] = React.useState<TechniqueObject[]>([])
  const [selected, setSelected] = React.useState<TechniqueObject | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [creating, setCreating] = React.useState(false)
  const [name, setName] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [preview, setPreview] = React.useState<{files: Record<string, string>; entryRenamed: boolean} | null>(null)
  const [schemePreview, setSchemePreview] = React.useState<SchemeBundle | null>(null)
  const input = React.useRef<HTMLInputElement>(null)
  const operation = React.useRef(techniqueOperationId())
  const visibleObjects = objects.filter(object => object.status === libraryStatus)
  const kindLabel = kind === 'technique' ? '写作技法' : '写作方案'
  const beginCreate = () => { setLibraryStatus('active'); setName(''); setDescription(''); operation.current = techniqueOperationId(); setCreating(true) }

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const [items, library] = await Promise.all([services.writingTechniques.list(kind, true), services.writingTechniques.list('technique')])
      setObjects(requireTechniqueData(items)); setTechniques(requireTechniqueData(library))
    } catch (error) { toast.error((error as Error).message) } finally { setLoading(false) }
  }, [kind, toast])
  React.useEffect(() => { void reload() }, [reload])
  React.useEffect(() => {
    if (locateId) setKind('technique')
  }, [locateId, location.key])
  React.useEffect(() => {
    if (!locateId || loading || kind !== 'technique' || !objects.length || locatedKey.current === location.key) return
    const target = objects.find(object => object.id === locateId)
    if (!target) { locatedKey.current = location.key; toast.info('此技法已不在库中'); return }
    setLibraryStatus(target.status)
    setHighlightedId(target.id)
  }, [locateId, loading, kind, objects, location.key, toast])
  React.useEffect(() => {
    if (!highlightedId || loading || !locatedCard.current) return
    locatedKey.current = location.key
    locatedCard.current.scrollIntoView({block: 'center', behavior: 'auto'})
    locatedCard.current.focus({preventScroll: true})
    const timer = window.setTimeout(() => setHighlightedId(''), 4000)
    return () => window.clearTimeout(timer)
  }, [highlightedId, libraryStatus, loading, location.key])
  const open = async (objectKind: TechniqueKind, id: string) => {
    try { setSelected(requireTechniqueData(await services.writingTechniques.get(objectKind, id))) }
    catch (error) { toast.error((error as Error).message) }
  }
  const create = async () => {
    setBusy(true)
    try {
      const draft = kind === 'technique'
        ? requireTechniqueData(await services.writingTechniques.importFiles({ 'SKILL.md': `---\nname: ${JSON.stringify(name)}\ndescription: ${JSON.stringify(description)}\n---\n\n` }, operation.current))
        : requireTechniqueData(await services.writingTechniques.createScheme({ schemaVersion: 1, name, description, composition: '', members: [] }, operation.current))
      setCreating(false); await reload(); await open(kind, draft.techniqueId || draft.schemeId || '')
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const previewUpload = async (file?: File) => {
    if (!file) return
    setBusy(true)
    try {
      if (file.name.toLowerCase().endsWith('.json')) setSchemePreview(requireTechniqueData(await services.writingTechniques.previewScheme(file)))
      else setPreview(requireTechniqueData(await services.writingTechniques.previewUpload(file)))
      operation.current = techniqueOperationId()
    }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
    if (input.current) input.current.value = ''
  }
  const importFiles = async () => {
    if (!preview) return
    setBusy(true)
    try {
      const draft = requireTechniqueData(await services.writingTechniques.importFiles(preview.files, operation.current))
      setPreview(null); setKind('technique'); setLibraryStatus('active'); await reload(); await open('technique', draft.techniqueId!)
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <div className="writing-methods-page">
    <AppHeader
      title="写作技法库"
      navigation={{
        home: { label: '返回首页', onClick: onHome },
        back: { label: '返回书架', onClick: onBack },
      }}
      showActions
      onOpenSettings={onOpenSettings}
    />
    <main className="writing-methods-main">
      <div className="writing-methods-toolbar"><div><h1>写作技法库</h1><p>整理可复用的写法，组合适合不同创作的方案。</p></div>
        <div className="writing-methods-header-actions">
          <input ref={input} type="file" hidden accept=".md,.zip,.json" onChange={event => void previewUpload(event.target.files?.[0])} />
          <PurrButton disabled={busy} icon={<ImportIcon />} onClick={() => input.current?.click()}>导入</PurrButton>
          <PurrButton type="primary" icon={<PlusIcon />} onClick={beginCreate}>新建{kind === 'technique' ? '技法' : '方案'}</PurrButton>
        </div>
      </div>
      <div className="writing-methods-filter-row"><nav aria-label="技法库分类"><PurrSegmented<TechniqueKind> value={kind} onChange={setKind} options={[{value: 'technique', label: '写作技法'}, {value: 'scheme', label: '写作方案'}]} /></nav>
        <div className="writing-methods-status-filter"><PurrSegmented value={libraryStatus} onChange={setLibraryStatus} options={[{value: 'active', label: '库中'}, {value: 'archived', label: '已归档'}]} /><span className="writing-methods-count">{loading ? '正在读取…' : `共 ${visibleObjects.length} 项`}</span></div>
      </div>
      <div className="writing-library-stage" aria-busy={loading}>{loading ? <div className="writing-methods-loading" role="status"><PurrSpin /><span>正在读取{kindLabel}…</span></div> : !visibleObjects.length ?
        <PurrEmpty className="writing-library-empty" image={kind === 'technique' ? <FileTextIcon /> : <ReadIcon />} description={<><h2>{libraryStatus === 'archived' ? `暂无已归档${kindLabel}` : `还没有${kindLabel}`}</h2><p>{libraryStatus === 'archived' ? '归档内容会保留在这里，可恢复或永久删除。' : kind === 'technique' ? '把节奏、对白与铺垫等写法整理下来，留给下一次创作。' : '将已有技法组合起来，为不同场景安排它们的分工与配合。'}</p></>}>
          {libraryStatus === 'active' ? <><PurrButton type="primary" icon={<PlusIcon />} onClick={beginCreate}>创建第一个{kind === 'technique' ? '技法' : '方案'}</PurrButton>
          <PurrButton type="text" disabled={busy} onClick={() => input.current?.click()}>或导入已有内容</PurrButton></> : <PurrButton onClick={() => setLibraryStatus('active')}>返回库中</PurrButton>}
        </PurrEmpty> :
        <div className="writing-method-card-grid">{visibleObjects.map(object => <PurrButton ref={object.id === highlightedId ? locatedCard : undefined} className={`writing-technique-card${object.status === 'archived' ? ' is-archived' : ''}${object.id === highlightedId ? ' is-located' : ''}`} key={object.id} onClick={() => void open(kind, object.id)}>
          <span className="writing-technique-card-meta"><span className="writing-technique-card-icon" aria-hidden>{kind === 'technique' ? <FileTextIcon /> : <ReadIcon />}</span><span className={`writing-technique-card-status${object.status !== 'archived' && object.publishedHead ? ' is-published' : ''}`}>{object.status === 'archived' ? '已归档' : object.publishedHead ? '已发布' : '草稿'}</span></span>
          <strong>{object.metadata?.name || '未命名草稿'}</strong><span className="writing-technique-card-description">{object.metadata?.description || '添加用途说明，方便在创作时选择。'}</span><span className="writing-technique-card-footer">{object.status === 'archived' || object.publishedHead ? '查看内容' : '继续编辑'}<ArrowRightIcon /></span>
        </PurrButton>)}</div>}</div>
    </main>
    <PurrModal open={creating} title={`新建写作${kind === 'technique' ? '技法' : '方案'}`} onCancel={() => setCreating(false)} onOk={create} confirmLoading={busy} okText="创建草稿" okButtonProps={{ disabled: !name.trim() || !description.trim() }}>
      <div className="writing-create-form"><label>名称<PurrInput value={name} maxLength={120} onChange={event => { operation.current = techniqueOperationId(); setName(event.target.value) }} /></label>
        <label>用途与适用时机<PurrInput.TextArea value={description} maxLength={1000} onChange={event => { operation.current = techniqueOperationId(); setDescription(event.target.value) }} /></label></div>
    </PurrModal>
    <PurrModal open={Boolean(preview)} title="导入预览" onCancel={() => setPreview(null)} onOk={importFiles} confirmLoading={busy} okText="存为新技法草稿">
      <p>{preview?.entryRenamed ? '此 Markdown 将作为入口 SKILL.md。' : '将保留以下文件及目录。'}</p><p>保存后可以编辑入口的名称、用途及正文，再发布使用。</p>
      <ul>{Object.keys(preview?.files || {}).map(path => <li key={path}>{path}</li>)}</ul>
    </PurrModal>
    <PurrModal open={Boolean(schemePreview)} title="导入写作方案" onCancel={() => setSchemePreview(null)} okText="保存方案与成员草稿" confirmLoading={busy} onOk={async () => {
      if (!schemePreview) return
      setBusy(true)
      try {
        const draft = requireTechniqueData(await services.writingTechniques.importScheme(schemePreview, operation.current))
        setSchemePreview(null); setKind('scheme'); setLibraryStatus('active'); await reload(); await open('scheme', draft.schemeId!)
      } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
    }}><p>{schemePreview?.scheme.name} · {schemePreview?.techniques.length} 个成员技法</p><p>导入后逐一审核并发布成员，再发布方案。</p></PurrModal>
    {selected?.kind === 'technique' && <TechniqueEditor object={selected} onClose={() => setSelected(null)} onChanged={reload} />}
    {selected?.kind === 'scheme' && <SchemeEditor object={selected} techniques={techniques} onClose={() => setSelected(null)} onChanged={reload} />}
  </div>
}

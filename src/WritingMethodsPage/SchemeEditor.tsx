import React from 'react'
import DeleteLibraryObject from './DeleteLibraryObject'
import { services } from '@/services'
import { PlusIcon, PurrButton, PurrEmpty, PurrInput, PurrModal, PurrSelect, usePurrConfirm, usePurrToast } from '@/purr-components'
import { techniqueOperationId, type TechniqueObject, type SchemeContent } from '../services/writingTechniques'
import { requireTechniqueData } from './operations'
import TechniqueEditor from './TechniqueEditor'

export default function SchemeEditor({ object, techniques, onClose, onChanged }: { object: TechniqueObject; techniques: TechniqueObject[]; onClose: () => void; onChanged: () => Promise<void> }) {
  const toast = usePurrToast()
  const confirm = usePurrConfirm()
  const [record, setRecord] = React.useState(object)
  const [draft, setDraft] = React.useState(object.draft!)
  const [content, setContent] = React.useState<SchemeContent>(object.draft!.content!)
  const [busy, setBusy] = React.useState(false)
  const [reviewMember, setReviewMember] = React.useState<TechniqueObject | null>(null)
  const [choosing, setChoosing] = React.useState(false)
  const [search, setSearch] = React.useState('')
  const [creatingTechnique, setCreatingTechnique] = React.useState(false)
  const [techniqueName, setTechniqueName] = React.useState('')
  const [techniqueDescription, setTechniqueDescription] = React.useState('')
  const createOperation = React.useRef(techniqueOperationId())
  const operation = React.useRef(techniqueOperationId())
  const [version, setVersion] = React.useState('draft')
  const editing = version === 'draft' && draft.state === 'editing' && record.status === 'active'
  const dirty = editing && JSON.stringify(content) !== JSON.stringify(draft.content)
  const close = async () => {
    if (busy) return
    if (dirty && await confirm({title: '放弃未保存的方案修改？', content: '已保存的草稿会保留。', confirmText: '放弃修改'}) !== 'confirm') return
    onClose()
  }
  const change = (next: SchemeContent) => { operation.current = techniqueOperationId(); setContent(next) }
  const availableTechniques = techniques.filter(t => t.status === 'active' && !content.members.some(m => m.id === t.id) && `${t.metadata?.name || ''} ${t.metadata?.description || ''}`.includes(search.trim()))
  const openTechnique = async (id: string) => {
    setBusy(true)
    try { setReviewMember(requireTechniqueData(await services.writingTechniques.get('technique', id))) }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const action = async (publish: boolean) => {
    setBusy(true)
    try {
      let current = draft
      if (editing) current = requireTechniqueData(await services.writingTechniques.updateScheme(record.id, draft, content, `${operation.current}:save:${draft.draftRevision}`))
      setDraft(current)
      if (publish) {
        if (current.state === 'editing') current = requireTechniqueData(await services.writingTechniques.seal('scheme', record.id, current, `${operation.current}:seal:${current.draftRevision}`))
        setDraft(current)
        setRecord(requireTechniqueData(await services.writingTechniques.publish(record, current.sealedRef!, `${operation.current}:publish`)))
      }
      operation.current = techniqueOperationId(); await onChanged(); toast.success(publish ? '方案已发布' : '草稿已保存')
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const editAgain = async () => {
    setBusy(true)
    try { setDraft(requireTechniqueData(await services.writingTechniques.createScheme(content, operation.current, record.id))); setVersion('draft'); operation.current = techniqueOperationId() }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  return <><PurrModal open title="写作方案" width={840} onCancel={() => void close()} className="writing-technique-modal" footer={<>
    <PurrButton disabled={busy || dirty} onClick={async () => { if (record.status === 'active' && await confirm({title: '归档此方案？', content: '归档后移入已归档列表并停止用于写作，成员技法保留。恢复后需要重新配置授权。', confirmText: '归档'}) !== 'confirm') return; setBusy(true); try { setRecord(requireTechniqueData(await services.writingTechniques.setStatus(record, record.status === 'active' ? 'archived' : 'active', techniqueOperationId()))); await onChanged() } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) } }}>{record.status === 'active' ? '归档' : '恢复到库中'}</PurrButton>
    {record.status === 'archived' && <DeleteLibraryObject object={record} disabled={busy} onDeleted={async () => { onClose(); await onChanged() }} />}
    {record.publishedHead && <a href={services.writingTechniques.exportSchemeUrl(record.id, version === 'draft' ? record.publishedHead : version)} download>导出方案及成员</a>}
    {!editing && record.status === 'active' && <PurrButton disabled={busy} onClick={() => void editAgain()}>编辑新版本</PurrButton>}
    {editing && <PurrButton disabled={busy} onClick={() => void action(false)}>保存草稿</PurrButton>}
    <PurrButton type="primary" disabled={busy || version !== 'draft' || record.status !== 'active' || !content.members.length} onClick={() => void action(true)}>发布方案</PurrButton>
  </>}><div className="writing-create-form">
    <PurrSelect value={version} disabled={busy || dirty} options={[{value: 'draft', label: '当前草稿'}, ...(record.publishedVersions || []).map(value => ({value, label: `已发布 · ${value.slice(0, 8)}`}))]} onChange={async next => {
      try { setContent(next === 'draft' ? draft.content! : requireTechniqueData(await services.writingTechniques.schemeVersion(record.id, next))); setVersion(next) }
      catch (error) { toast.error((error as Error).message) }
    }} />
    <label>名称<PurrInput value={content.name} disabled={!editing || busy} onChange={e => change({ ...content, name: e.target.value })} /></label>
    <label>用途<PurrInput value={content.description} disabled={!editing || busy} onChange={e => change({ ...content, description: e.target.value })} /></label>
    <label>组合说明<PurrInput.TextArea value={content.composition} disabled={!editing || busy} placeholder="说明不同技法何时配合，以及有差异时如何取舍。" onChange={e => change({ ...content, composition: e.target.value })} /></label>
    <div className="writing-scheme-members"><div className="writing-scheme-member-heading"><strong>已选技法 · {content.members.length}</strong>{editing && <PurrButton icon={<PlusIcon />} disabled={busy} onClick={() => { setSearch(''); setChoosing(true) }}>添加写作技法</PurrButton>}</div>
      {!content.members.length && <PurrEmpty description="还没有添加技法。选择需要配合使用的写作技法，再填写上方的组合说明。">{editing && <PurrButton type="primary" disabled={busy} onClick={() => { setSearch(''); setChoosing(true) }}>选择写作技法</PurrButton>}</PurrEmpty>}
      {content.members.map((ref, index) => <div className="writing-scheme-member-row" key={ref.id}><strong>{techniques.find(t => t.id === ref.id)?.metadata?.name || '不可用技法'}</strong>
        {editing && techniques.find(t => t.id === ref.id)?.publishedVersions?.includes(ref.versionId) !== true && <PurrButton size="small" disabled={busy} onClick={async () => {
          setBusy(true)
          try {
            const member = requireTechniqueData(await services.writingTechniques.get('technique', ref.id))
            if (member.draft?.sealedRef?.versionId !== ref.versionId) throw new Error('请在技法库中审核并发布此成员的对应版本')
            setReviewMember(member)
          } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
        }}>审阅此成员</PurrButton>}
        <PurrSelect size="small" value={ref.versionId} disabled={!editing || busy} options={[...new Set([ref.versionId, ...(techniques.find(t => t.id === ref.id)?.publishedVersions || [])])].map(value => ({value, label: value.slice(0, 8)}))} onChange={versionId => change({...content, members: content.members.map(member => member.id === ref.id ? {...member, versionId} : member)})} />
        <PurrButton size="small" aria-label={`上移${techniques.find(t => t.id === ref.id)?.metadata?.name || '技法'}`} disabled={!editing || busy || index === 0} onClick={() => { const members = [...content.members]; [members[index - 1], members[index]] = [members[index], members[index - 1]]; change({ ...content, members }) }}>上移</PurrButton>
        <PurrButton size="small" aria-label={`移除${techniques.find(t => t.id === ref.id)?.metadata?.name || '技法'}`} disabled={!editing || busy} onClick={() => change({ ...content, members: content.members.filter(m => m.id !== ref.id) })}>移除</PurrButton>
      </div>)}
    </div>
  </div></PurrModal>
  <PurrModal open={choosing} title="选择写作技法" onCancel={() => setChoosing(false)} footer={<><PurrButton disabled={busy} onClick={() => { setTechniqueName(''); setTechniqueDescription(''); createOperation.current = techniqueOperationId(); setCreatingTechnique(true) }}>新建写作技法</PurrButton><PurrButton type="primary" onClick={() => setChoosing(false)}>完成选择</PurrButton></>}>
    <PurrInput aria-label="搜索写作技法" placeholder="搜索名称或用途" value={search} onChange={e => setSearch(e.target.value)} />
    <p className="writing-scheme-choice-hint">选择已发布的技法加入方案。草稿可先编辑并发布，再回来添加。</p>
    <div className="writing-scheme-choices">{availableTechniques.map(technique => <div className="writing-scheme-choice" key={technique.id}><div><strong>{technique.metadata?.name || '未命名技法'}</strong><p>{technique.metadata?.description || '暂无用途说明'}</p><span>{technique.publishedHead ? '已发布' : '草稿 · 尚未发布'}</span></div>
      {technique.publishedHead ? <PurrButton disabled={busy || content.members.length >= 64} onClick={() => change({ ...content, members: [...content.members, { kind: 'technique', id: technique.id, versionId: technique.publishedHead! }] })}>加入方案</PurrButton> : <PurrButton disabled={busy} onClick={() => void openTechnique(technique.id)}>编辑并发布</PurrButton>}
    </div>)}</div>
    {!availableTechniques.length && <PurrEmpty description={search.trim() ? '没有匹配的写作技法。' : techniques.length ? '现有可选技法已全部加入方案。' : '技法库还是空的，可以在这里新建第一个写作技法。'} />}
  </PurrModal>
  <PurrModal open={creatingTechnique} title="新建写作技法" onCancel={() => setCreatingTechnique(false)} okText="创建并编辑" confirmLoading={busy} okButtonProps={{ disabled: !techniqueName.trim() || !techniqueDescription.trim() }} onOk={async () => {
    setBusy(true)
    try {
      const created = requireTechniqueData(await services.writingTechniques.importFiles({ 'SKILL.md': `---\nname: ${JSON.stringify(techniqueName)}\ndescription: ${JSON.stringify(techniqueDescription)}\n---\n\n` }, createOperation.current))
      setReviewMember(requireTechniqueData(await services.writingTechniques.get('technique', created.techniqueId!)))
      setCreatingTechnique(false); await onChanged()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }}><div className="writing-create-form"><label>技法名称<PurrInput value={techniqueName} onChange={e => { createOperation.current = techniqueOperationId(); setTechniqueName(e.target.value) }} /></label><label>用途与适用时机<PurrInput.TextArea value={techniqueDescription} onChange={e => { createOperation.current = techniqueOperationId(); setTechniqueDescription(e.target.value) }} /></label></div></PurrModal>
  {reviewMember && <TechniqueEditor object={reviewMember} onClose={() => { setReviewMember(null); void onChanged() }} onChanged={onChanged} />}</>
}

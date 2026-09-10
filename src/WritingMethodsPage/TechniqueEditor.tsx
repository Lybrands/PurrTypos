import React from 'react'
import DeleteLibraryObject from './DeleteLibraryObject'
import { useNavigate } from 'react-router-dom'
import { services } from '@/services'
import { FolderIcon, PurrButton, PurrInput, PurrModal, PurrSelect, usePurrConfirm, usePurrToast } from '@/purr-components'
import { techniqueOperationId, type TechniqueFileChange, type TechniqueObject } from '../services/writingTechniques'
import { putTechniqueFile, requireTechniqueData } from './operations'
import TechniqueFileTree from '../components/TechniqueFileTree'

export default function TechniqueEditor({ object, onClose, onChanged }: { object: TechniqueObject; onClose: () => void; onChanged: () => Promise<void> }) {
  const toast = usePurrToast()
  const navigate = useNavigate()
  const confirm = usePurrConfirm()
  const close = async () => {
    if (busy) return
    if (changes.length && await confirm({title: "放弃未保存的修改？", content: "已保存的草稿会保留。", confirmText: "放弃修改"}) !== "confirm") return
    onClose()
  }
  const [record, setRecord] = React.useState(object)
  const [draft, setDraft] = React.useState(object.draft!)
  const [path, setPath] = React.useState('SKILL.md')
  const [folder, setFolder] = React.useState<string | null>(null)
  const [cache, setCache] = React.useState<Record<string, string>>({})
  const [changes, setChanges] = React.useState<TechniqueFileChange[]>([])
  const [busy, setBusy] = React.useState(false)
  const [version, setVersion] = React.useState('draft')
  const operation = React.useRef(techniqueOperationId())
  const editing = version === 'draft' && draft.state === 'editing' && record.status === 'active'
  const [versionPaths, setVersionPaths] = React.useState<string[]>([])
  const paths = React.useMemo(() => {
    const result = new Set((draft.manifest?.files || []).map(f => f.path))
    for (const change of changes) {
      if (change.action === 'put') result.add(change.path)
      else { result.delete(change.path); if (change.target) result.add(change.target) }
    }
    return [...result].sort()
  }, [draft, changes])
  React.useEffect(() => {
    if (path in cache) return
    let current = true
    const load = async () => {
      try {
        if (version === 'draft' && !(draft.manifest?.files || []).some(f => f.path === path)) return
        const result = version === 'draft'
          ? await services.writingTechniques.readDraftFile(record.id, draft, path)
          : await services.writingTechniques.readVersionFile(record.id, version, path)
        const file = requireTechniqueData(result)
        if (current) setCache(previous => ({ ...previous, [path]: file.content }))
      } catch (error) { if (current) toast.error((error as Error).message) }
    }
    void load()
    return () => { current = false }
  }, [path, cache, draft, record.id, version, toast])
  const modify = (content: string) => {
    operation.current = techniqueOperationId(); setCache(previous => ({ ...previous, [path]: content }))
    setChanges(previous => putTechniqueFile(previous, path, content))
  }
  const save = async (publish: boolean) => {
    setBusy(true)
    try {
      let current = draft
      if (changes.length) current = requireTechniqueData(await services.writingTechniques.changeFiles(record.id, current, changes, `${operation.current}:save:${current.draftRevision}`))
      setDraft(current); setChanges([])
      if (publish) {
        if (current.state === 'editing') current = requireTechniqueData(await services.writingTechniques.seal('technique', record.id, current, `${operation.current}:seal:${current.draftRevision}`))
        setDraft(current)
        setRecord(requireTechniqueData(await services.writingTechniques.publish(record, current.sealedRef!, `${operation.current}:publish`)))
      }
      operation.current = techniqueOperationId(); await onChanged(); toast.success(publish ? '技法已发布' : '草稿已保存')
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const editAgain = async () => {
    setBusy(true)
    try {
      const ref = version !== 'draft' ? { kind: 'technique' as const, id: record.id, versionId: version } : draft.sealedRef
      if (!ref) throw new Error('此草稿没有可复制的完整版本')
      setDraft(requireTechniqueData(await services.writingTechniques.createDraft(operation.current, record.id, ref)))
      setVersion('draft'); setCache({}); setChanges([]); setPath('SKILL.md'); setFolder(null); operation.current = techniqueOperationId()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const switchVersion = async (next: string) => {
    try {
      if (next !== 'draft') setVersionPaths(requireTechniqueData(await services.writingTechniques.versionManifest(record.id, next)).files.map(f => f.path))
      setVersion(next); setCache({}); setPath('SKILL.md'); setFolder(null)
    } catch (error) { toast.error((error as Error).message) }
  }
  const changeStatus = async () => {
    if (record.status === 'active' && await confirm({ title: '归档此技法？', content: '归档后移入已归档列表并停止用于写作，相关运行可能会停止。可以恢复，但原有授权需要重新配置。', confirmText: '归档' }) !== 'confirm') return
    setBusy(true)
    try { setRecord(requireTechniqueData(await services.writingTechniques.setStatus(record, record.status === 'active' ? 'archived' : 'active', `${operation.current}:status`))); operation.current = techniqueOperationId(); await onChanged() }
    catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const childPath = (parent: string, name: string) => parent && parent !== '/' ? `${parent}/${name}` : name
  const validateNodeName = (name: string) => {
    if (!name || /[\\/:<>"|?*\x00-\x1f]/.test(name) || /[ .]$/.test(name)) throw new Error('请输入有效名称，名称不能包含路径分隔符')
  }
  const createNode = async (kind: 'file' | 'folder', parent: string, name: string) => {
    validateNodeName(name)
    const folderPath = childPath(parent, name)
    const target = kind === 'folder' ? `${folderPath}/说明.md` : folderPath
    if (paths.some(existing => existing.toLocaleLowerCase() === target.toLocaleLowerCase() || existing.toLocaleLowerCase().startsWith(`${folderPath.toLocaleLowerCase()}/`))) throw new Error('当前目录已存在同名节点')
    operation.current = techniqueOperationId()
    setChanges(previous => [...previous, { action: 'put', path: target, content: '' }]); setCache(previous => ({ ...previous, [target]: '' })); setPath(target); setFolder(null)
  }
  const renameNode = async (source: string, name: string, isFolder: boolean) => {
    validateNodeName(name)
    const parent = source.split('/').slice(0, -1).join('/')
    const rootTarget = childPath(parent, name)
    const moving = paths.filter(item => item === source || isFolder && item.startsWith(`${source}/`))
    const targets = moving.map(item => isFolder ? `${rootTarget}${item.slice(source.length)}` : rootTarget)
    if (targets.some(item => paths.some(existing => !moving.includes(existing) && existing.toLocaleLowerCase() === item.toLocaleLowerCase()))) throw new Error('当前目录已存在同名节点')
    operation.current = techniqueOperationId()
    setChanges(previous => [...previous, ...moving.map((item, index) => ({ action: 'move' as const, path: item, target: targets[index] }))])
    setCache(previous => {
      const next = { ...previous }
      moving.forEach((item, index) => { if (item in next) { next[targets[index]] = next[item]; delete next[item] } })
      return next
    })
    setPath(targets[0] || 'SKILL.md'); setFolder(null)
  }
  const deleteNode = async (target: string, isFolder: boolean) => {
    const targets = paths.filter(item => item === target || isFolder && item.startsWith(`${target}/`))
    if (!targets.length || await confirm({ title: isFolder ? '删除此文件夹？' : '删除此文件？', content: isFolder ? `将同时删除其中的 ${targets.length} 个文件。` : target, confirmText: '删除' }) !== 'confirm') return
    operation.current = techniqueOperationId()
    setChanges(previous => [...previous, ...targets.map(item => ({ action: 'delete' as const, path: item }))])
    setCache(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => !targets.includes(key))))
    if (targets.includes(path)) setPath('SKILL.md')
  }
  return <><PurrModal open title={record.metadata?.name || draft.manifest?.metadata?.name || '写作技法'} width={1080} className="writing-technique-modal" onCancel={() => void close()}
    footer={<>
      <PurrButton disabled={busy || Boolean(changes.length)} onClick={() => void changeStatus()}>{record.status === 'archived' ? '恢复到库中' : '归档'}</PurrButton>
    {record.status === 'archived' && <DeleteLibraryObject object={record} disabled={busy} onDeleted={async () => { onClose(); await onChanged() }} />}
      {record.publishedHead && <a href={services.writingTechniques.exportUrl(record.id, version === 'draft' ? record.publishedHead : version)} download>导出 ZIP</a>}
      {(!editing && record.status === 'active') && <PurrButton disabled={busy} onClick={() => void editAgain()}>编辑新版本</PurrButton>}
      {editing && <PurrButton disabled={busy || !changes.length} onClick={() => void save(false)}>保存草稿</PurrButton>}
      {version === 'draft' && record.status === 'active' && draft.state !== 'cancelled' && <PurrButton type="primary" disabled={busy} onClick={() => void save(true)}>发布技法</PurrButton>}
    </>}>
    <div className="writing-technique-editor">
      {draft.owner?.sourceRevisionId && <PurrButton type="text" disabled={busy || Boolean(changes.length)} onClick={async () => {
        try {
          const revision = requireTechniqueData(await services.novelSources.getRevision({revisionId: draft.owner!.sourceRevisionId!}))
          navigate(`/novel-sources/${revision.work_id}`)
        } catch (error) { toast.error((error as Error).message) }
      }}>查看来源及分析记录</PurrButton>}
      <div className="writing-technique-editor-toolbar"><PurrSelect value={version} disabled={busy || Boolean(changes.length)} onChange={next => void switchVersion(next)} options={[
        { value: 'draft', label: draft.state === 'editing' ? '当前草稿' : '当前封存稿' },
        ...(record.publishedVersions || []).map(v => ({ value: v, label: `已发布 · ${v.slice(0, 8)}` })),
      ]} /><PurrButton disabled={busy || Boolean(changes.length)} onClick={async () => { try { const fresh = requireTechniqueData(await services.writingTechniques.get('technique', record.id)); setRecord(fresh); setDraft(fresh.draft!); setCache({}); setVersion('draft'); setPath('SKILL.md'); setFolder(null); operation.current = techniqueOperationId() } catch (error) { toast.error((error as Error).message) } }}>重新载入</PurrButton><span>{record.status === 'archived' ? '已归档 · 只读' : changes.length ? '有未保存修改' : draft.state === 'editing' ? '编辑后保存草稿' : '完整版本只读'}</span></div>
      <div className="writing-technique-files"><nav aria-label="技法文件目录">
        <TechniqueFileTree rootLabel={record.metadata?.name || draft.manifest?.metadata?.name || '技法目录'} paths={version === 'draft' ? paths : versionPaths} selectedPath={folder ?? path} onSelect={next => { setFolder(null); setPath(next) }} onFolderSelect={setFolder}
          fileActions={editing ? { heading: '文件目录', disabled: busy, allowedFileExtensions: ['.md', '.txt'], defaultFileExtension: '.md', onCreateNode: createNode, onRenameNode: (target, name, node) => renameNode(target, name, Boolean(node.children)), onDeleteNode: (target, node) => void deleteNode(target, Boolean(node.children)), canRenameNode: target => target !== '/' && target !== 'SKILL.md', canDeleteNode: target => target !== '/' && target !== 'SKILL.md' } : undefined} />
      </nav>{folder !== null ? <div className="writing-technique-folder-content"><FolderIcon /><h3>{folder === '/' ? '技法根目录' : folder}</h3><p>{editing ? '选择文件进行编辑；新建文件或子目录可使用左侧「文件目录」旁的按钮。' : '选择文件查看内容。'}</p>
        <ul>{(version === 'draft' ? paths : versionPaths).filter(file => folder === '/' || file.startsWith(`${folder}/`)).map(file => <li key={file}><PurrButton type="text" onClick={() => { setFolder(null); setPath(file) }}>{folder === '/' ? file : file.slice(folder.length + 1)}</PurrButton></li>)}</ul>
      </div> : <div className="writing-technique-file-content"><div><strong>{path}</strong></div><PurrInput.TextArea aria-label={`${path} 正文`} value={cache[path] || ''} readOnly={!editing} disabled={busy || !(path in cache)} onChange={e => modify(e.target.value)} placeholder={path === 'SKILL.md' ? '从入口说明这个技法如何使用，按需引用辅助文件。' : '辅助说明'} /></div>}</div>
    </div>
  </PurrModal></>
}

import React from 'react'
import KnowledgeMarkdownEditor from '@/components/KnowledgeMarkdownEditor'
import Markdown from '../components/Markdown'
import TechniqueFileTree from '../components/TechniqueFileTree'
import { services } from '@/services'
import { CloseIcon, EditIcon, PurrButton, PurrModal, PurrSpin, PurrTooltip, SaveIcon, usePurrToast } from '@/purr-components'
import { techniqueOperationId, type TechniqueDraft, type TechniqueFileChange, type TechniqueManifest } from '../services/writingTechniques'
import type { NovelAnalysisArtifact, WritingTechniqueResult } from '../types'

const FRONTMATTER = /^(---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$))/

function splitFrontmatter(content: string) {
  const match = content.match(FRONTMATTER)
  return { prefix: match?.[1] ?? '', body: match ? content.slice(match[1].length) : content }
}

function requireTechniqueData<T>(result: { success: boolean; data?: T; error?: string }): T {
  if (!result.success || result.data == null) throw new Error(result.error || '写作技法操作失败')
  return result.data
}

export function WritingSkillReview({ artifact, onTechniqueResultChange }: {
  artifact: NovelAnalysisArtifact
  onTechniqueResultChange?: (result: WritingTechniqueResult) => void
}) {
  const toast = usePurrToast()
  const result = artifact.techniqueResult
  const candidate = result.candidate
  const [activeCandidate, setActiveCandidate] = React.useState(candidate)
  const [manifest, setManifest] = React.useState<TechniqueManifest | null>(null)
  const [draft, setDraft] = React.useState<TechniqueDraft | null>(null)
  const [editing, setEditing] = React.useState(false)
  const [path, setPath] = React.useState('SKILL.md')
  const [contents, setContents] = React.useState<Record<string, string>>({})
  const [originalContents, setOriginalContents] = React.useState<Record<string, string>>({})
  const [error, setError] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [draftChanged, setDraftChanged] = React.useState(false)
  const [pendingDelete, setPendingDelete] = React.useState<{ target: string; folder: boolean } | null>(null)

  React.useEffect(() => {
    setActiveCandidate(candidate)
    setManifest(null); setDraft(null); setEditing(false); setPath('SKILL.md'); setContents({}); setOriginalContents({}); setError(''); setDraftChanged(false); setPendingDelete(null)
  }, [candidate?.techniqueId, candidate?.versionId, candidate?.draftId])

  React.useEffect(() => {
    if (!activeCandidate || editing) return
    let active = true
    void services.writingTechniques.versionManifest(activeCandidate.techniqueId, activeCandidate.versionId).then(response => {
      if (!active) return
      if (response.success && response.data) setManifest(response.data)
      else setError(response.error || '技法目录读取失败')
    }).catch(error => { if (active) setError(String(error)) })
    return () => { active = false }
  }, [activeCandidate?.techniqueId, activeCandidate?.versionId, editing])

  const files = editing ? draft?.manifest?.files ?? [] : manifest?.files ?? []
  React.useEffect(() => {
    if (!activeCandidate || !files.some(file => file.path === path) || Object.prototype.hasOwnProperty.call(contents, path)) return
    let active = true
    setLoading(true); setError('')
    const request = editing && draft
      ? services.writingTechniques.readDraftFile(activeCandidate.techniqueId, draft, path)
      : services.writingTechniques.readVersionFile(activeCandidate.techniqueId, activeCandidate.versionId, path)
    void request.then(response => {
      if (!active) return
      const file = requireTechniqueData(response)
      setContents(previous => ({ ...previous, [path]: file.content }))
      setOriginalContents(previous => ({ ...previous, [path]: file.content }))
    }).catch(error => { if (active) setError((error as Error).message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [activeCandidate?.techniqueId, activeCandidate?.versionId, contents, draft, editing, files, path])

  const dirtyPaths = React.useMemo(() => Object.keys(contents).filter(key => Object.prototype.hasOwnProperty.call(originalContents, key) && contents[key] !== originalContents[key]), [contents, originalContents])
  const startEditing = async () => {
    if (editing && draft) return draft
    if (!activeCandidate) return null
    setBusy(true)
    try {
      const nextDraft = requireTechniqueData(await services.writingTechniques.createDraft(techniqueOperationId(), activeCandidate.techniqueId, { kind: 'technique', id: activeCandidate.techniqueId, versionId: activeCandidate.versionId }))
      setDraft(nextDraft); setManifest(nextDraft.manifest ?? null); setEditing(true)
      return nextDraft
    } catch (error) { toast.error((error as Error).message); return null } finally { setBusy(false) }
  }
  const cancelEditing = () => {
    setEditing(false); setDraft(null); setContents({}); setOriginalContents({}); setPath('SKILL.md'); setDraftChanged(false); setPendingDelete(null)
  }
  const applyDraftChanges = async (currentDraft: TechniqueDraft, changes: TechniqueFileChange[], excludedDirty: string[] = []) => {
    if (!activeCandidate) return null
    const contentChanges = dirtyPaths.filter(changedPath => !excludedDirty.some(target => changedPath === target || changedPath.startsWith(`${target}/`)))
      .map(changedPath => ({ action: 'put' as const, path: changedPath, content: contents[changedPath] }))
    const nextDraft = requireTechniqueData(await services.writingTechniques.changeFiles(activeCandidate.techniqueId, currentDraft, [...contentChanges, ...changes], techniqueOperationId()))
    setDraft(nextDraft); setManifest(nextDraft.manifest ?? null); setDraftChanged(true)
    return nextDraft
  }
  const saveEditing = async () => {
    if (!activeCandidate || !draft || (!dirtyPaths.length && !draftChanged)) return
    setBusy(true)
    try {
      let nextDraft = draft
      if (dirtyPaths.length) nextDraft = (await applyDraftChanges(draft, [])) ?? draft
      nextDraft = requireTechniqueData(await services.writingTechniques.seal('technique', activeCandidate.techniqueId, nextDraft, techniqueOperationId()))
      const sealedRef = nextDraft.sealedRef
      if (!sealedRef) throw new Error('技法版本封存失败')
      const nextCandidate = { techniqueId: activeCandidate.techniqueId, draftId: nextDraft.draftId, versionId: sealedRef.versionId }
      const nextResult: WritingTechniqueResult = { ...result!, candidate: nextCandidate }
      setActiveCandidate(nextCandidate); setDraft(null); setEditing(false); setManifest(nextDraft.manifest ?? null); setOriginalContents(contents); setDraftChanged(false)
      onTechniqueResultChange?.(nextResult)
      toast.success('技法修改已保存，保存分析结果后会作为续写默认技法')
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const childPath = (parent: string, name: string) => parent && parent !== '/' ? `${parent}/${name}` : name
  const validateNodeName = (name: string) => {
    if (!name || /[\\/:<>"|?*\x00-\x1f]/.test(name) || /[ .]$/.test(name)) throw new Error('请输入有效名称，名称不能包含路径分隔符')
  }
  const createNode = async (kind: 'file' | 'folder', parent: string, name: string) => {
    validateNodeName(name)
    const currentDraft = await startEditing()
    if (!currentDraft) throw new Error('无法创建技法草稿')
    const folderPath = childPath(parent, name)
    const target = kind === 'folder' ? `${folderPath}/说明.md` : folderPath
    if ((currentDraft.manifest?.files ?? []).some(file => file.path.toLocaleLowerCase() === target.toLocaleLowerCase() || file.path.toLocaleLowerCase().startsWith(`${folderPath.toLocaleLowerCase()}/`))) throw new Error('当前目录已存在同名节点')
    const nextDraft = await applyDraftChanges(currentDraft, [{ action: 'put', path: target, content: '' }])
    if (!nextDraft) throw new Error('新建节点失败')
    setContents(previous => ({ ...previous, [target]: '' })); setOriginalContents(previous => ({ ...previous, [target]: '' })); setPath(target)
  }
  const renameNode = async (source: string, name: string, folder: boolean) => {
    validateNodeName(name)
    const currentDraft = await startEditing()
    if (!currentDraft) throw new Error('无法创建技法草稿')
    const parent = source.split('/').slice(0, -1).join('/')
    const rootTarget = childPath(parent, name)
    const currentPaths = (currentDraft.manifest?.files ?? []).map(file => file.path)
    const moving = currentPaths.filter(item => item === source || folder && item.startsWith(`${source}/`))
    const targets = moving.map(item => folder ? `${rootTarget}${item.slice(source.length)}` : rootTarget)
    if (targets.some(item => currentPaths.some(existing => !moving.includes(existing) && existing.toLocaleLowerCase() === item.toLocaleLowerCase()))) throw new Error('当前目录已存在同名节点')
    const nextDraft = await applyDraftChanges(currentDraft, moving.map((item, index) => ({ action: 'move', path: item, target: targets[index] })))
    if (!nextDraft) throw new Error('重命名失败')
    const nextContents = { ...contents }; const nextOriginals = { ...originalContents }
    moving.forEach((item, index) => {
      if (Object.prototype.hasOwnProperty.call(nextContents, item)) { nextContents[targets[index]] = nextContents[item]; delete nextContents[item] }
      if (Object.prototype.hasOwnProperty.call(nextOriginals, item)) { nextOriginals[targets[index]] = nextOriginals[item]; delete nextOriginals[item] }
    })
    setContents(nextContents); setOriginalContents(nextOriginals); setPath(targets[0] || 'SKILL.md')
  }
  const deleteNode = async (target: string, folder: boolean) => {
    if (target === '/' || target === 'SKILL.md') return
    setPendingDelete({ target, folder })
  }
  const applyDelete = async () => {
    if (!pendingDelete) return
    const currentDraft = await startEditing()
    if (!currentDraft) return
    const { target, folder } = pendingDelete
    const targets = (currentDraft.manifest?.files ?? []).map(file => file.path).filter(item => item === target || folder && item.startsWith(`${target}/`))
    if (!targets.length) { setPendingDelete(null); return }
    setBusy(true)
    try {
      await applyDraftChanges(currentDraft, targets.map(item => ({ action: 'delete', path: item })), [target])
      setContents(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => !targets.includes(key))))
      setOriginalContents(previous => Object.fromEntries(Object.entries(previous).filter(([key]) => !targets.includes(key))))
      if (targets.includes(path)) setPath('SKILL.md')
      setPendingDelete(null)
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }

  if (result.status === 'insufficient_material') return <p>{result.reason}</p>
  const content = contents[path] ?? ''
  const { prefix, body } = splitFrontmatter(content)
  const isMarkdown = path.endsWith('.md')
  return <section className={`novel-technique-review${editing ? ' is-editing' : ''}`}>
    {error && <p role="alert" className="novel-technique-error">{error}</p>}
    <div className="novel-technique-files">
      <nav aria-label="写作技法文件目录"><TechniqueFileTree rootLabel="技法文件" paths={files.map(file => file.path)} selectedPath={path} onSelect={setPath} fileActions={{
        heading: '文件目录', disabled: busy,
        allowedFileExtensions: ['.md', '.txt'], defaultFileExtension: '.md',
        onCreateNode: createNode,
        onRenameNode: (target, name, node) => renameNode(target, name, Boolean(node.children)),
        onDeleteNode: (target, node) => void deleteNode(target, Boolean(node.children)),
        canRenameNode: target => target !== '/' && target !== 'SKILL.md', canDeleteNode: target => target !== '/' && target !== 'SKILL.md',
      }} /></nav>
      <article aria-label={path} className="novel-technique-file-content">
        <header>
          <div><strong>{path}</strong>{path === 'SKILL.md' && <span>入口文件</span>}{editing && <em>{dirtyPaths.length ? '有未保存修改' : '编辑中'}</em>}</div>
          <div className="novel-technique-file-actions">
            {editing ? <>
              <PurrTooltip title="取消编辑"><PurrButton type="text" size="small" aria-label="取消编辑" icon={<CloseIcon />} disabled={busy} onClick={cancelEditing} /></PurrTooltip>
              <PurrTooltip title="保存修改"><PurrButton type="text" size="small" aria-label="保存修改" icon={<SaveIcon />} loading={busy} disabled={!dirtyPaths.length && !draftChanged} onClick={() => void saveEditing()} /></PurrTooltip>
            </> : <PurrTooltip title="编辑技法"><PurrButton type="text" size="small" aria-label="编辑技法" icon={<EditIcon />} loading={busy} onClick={() => void startEditing()} /></PurrTooltip>}
          </div>
        </header>
        {loading ? <PurrSpin /> : isMarkdown && editing ? <KnowledgeMarkdownEditor documentKey={`${draft?.draftId ?? 'version'}:${path}`} value={body} ariaLabel={`${path} 正文`} onChange={value => setContents(previous => ({ ...previous, [path]: `${prefix}${value}` }))} /> : isMarkdown ? <div className="novel-technique-markdown"><Markdown>{body}</Markdown></div> : <pre>{content}</pre>}
      </article>
    </div>
    <PurrModal open={Boolean(pendingDelete)} title={pendingDelete?.folder ? '删除文件夹？' : '删除文件？'} onCancel={() => setPendingDelete(null)} onOk={() => void applyDelete()} okText="删除" confirmLoading={busy} okButtonProps={{ danger: true }}>
      <p>{pendingDelete?.folder ? '文件夹内的文件会一并从当前技法草稿中删除。' : pendingDelete?.target}</p>
    </PurrModal>
  </section>
}

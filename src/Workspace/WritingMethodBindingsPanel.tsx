import React from 'react'
import './ContinuationPanels.scss'
import { useNavigate } from 'react-router-dom'
import { services } from '@/services'
import { PurrButton, PurrSelect, PurrSpin, usePurrToast } from '@/purr-components'
import type { EntityId } from '../types'
import type { TechniqueObject, WritingTechniqueGrant } from '../services/writingTechniques'

export default function WritingMethodBindingsPanel({ bookId }: { bookId: EntityId | null }) {
  const navigate = useNavigate()
  const toast = usePurrToast()
  const [grants, setGrants] = React.useState<WritingTechniqueGrant[]>([])
  const [objects, setObjects] = React.useState<TechniqueObject[]>([])
  const [selection, setSelection] = React.useState('')
  const [mode, setMode] = React.useState<'manual' | 'auto'>('manual')
  const [loading, setLoading] = React.useState(true)
  const [busy, setBusy] = React.useState(false)
  const reload = React.useCallback(async () => {
    if (bookId == null) return
    setLoading(true)
    try {
      const results = await Promise.all([services.writingTechniques.grants(String(bookId)), services.writingTechniques.list('technique'), services.writingTechniques.list('scheme'), services.writingTechniques.getMode('book', String(bookId))])
      if (results.some(r => !r.success)) throw new Error('读取写作技法设置失败')
      setGrants(results[0].data || []); setObjects([...(results[1].data || []), ...(results[2].data || [])]); setMode(results[3].data || 'manual')
    } catch (error) { toast.error((error as Error).message) } finally { setLoading(false) }
  }, [bookId, toast])
  React.useEffect(() => { void reload() }, [reload])
  const isGranted = (kind: string, id: string, versionId: string) => grants.some(grant => grant.ref.kind === kind && grant.ref.id === id && grant.ref.versionId === versionId)
  const options = objects.flatMap(object => (object.publishedVersions || []).map(version => ({
    value: `${object.kind}:${object.id}:${version}`, label: `${object.metadata?.name || (object.kind === 'scheme' ? '未命名方案' : '未命名技法')}${(object.publishedVersions || []).length > 1 ? ` · 版本 ${(object.publishedVersions || []).indexOf(version) + 1}` : ''}${isGranted(object.kind, object.id, version) ? ' · 已授权' : ''}`,
    disabled: isGranted(object.kind, object.id, version),
    title: isGranted(object.kind, object.id, version) ? '此版本已授权自动使用，无需重复添加' : undefined,
  })))
  const add = async () => {
    if (bookId == null || !selection) return
    const [kind, id, versionId] = selection.split(':')
    if (isGranted(kind, id, versionId)) return
    setBusy(true)
    try {
      const result = await services.writingTechniques.grant(String(bookId), {kind: kind as 'technique' | 'scheme', id, versionId})
      if (!result.success) throw new Error(result.error || '授权失败')
      setSelection(''); await reload()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }
  const revoke = async (id: string) => {
    if (bookId == null) return
    const result = await services.writingTechniques.revoke(String(bookId), id)
    if (!result.success) return toast.error(result.error || '撤销授权失败')
    await reload()
  }
  const changeMode = async (next: 'manual' | 'auto') => {
    if (bookId == null) return
    const result = await services.writingTechniques.setMode('book', String(bookId), next)
    if (result.success) setMode(next)
    else toast.error(result.error || '保存默认模式失败')
  }
  if (bookId == null) return <p>请先打开一本小说</p>
  if (loading) return <PurrSpin />
  return <div className="writing-method-bindings-panel">
    <div className="writing-method-bindings-header"><div><h2>写作技法</h2><p>管理本书可自动使用的技法与方案。</p></div><PurrButton onClick={() => navigate('/writing-methods')}>打开技法库</PurrButton></div>
    <label>新会话默认模式<PurrSelect value={mode} onChange={changeMode} options={[{value: 'manual', label: '仅手动指定'}, {value: 'auto', label: '允许自动选择'}]} /></label>
    <p>修改默认模式只影响之后创建的会话。</p>
    <div className="writing-method-bindings-add"><PurrSelect value={selection || undefined} onChange={setSelection} options={options} optionRender={({label, data}) => <span title={data.title as string | undefined}>{label}</span>} placeholder="选择允许自动使用的技法或方案版本" /><PurrButton type="primary" disabled={!selection || busy} onClick={() => void add()}>授权自动使用</PurrButton></div>
    <div className="writing-method-binding-list">{grants.length ? grants.map(grant => <article key={grant.grantId}>
      <div className="writing-method-binding-copy"><strong>{grant.metadata.name || objects.find(object => object.id === grant.ref.id)?.metadata?.name || "未命名技法"}</strong><span>{grant.ref.kind === 'scheme' ? '写作方案' : '写作技法'} · 已授权自动使用{grant.available === false ? ' · 已失效，请撤销或重新授权' : ''}</span></div>
      <PurrButton onClick={() => void revoke(grant.grantId)}>撤销授权</PurrButton>
    </article>) : <p>尚未授权自动使用的写作技法或方案。</p>}</div>
  </div>
}

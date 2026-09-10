import React from 'react'
import { PurrButton, PurrCollapse, PurrInput, PurrModal, PurrSelect, PurrSpin, PurrTag } from '@/purr-components'
import type { EntityId } from '../../types'
import { novelKnowledge, type KnowledgeDocument, type KnowledgeLocator, type KnowledgePreview, type KnowledgeScope, type KnowledgeSearch, type KnowledgeSource, type KnowledgeStatus, type KnowledgeUsage } from '../../services/novelKnowledge'
import WorkspaceContext from '../WorkspaceContext'
import './index.scss'
import SharedMaterials from './SharedMaterials'
import Markdown from '../../components/Markdown'

const labels: Record<string, string> = {
  active: '目录可用', unbound: '未连接', unavailable: '目录不可用', reauthorization_required: '需重新选择目录',
  eligible: '已具备检索条件', reference: '待确认使用条件', conflict: '资料标识重复', stale: '解析或来源失效', missing: '文件缺失',
  disabled: '未启用', enabled: '已启用', configuration_changed: '配置已变化，请重新授权',
  not_confirmed: '未确认', future: '尚未到适用章节', expired: '已过适用章节', character_knowledge_unknown: '角色知情范围未知',
  semantic_unavailable: '语义检索不可用，已使用全文检索', chapter_required: '需要选择章节',
  host_ownership_retained: '原资料仍由 PurrTypos 维护', entity_overlap_requires_mapping: '同名或映射重叠，尚未决议',
  formal_metadata_incomplete: '正式资料字段不完整', duplicate_id: '资料标识重复', proposal_directory: '建议目录中的候选',
}
const label = (value: string) => labels[value] || value

export default function KnowledgePanel({ bookId }: { bookId: EntityId | null }) {
  const workspace = React.useContext(WorkspaceContext)
  const book = String(bookId ?? '')
  const [status, setStatus] = React.useState<KnowledgeStatus | null>(null)
  const [documents, setDocuments] = React.useState<KnowledgeDocument[]>([])
  const [preview, setPreview] = React.useState<{ data: KnowledgePreview; token: string } | null>(null)
  const [usage, setUsage] = React.useState<KnowledgeUsage[]>([])
  const [usageLoaded, setUsageLoaded] = React.useState(false)
  const [source, setSource] = React.useState<KnowledgeSource | null>(null)
  const [locator, setLocator] = React.useState<KnowledgeLocator | undefined>()
  const [query, setQuery] = React.useState('')
  const [filter, setFilter] = React.useState('all')
  const [result, setResult] = React.useState<KnowledgeSearch | null>(null)
  const [mode, setMode] = React.useState<'fulltext' | 'hybrid'>('fulltext')
  const [scope, setScope] = React.useState<KnowledgeScope>({ purpose: 'prose' })
  const [busy, setBusy] = React.useState(false)
  const [notice, setNotice] = React.useState('')
  const epoch = React.useRef(0)
  const alive = React.useRef(true)
  React.useEffect(() => { alive.current = true; return () => { alive.current = false; epoch.current++ } }, [])
  const load = React.useCallback(async () => {
    const version = epoch.current
    const response = await novelKnowledge.status(book)
    if (!alive.current || version !== epoch.current) return
    if (!response.success || !response.data) throw new Error(response.error || '资料库状态读取失败')
    setStatus(response.data)
    setScope(response.data.scope?.purpose ? response.data.scope : { purpose: 'prose' })
    if (response.data.state !== 'unbound') {
      const list = await novelKnowledge.documents(book)
      if (alive.current && version === epoch.current && list.success) setDocuments(list.data || [])
    } else setDocuments([])
  }, [book])
  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setNotice('')
    try { await action() } catch (error) { if (alive.current) setNotice(error instanceof Error ? error.message : String(error)) }
    finally { if (alive.current) setBusy(false) }
  }
  React.useEffect(() => {
    epoch.current++; setStatus(null); setDocuments([]); setSource(null); setPreview(null); setResult(null)
    if (book) void load().catch((error: Error) => setNotice(error.message))
  }, [book, load])
  const checked = (response: { success: boolean; error?: string }) => { if (!response.success) throw new Error(response.error || '操作失败') }
  const choose = async () => {
    if (!window.purrDesktop?.selectNovelKnowledge) throw new Error('持续连接本地目录需要使用 Electron 桌面版。')
    const selection = await window.purrDesktop.selectNovelKnowledge(book)
    if (!selection.success || !selection.data) { if (selection.error === 'canceled') return; throw new Error(selection.error || '目录选择失败') }
    const response = await novelKnowledge.preview(book, selection.data.selectionToken)
    checked(response)
    if (response.data) setPreview({ data: response.data, token: selection.data.selectionToken })
  }
  const configure = async (patch: { scope?: KnowledgeScope; semantic?: boolean; unbind?: boolean }) => {
    checked(await novelKnowledge.configure(book, status?.version || 0, patch)); setResult(null); await load()
  }
  const showSource = async (documentId: string, revision?: string, location?: KnowledgeLocator) => {
    setLocator(location)
    const response = await novelKnowledge.source(book, documentId, revision); checked(response); setSource(response.data || null)
  }
  const navigate = async (action: 'open' | 'copy' | 'reveal') => {
    if (!source || !window.purrDesktop?.openNovelKnowledge) return
    const response = await window.purrDesktop.openNovelKnowledge({ bookId: book, documentId: source.id, revision: source.usedRevision, anchor: locator?.blocks?.[0] ? `^${locator.blocks[0]}` : locator?.heading, action })
    checked(response)
    setNotice(action === 'copy' ? '已复制路径' : '')
  }
  const connected = status && status.state !== 'unbound'
  const visible = documents.filter((d) => (filter === 'all' || d.state === filter) && (!query || `${d.title} ${d.path} ${JSON.stringify(d.metadata.aliases || [])}`.includes(query)))
  return <section className="novel-knowledge-panel">
    <header><h2>创作资料库</h2><PurrTag>{label(status?.state || 'unbound')}</PurrTag></header>
    <SharedMaterials key={book} book={book} onChanged={load} />
    {!status?.sharedStorage && <p className="knowledge-note">可连接只读参考笔记。</p>}
    <div className="knowledge-actions">{(!status?.sharedStorage || status.state === 'reauthorization_required') && <PurrButton disabled={busy || !window.purrDesktop?.selectNovelKnowledge || (status?.sharedStorage && status.state !== 'reauthorization_required')} onClick={() => void perform(choose)}>{connected ? '重新选择目录' : '连接资料目录'}</PurrButton>}
      {connected && <><PurrButton size="small" disabled={busy} onClick={() => void perform(async () => { checked(await novelKnowledge.refresh(book)); await load() })}>刷新笔记</PurrButton>{!status?.sharedStorage && <PurrButton disabled={busy} onClick={() => void perform(() => configure({ unbind: true }))}>解除绑定</PurrButton>}</>}
      {connected && <span className="knowledge-library-status">{status.scan?.markdown || 0} 篇笔记 · {status.scanned_at || '尚未扫描'}</span>}
      {busy && <PurrSpin size="small" />}</div>
    {!window.purrDesktop && <p>连接本地目录和 Obsidian 跳转需使用桌面版。</p>}
    {notice && <p role="status" className="knowledge-notice">{notice}</p>}
    {preview && <article className="knowledge-preview"><h3>确认读取范围：{preview.data.directory}</h3><p>{preview.data.documents.length} 篇 Markdown；跳过 {Object.entries(preview.data.skipped).map(([k, v]) => `${k} ${v}`).join('、') || '0'}。不迁移或覆盖现有资料。</p>
      <ul>{preview.data.documents.slice(0, 30).map((d) => <li key={d.path}>{d.path} · {d.status}</li>)}</ul>
      {preview.data.documents.length > 30 && <p>仅预览前 30 篇，连接后可查看完整清单。</p>}
      <PurrButton disabled={busy} onClick={() => void perform(async () => { checked(await novelKnowledge.bind(book, preview.token, status?.version || 0)); setPreview(null); await load() })}>确认只读连接</PurrButton><PurrButton onClick={() => setPreview(null)}>取消</PurrButton></article>}
    {connected && <>
      <div className="knowledge-controls knowledge-search"><PurrInput aria-label="检索资料" value={query} placeholder="人名、别名或资料内容" onChange={(e) => setQuery(e.target.value)} /><PurrSelect value={mode} options={[{ value: 'fulltext', label: '精确＋全文' }, { value: 'hybrid', label: '精确＋全文＋语义', disabled: status.semantic.state !== 'enabled' }]} onChange={(value) => setMode(value as typeof mode)} /><PurrButton disabled={busy || !query.trim()} onClick={() => void perform(async () => { const response = await novelKnowledge.search(book, query, mode); checked(response); setResult(response.data || null) })} title="按当前写作范围检索">检索</PurrButton></div>
      {result && <article><h3>本次召回 {result.items.length} 个片段</h3><p>这是检索预览，不代表已提供给模型。预算暂缓 {result.deferred} 个片段。</p>{Object.entries(result.excluded).map(([k, v]) => <PurrTag key={k}>{label(k)} {v}</PurrTag>)}
        {result.items.map((item) => <div key={item.id}><PurrButton onClick={() => void perform(() => showSource(item.documentId, item.revision, item.locator))}>{item.title} · {item.reasons.join(' / ')}</PurrButton></div>)}</article>}
      <div className="knowledge-list-heading"><h3>资料清单 <span>{visible.length}</span></h3><PurrSelect value={filter} options={['all', 'eligible', 'reference', 'conflict', 'stale', 'missing'].map((value) => ({ value, label: value === 'all' ? '全部状态' : label(value) }))} onChange={(value) => setFilter(String(value))} /></div>
      <div className="knowledge-document-list">{visible.map((d) => <PurrButton type="text" className="knowledge-document-row" title={d.title} key={d.id} disabled={!d.revision || busy} onClick={() => void perform(() => showSource(d.id))}>
        <span className="knowledge-document-row__copy"><strong>{d.title}</strong><small>{d.path.split('/').slice(0, -1).join('/') || '资料'}</small></span>
        {['conflict', 'stale', 'missing'].includes(d.state) && <span className="knowledge-document-row__issue">{label(d.state)}</span>}
        <span aria-hidden="true" className="knowledge-document-row__arrow">›</span>
      </PurrButton>)}{!visible.length && <p className="knowledge-empty">{documents.length ? '没有匹配的资料' : '暂无资料'}</p>}</div>
      <PurrCollapse items={[{ key: 'scope', label: '写作范围与资料规范', children: <><p>Agent 优先使用当前写作章节；下方范围用于检索预览和无当前章节的任务。</p>
        <div className="knowledge-controls"><PurrSelect value={scope.purpose} options={[{ value: 'prose', label: '正文写作' }, { value: 'character', label: '人物视角' }, { value: 'discussion', label: '作者设定讨论（可见未来计划）' }]} onChange={(purpose) => setScope({ ...scope, purpose: purpose as KnowledgeScope['purpose'] })} />
          <PurrSelect value={scope.chapterId || ''} options={[{ value: '', label: '跟随当前写作章节' }, ...(workspace?.writingChapters || []).map((c) => ({ value: String(c.id), label: c.title }))]} onChange={(chapterId) => setScope({ ...scope, chapterId: String(chapterId) || undefined })} />
          <PurrInput placeholder="知情人物的 purr_id 或人物卡 ID" value={scope.characterId || ''} onChange={(e) => setScope({ ...scope, characterId: e.target.value || undefined })} />
          <PurrButton disabled={busy} onClick={() => void perform(() => configure({ scope }))}>保存范围</PurrButton></div>
        <p>正式资料需声明状态、章节范围和角色知情范围；重叠的外部笔记不会覆盖项目资料。</p>
      </> }]} />
      <PurrCollapse items={[{ key: 'embedding', label: `Embedding 检索增强：${label(status.semantic.state)}`, children: <><p>配置自备 Embedding 服务后可启用语义检索。</p>
        <div className="knowledge-actions"><PurrButton disabled={busy || (status.semantic.state !== 'enabled' && !status.semantic.model)} onClick={() => void perform(async () => { const enable = status.semantic.state !== 'enabled'; await configure({ semantic: enable }); setMode(enable ? 'hybrid' : 'fulltext') })}>{status.semantic.state === 'enabled' ? '关闭语义检索' : '允许发送并启用语义检索'}</PurrButton>
          {status.semantic.state === 'enabled' && <PurrButton disabled={busy} onClick={() => void perform(async () => { checked(await novelKnowledge.semanticIndex(book)); await load() })}>索引下一批（最多 40 个片段）</PurrButton>}</div>
        {status.semantic.jobs?.map((j) => <p key={j.state}>{j.state}：{j.count} 项，{j.calls} 次调用，输入 Token {j.tokens ?? '未提供'}</p>)}
      </> }]} />
    </>}
    {connected && <PurrCollapse items={[{ key: 'usage', label: '模型调用实际来源记录', children: <><p>读取最近任务实际提供给模型的资料来源。</p><PurrButton disabled={busy} onClick={() => void perform(async () => { const response = await novelKnowledge.usage(book); checked(response); setUsage(response.data || []); setUsageLoaded(true) })}>读取最近调用凭据</PurrButton>
      {usageLoaded && !usage.length && <p>最近调用中没有创作资料输入凭据。</p>}
      {usage.map((u) => <article key={`${u.eventId}:${u.evidenceId}`}><PurrButton onClick={() => void perform(() => showSource(u.documentId, u.revision, u.locator))}>{u.title} · {u.revision.slice(0, 12)}</PurrButton><p>{u.recordedAt} · 调用 {u.eventId}</p><PurrCollapse size="small" items={[{ key: 'scope', label: '本次范围', children: <pre>{JSON.stringify(u.scope, null, 2)}</pre> }]} /></article>)}
    </> }]} />}
    {source && <PurrModal open title={source.title} onCancel={() => setSource(null)} footer={null} width={720} className="knowledge-reader">
      {notice && <p role="status" className="knowledge-reader__notice">{notice}</p>}
      <div className="knowledge-reader__toolbar">
        <span title={source.path}>{source.path}</span>
        {window.purrDesktop?.openNovelKnowledge && <PurrButton size="small" onClick={() => void perform(() => navigate('open'))}>在 Obsidian 打开</PurrButton>}
      </div>
      {!source.currentMatches && <p className="knowledge-reader__notice">文件已更新，当前显示的是当时使用的版本。</p>}
      <Markdown className="knowledge-reader__body">{source.body}</Markdown>

    </PurrModal>}


  </section>
}

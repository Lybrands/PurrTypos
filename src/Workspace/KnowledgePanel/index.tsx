import React from 'react'
import { PurrButton, PurrInput, PurrSelect, PurrSpin, PurrTag } from '@/purr-components'
import type { EntityId } from '../../types'
import { novelKnowledge, type KnowledgeDocument, type KnowledgeLocator, type KnowledgePreview, type KnowledgeScope, type KnowledgeSearch, type KnowledgeSource, type KnowledgeStatus, type KnowledgeUsage } from '../../services/novelKnowledge'
import WorkspaceContext from '../WorkspaceContext'
import './index.scss'

const labels: Record<string, string> = {
  active: '已连接', unbound: '未连接', unavailable: '目录不可用', reauthorization_required: '需重新选择目录',
  eligible: '可按范围使用', reference: '仅参考', conflict: '冲突', stale: '解析或来源失效', missing: '文件缺失',
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
    setNotice(action === 'copy' ? '已复制当前来源路径。' : '已交给操作系统处理；请在本机核对打开位置。')
  }
  const connected = status && status.state !== 'unbound'
  const visible = documents.filter((d) => (filter === 'all' || d.state === filter) && (!query || `${d.title} ${d.path} ${JSON.stringify(d.metadata.aliases || [])}`.includes(query)))
  return <section className="novel-knowledge-panel">
    <header><div><h2>创作资料库</h2><p>在 Obsidian 维护笔记，在写作时按章节与知情范围取用。</p></div><PurrTag>{label(status?.state || 'unbound')}</PurrTag></header>
    <p className="knowledge-note">只读连接本小说的独立目录。画布内容暂不参与 AI 检索，请将需要使用的设定保存为笔记。关系图与 Canvas 在 Obsidian 中使用。</p>
    <div className="knowledge-actions"><PurrButton disabled={busy || !window.purrDesktop?.selectNovelKnowledge} onClick={() => void perform(choose)}>{connected ? '重新选择目录' : '连接资料目录'}</PurrButton>
      {connected && <><PurrButton disabled={busy} onClick={() => void perform(async () => { checked(await novelKnowledge.refresh(book)); await load() })}>刷新笔记</PurrButton><PurrButton disabled={busy} onClick={() => void perform(() => configure({ unbind: true }))}>解除绑定</PurrButton></>}
      {busy && <PurrSpin size="small" />}</div>
    {!window.purrDesktop && <p>本地目录持续连接和 Obsidian 跳转仅在 Electron 桌面版提供。此处可查看已经授权的来源预览。</p>}
    {notice && <p role="status" className="knowledge-notice">{notice}</p>}
    {preview && <article className="knowledge-preview"><h3>确认读取范围：{preview.data.directory}</h3><p>{preview.data.documents.length} 篇 Markdown；跳过 {Object.entries(preview.data.skipped).map(([k, v]) => `${k} ${v}`).join('、') || '0'}。不迁移或覆盖现有资料。</p>
      <ul>{preview.data.documents.slice(0, 30).map((d) => <li key={d.path}>{d.path} · {d.status}</li>)}</ul>
      {preview.data.documents.length > 30 && <p>仅预览前 30 篇，连接后可查看完整清单。</p>}
      <PurrButton disabled={busy} onClick={() => void perform(async () => { checked(await novelKnowledge.bind(book, preview.token, status?.version || 0)); setPreview(null); await load() })}>确认只读连接</PurrButton><PurrButton onClick={() => setPreview(null)}>取消</PurrButton></article>}
    {connected && <>
      <p>{status.directory} · {status.scan?.markdown || 0} 篇笔记 · 上次成功扫描 {status.scanned_at || '尚未完成'}<br />未摄取：{Object.entries(status.scan?.skipped || {}).map(([k, v]) => `${k} ${v}`).join('、') || '无'}</p>
      <details><summary>写作范围与资料规范</summary><p>Agent 优先使用当前写作章节；下方章节用于检索预览及没有当前章节的任务。范围对新 Agent 任务生效；修改范围后请发起新任务。正文模式只使用明确已知的资料，未指定人物时仅使用 known_to 包含 * 的公开资料。旧记忆与设定没有历史/知情投影时暂不注入。</p>
        <div className="knowledge-controls"><PurrSelect value={scope.purpose} options={[{ value: 'prose', label: '正文写作' }, { value: 'character', label: '人物视角' }, { value: 'discussion', label: '作者设定讨论（可见未来计划）' }]} onChange={(purpose) => setScope({ ...scope, purpose: purpose as KnowledgeScope['purpose'] })} />
          <PurrSelect value={scope.chapterId || ''} options={[{ value: '', label: '跟随当前写作章节' }, ...(workspace?.writingChapters || []).map((c) => ({ value: String(c.id), label: c.title }))]} onChange={(chapterId) => setScope({ ...scope, chapterId: String(chapterId) || undefined })} />
          <PurrInput placeholder="知情人物的 purr_id 或人物卡 ID" value={scope.characterId || ''} onChange={(e) => setScope({ ...scope, characterId: e.target.value || undefined })} />
          <PurrButton disabled={busy} onClick={() => void perform(() => configure({ scope }))}>保存范围</PurrButton></div>
        <p>既有人物卡、设定和正文事实保留原所有权。与其重叠的外部笔记仅作参考，不覆盖旧记录。无状态或无范围的笔记可浏览，但不当作正式约束。</p>
        <pre>{'---\npurr_id: world-moon\ntype: world\nstatus: confirmed\ntemporal_scope: global\nknown_to: ["*"]\n---\n月亮每晚呈蓝色。'}</pre><p>变化事实使用 temporal_scope: chapter_range，valid_from_chapter / valid_to_chapter 填本作品稳定章节 ID（起章包含、止章不含）；不同人物的知情时间拆成笔记。</p>
      </details>
      <details><summary>语义检索：{label(status.semantic.state)}</summary><p>复用设置中的 Embedding 配置。启用后，本目录合格笔记内容和搜索词会发送给 {status.semantic.provider || '尚未配置的服务'}，模型 {status.semantic.model || '未配置'}，用于生成检索向量；可能产生费用。文本索引和向量缓存保存在本地。</p>
        <div className="knowledge-actions"><PurrButton disabled={busy} onClick={() => void perform(() => configure({ semantic: status.semantic.state !== 'enabled' }))}>{status.semantic.state === 'enabled' ? '关闭语义检索' : '允许发送并启用语义检索'}</PurrButton>
          {status.semantic.state === 'enabled' && <PurrButton disabled={busy} onClick={() => void perform(async () => { checked(await novelKnowledge.semanticIndex(book)); await load() })}>索引下一批（最多 40 个片段）</PurrButton>}</div>
        {status.semantic.jobs?.map((j) => <p key={j.state}>{j.state}：{j.count} 项，{j.calls} 次调用，输入 Token {j.tokens ?? '未提供'}</p>)}
      </details>
      <div className="knowledge-controls"><PurrInput value={query} placeholder="人名、别名或资料内容" onChange={(e) => setQuery(e.target.value)} /><PurrSelect value={mode} options={[{ value: 'fulltext', label: '精确＋全文' }, { value: 'hybrid', label: '精确＋全文＋语义' }]} onChange={(value) => setMode(value as typeof mode)} /><PurrButton disabled={busy || !query.trim()} onClick={() => void perform(async () => { const response = await novelKnowledge.search(book, query, mode); checked(response); setResult(response.data || null) })}>按写作范围检索</PurrButton></div>
      {result && <article><h3>本次召回 {result.items.length} 个片段</h3><p>这是检索预览，不代表已提供给模型。预算暂缓 {result.deferred} 个片段。</p>{Object.entries(result.excluded).map(([k, v]) => <PurrTag key={k}>{label(k)} {v}</PurrTag>)}
        {result.items.map((item) => <div key={item.id}><PurrButton onClick={() => void perform(() => showSource(item.documentId, item.revision, item.locator))}>{item.title} · {item.reasons.join(' / ')}</PurrButton></div>)}</article>}
      <div className="knowledge-controls"><h3>资料清单</h3><PurrSelect value={filter} options={['all', 'eligible', 'reference', 'conflict', 'stale', 'missing'].map((value) => ({ value, label: value === 'all' ? '全部状态' : label(value) }))} onChange={(value) => setFilter(String(value))} /></div>
      <div className="knowledge-document-list">{visible.map((d) => <article key={d.id}><PurrButton disabled={!d.revision || busy} onClick={() => void perform(() => showSource(d.id))}>{d.title}</PurrButton><PurrTag>{label(d.state)}</PurrTag><small>{d.path}</small>{d.diagnostics.map((reason) => <small key={reason}>{label(reason)}</small>)}</article>)}</div>
    </>}
    {connected && <details><summary>模型调用实际来源记录</summary><p>来自持久化调用输入凭据；检索预览、已索引文件和画布不会计入。展开来源可核对当次版本。</p><PurrButton disabled={busy} onClick={() => void perform(async () => { const response = await novelKnowledge.usage(book); checked(response); setUsage(response.data || []); setUsageLoaded(true) })}>读取最近调用凭据</PurrButton>
      {usageLoaded && !usage.length && <p>最近调用中没有创作资料输入凭据。</p>}
      {usage.map((u) => <article key={`${u.eventId}:${u.evidenceId}`}><PurrButton onClick={() => void perform(() => showSource(u.documentId, u.revision, u.locator))}>{u.title} · {u.revision.slice(0, 12)}</PurrButton><p>{u.recordedAt} · 调用 {u.eventId}</p><details><summary>本次范围</summary><pre>{JSON.stringify(u.scope, null, 2)}</pre></details></article>)}
    </details>}
    {source && <article className="knowledge-source"><header><h3>{source.title}</h3><PurrButton onClick={() => setSource(null)}>关闭预览</PurrButton></header><p>{source.path}</p><p>预览版本 {source.usedRevision.slice(0, 12)} · {source.currentMatches ? '与当前原文件一致' : '当前文件已更新或不可用；以下保留该次索引版本'}</p>
      <p>{locator && `使用位置：${locator.heading || "无标题"}，正文第 ${locator.line || 1} 行${locator.blocks?.length ? `，块 ${locator.blocks.join("、")}` : ""}。跳转先打开原文件，请按此位置查找。`}</p>
      <div className="knowledge-actions">{window.purrDesktop?.openNovelKnowledge && <>{(['open', 'copy', 'reveal'] as const).map((action) => <PurrButton key={action} disabled={busy} onClick={() => void perform(() => navigate(action))}>{({ open: '在 Obsidian 打开当前文件', copy: '复制当前路径', reveal: '在文件管理器定位' })[action]}</PurrButton>)}</>}</div>
      <details><summary>状态、范围与链接</summary><pre>{JSON.stringify(source.usedMetadata, null, 2)}</pre>{source.links.map((l, i) => <p key={i}>{l.target}#{l.anchor} · {l.state}</p>)}</details>
      <pre className="knowledge-source-text">{source.body}</pre></article>}
    <footer>SQLite 备份包含来源修订记录，不包含外部 Vault 原文件；请单独备份 Vault。解除绑定或删除作品不会删除笔记。恢复备份后需重新选择资料目录。</footer>
  </section>
}

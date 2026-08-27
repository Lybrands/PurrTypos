import React from 'react'
import { services } from '@/services'
import { ArrowLeftIcon, CopyIcon, HomeIcon, PlusIcon } from '@/purr-components'
import { PurrButton, PurrCheckbox, PurrChoiceCard, PurrInput, PurrModal, PurrSelect, PurrSpin, PurrTooltip, usePurrToast } from '@/purr-components'
import AppHeader from '../components/AppHeader'
import type {
  WritingMethod,
  WritingMethodRevision,
  WritingMethodType,
  WritingScheme,
} from '../types'
import './index.scss'

interface Props { onBack: () => void; onHome: () => void }

type LibraryTab = 'methods' | 'schemes'

export default function WritingMethodsPage({ onBack, onHome }: Props) {
  const appMessage = usePurrToast()
  const [tab, setTab] = React.useState<LibraryTab>('methods')
  const [methods, setMethods] = React.useState<WritingMethod[]>([])
  const [schemes, setSchemes] = React.useState<WritingScheme[]>([])
  const [selectedMethod, setSelectedMethod] = React.useState<WritingMethod | null>(null)
  const [selectedScheme, setSelectedScheme] = React.useState<WritingScheme | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [createMethodOpen, setCreateMethodOpen] = React.useState(false)
  const [createMethodMode, setCreateMethodMode] = React.useState<'guided' | 'blank'>('guided')
  const [createMethodName, setCreateMethodName] = React.useState('')
  const [createMethodType, setCreateMethodType] = React.useState<WritingMethodType>('technique')
  const [createSchemeOpen, setCreateSchemeOpen] = React.useState(false)
  const [createSchemeName, setCreateSchemeName] = React.useState('')
  const [createSchemeDescription, setCreateSchemeDescription] = React.useState('')
  const [createSchemeMembers, setCreateSchemeMembers] = React.useState<string[]>([])
  const [schemeMethodQuery, setSchemeMethodQuery] = React.useState('')
  const [schemeMethodType, setSchemeMethodType] = React.useState<'all' | WritingMethodType>('all')
  const [creating, setCreating] = React.useState(false)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const [methodResult, schemeResult] = await Promise.all([
        services.writingMethods.listMethods({ includeArchived: true }),
        services.writingMethods.listSchemes({ includeArchived: true }),
      ])
      if (!methodResult.success || !schemeResult.success) throw new Error('加载写作方法库失败')
      setMethods(methodResult.data ?? [])
      setSchemes(schemeResult.data ?? [])
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [appMessage])

  React.useEffect(() => { void reload() }, [reload])

  const openMethod = React.useCallback(async (methodId: string) => {
    const result = await services.writingMethods.getMethod({ methodId })
    if (result.success && result.data) setSelectedMethod(result.data)
  }, [])

  const openScheme = React.useCallback(async (schemeId: string) => {
    const result = await services.writingMethods.getScheme({ schemeId })
    if (result.success && result.data) setSelectedScheme(result.data)
  }, [])

  const createMethod = async () => {
    const name = createMethodName.trim()
    if (!name) return
    setCreating(true)
    const result = await services.writingMethods.createMethod({
      name,
      methodType: createMethodType,
      markdown: createMethodMode === 'guided'
        ? `# ${name}\n\n## 目标\n\n说明这个方法希望改善什么。\n\n## 使用方式\n\n写下可直接执行的创作指导。\n\n## 避免\n\n写下需要避免的做法。`
        : '',
    })
    setCreating(false)
    if (!result.success || !result.data) return appMessage.error(result.error || '创建写作方法失败')
    setCreateMethodOpen(false)
    setCreateMethodName('')
    await reload()
    await openMethod(result.data.id)
  }

  const createScheme = async () => {
    const name = createSchemeName.trim()
    if (!name) return
    setCreating(true)
    const result = await services.writingMethods.createScheme({ name, memberRevisionIds: createSchemeMembers })
    setCreating(false)
    if (!result.success || !result.data) return appMessage.error(result.error || '创建写作方案失败')
    if (createSchemeDescription.trim()) {
      const updateResult = await services.writingMethods.updateSchemeDraft({
        schemeId: result.data.id,
        expectedDraftRevision: result.data.draft_revision,
        name,
        description: createSchemeDescription.trim(),
        memberRevisionIds: createSchemeMembers,
      })
      if (!updateResult.success) appMessage.error(updateResult.error || '方案已创建，但说明保存失败')
    }
    setCreateSchemeOpen(false)
    setCreateSchemeName('')
    setCreateSchemeDescription('')
    setCreateSchemeMembers([])
    setSchemeMethodQuery('')
    setSchemeMethodType('all')
    await reload()
    await openScheme(result.data.id)
  }

  const publishedMethodRevisions = React.useMemo(() => methods.flatMap((method) => (
    method.revisions?.slice(0, 1).map((revision) => ({ method, revision })) ?? []
  )), [methods])
  const filteredMethodRevisions = React.useMemo(() => {
    const query = schemeMethodQuery.trim().toLocaleLowerCase()
    return publishedMethodRevisions.filter(({ method }) => (
      (schemeMethodType === 'all' || method.method_type === schemeMethodType)
      && (!query || method.name.toLocaleLowerCase().includes(query))
    ))
  }, [publishedMethodRevisions, schemeMethodQuery, schemeMethodType])

  return (
    <div className="writing-methods-page">
      <AppHeader
        title="写作方法库"
        left={<div className="library-header-nav">
          <PurrTooltip title="返回首页"><PurrButton type="text" size="small" aria-label="返回首页" icon={<HomeIcon style={{ fontSize: 15 }} />} onClick={onHome} /></PurrTooltip>
          <PurrTooltip title="返回书架"><PurrButton type="text" size="small" aria-label="返回书架" icon={<ArrowLeftIcon style={{ fontSize: 14 }} />} onClick={onBack} /></PurrTooltip>
        </div>}
        showActions
      />
      <main className="writing-methods-main">
        <div className="writing-methods-toolbar">
          <div>
            <span className="writing-methods-eyebrow">WRITING METHODS</span>
            <h1>写作方法库</h1>
            <p>草稿自动保存；只有主动发布才生成不可变版本。</p>
          </div>
          <div className="writing-methods-header-actions">
            {tab === 'methods' ? (
              <PurrButton type="primary" icon={<PlusIcon />} onClick={() => { setCreateMethodMode('guided'); setCreateMethodOpen(true) }}>新建写作方法</PurrButton>
            ) : (
              <PurrButton type="primary" icon={<PlusIcon />} onClick={() => setCreateSchemeOpen(true)}>新建方案</PurrButton>
            )}
          </div>
        </div>
        <nav className="writing-methods-tabs" aria-label="写作方法库分类">
          <button className={tab === 'methods' ? 'active' : ''} onClick={() => setTab('methods')}>写作方法</button>
          <button className={tab === 'schemes' ? 'active' : ''} onClick={() => setTab('schemes')}>写作方案</button>
        </nav>
        {loading ? <div className="writing-methods-loading"><PurrSpin /></div> : (
          <div className="writing-library-stage">
            {tab === 'methods' ? <>
              <section className="writing-method-card-grid" aria-label="写作方法">
                {methods.map((method) => <MethodCard key={method.id} method={method} active={selectedMethod?.id === method.id} onClick={() => void openMethod(method.id)} />)}
              </section>
            </> : <>
              <section className="writing-scheme-card-grid" aria-label="写作方案">
                {schemes.map((scheme) => <SchemeCard key={scheme.id} scheme={scheme} active={selectedScheme?.id === scheme.id} onClick={() => void openScheme(scheme.id)} />)}
              </section>
            </>}
          </div>
        )}
      </main>
      <PurrModal
        title="新建写作方法"
        open={createMethodOpen}
        onOk={() => void createMethod()}
        onCancel={() => { setCreateMethodOpen(false); setCreateMethodName('') }}
        okText="创建方法"
        cancelText="取消"
        confirmLoading={creating}
        okButtonProps={{ disabled: !createMethodName.trim() }}
      >
        <div className="writing-create-form">
          <PurrChoiceCard.Group<'guided' | 'blank'> value={createMethodMode} ariaLabel="创建方式" onChange={(event) => setCreateMethodMode(event.target.value)}>
            <PurrChoiceCard value="guided" title="引导创建" description="预置目标、使用方式和避免事项" />
            <PurrChoiceCard value="blank" title="空白创建" description="从一份空白 Markdown 开始" />
          </PurrChoiceCard.Group>
          <label><span>方法名称</span><PurrInput autoFocus value={createMethodName} maxLength={50} placeholder="例如：有限视角下的信息控制" onChange={(event) => setCreateMethodName(event.target.value)} onPressEnter={() => void createMethod()} /></label>
          <label><span>方法类型</span><PurrSelect<WritingMethodType> value={createMethodType} style={{ width: '100%' }} options={[{ value: 'technique', label: '专项技法' }, { value: 'primary', label: '主风格' }]} onChange={setCreateMethodType} /></label>
        </div>
      </PurrModal>
      <PurrModal
        title="新建写作方案"
        open={createSchemeOpen}
        onOk={() => void createScheme()}
        onCancel={() => { setCreateSchemeOpen(false); setCreateSchemeName(''); setCreateSchemeDescription(''); setCreateSchemeMembers([]); setSchemeMethodQuery(''); setSchemeMethodType('all') }}
        okText="创建方案草稿"
        cancelText="取消"
        confirmLoading={creating}
        okButtonProps={{ disabled: !createSchemeName.trim() }}
        width="min(760px, calc(100vw - 32px))"
        styles={{ body: { maxHeight: 'min(72vh, 680px)', overflowY: 'auto' } }}
      >
        <div className="writing-create-form">
          <p>先创建方案草稿，再从已发布的方法版本中组合成员。作品绑定时，方案会作为一个整体被固定。</p>
          <label><span>方案名称</span><PurrInput autoFocus value={createSchemeName} maxLength={50} placeholder="例如：悬疑长篇基础方案" onChange={(event) => setCreateSchemeName(event.target.value)} onPressEnter={() => void createScheme()} /></label>
          <label><span>方案说明</span><PurrInput value={createSchemeDescription} maxLength={120} placeholder="说明这套方案适合什么创作任务" onChange={(event) => setCreateSchemeDescription(event.target.value)} /></label>
          <div className="writing-create-members">
            <div className="writing-create-members-heading"><span>选择已发布的方法版本</span><small>已选择 {createSchemeMembers.length} 个 · 顺序按选择先后</small></div>
            <div className="writing-create-members-toolbar">
              <PurrInput aria-label="搜索方法名称" value={schemeMethodQuery} placeholder="搜索方法名称" onChange={(event) => setSchemeMethodQuery(event.target.value)} />
              <PurrSelect<'all' | WritingMethodType>
                value={schemeMethodType}
                aria-label="筛选方法类型"
                options={[{ value: 'all', label: '全部类型' }, { value: 'primary', label: '主风格' }, { value: 'technique', label: '专项技法' }]}
                onChange={setSchemeMethodType}
              />
            </div>
            <div className="writing-create-members-list">
              {filteredMethodRevisions.length ? filteredMethodRevisions.map(({ method, revision }) => <PurrCheckbox key={revision.id} checked={createSchemeMembers.includes(revision.id)} onChange={(event) => setCreateSchemeMembers((current) => event.target.checked ? [...current, revision.id] : current.filter((id) => id !== revision.id))}>
                {method.name} · v{revision.version_no} · {method.method_type === 'primary' ? '主风格' : '专项技法'}
              </PurrCheckbox>) : <small>{publishedMethodRevisions.length ? '没有符合筛选条件的方法' : '暂无可组合的已发布方法版本'}</small>}
            </div>
          </div>
        </div>
      </PurrModal>
      <PurrModal
        title={selectedMethod?.name ?? '写作方法详情'}
        open={!!selectedMethod}
        onCancel={() => setSelectedMethod(null)}
        footer={null}
        width="min(900px, calc(100vw - 32px))"
        className="writing-detail-modal"
        styles={{ body: { maxHeight: 'min(72vh, 720px)', overflowY: 'auto' } }}
      >
        {selectedMethod ? <MethodEditor
          key={`${selectedMethod.id}:${selectedMethod.draft_revision}`}
          method={selectedMethod}
          onChange={setSelectedMethod}
          onReload={async () => { await reload(); await openMethod(selectedMethod.id) }}
          onDeleted={async () => { setSelectedMethod(null); await reload() }}
        /> : null}
      </PurrModal>
      <PurrModal
        title={selectedScheme?.name ?? '写作方案详情'}
        open={!!selectedScheme}
        onCancel={() => setSelectedScheme(null)}
        footer={null}
        width="min(820px, calc(100vw - 32px))"
        className="writing-detail-modal"
        styles={{ body: { maxHeight: 'min(72vh, 720px)', overflowY: 'auto' } }}
      >
        {selectedScheme ? <><div className="writing-scheme-binding-note">方案作为整体绑定到作品，内部方法不能在作品绑定层单独替换。</div><SchemeEditor
          key={`${selectedScheme.id}:${selectedScheme.draft_revision}`}
          scheme={selectedScheme}
          methods={methods}
          onChange={setSelectedScheme}
          onReload={async () => { await reload(); await openScheme(selectedScheme.id) }}
          onDeleted={async () => { setSelectedScheme(null); await reload() }}
        /></> : null}
      </PurrModal>
    </div>
  )
}

function MethodCard({ method, active, onClick }: { method: WritingMethod; active: boolean; onClick(): void }) {
  const latest = method.revisions?.[0]
  const versionLabel = latest ? `v${latest.version_no}` : method.current_published_revision_id ? '已发布' : '仅草稿'
  return <button className={`writing-method-card ${method.method_type}${active ? ' active' : ''}`} onClick={onClick}>
    <div><span className="writing-card-kind-text">{method.method_type === 'primary' ? '主风格' : '专项技法'}</span><span className={method.current_published_revision_id ? 'writing-card-status published' : 'writing-card-status'}>{method.current_published_revision_id ? '已发布' : '草稿'}</span></div>
    <strong>{method.name}</strong>
    <footer><span>{method.is_builtin ? '内置 · 只读' : method.source_type === 'copy' ? '我的副本' : '我的方法'}</span><span>{versionLabel}</span></footer>
  </button>
}

function SchemeCard({ scheme, active, onClick }: { scheme: WritingScheme; active: boolean; onClick(): void }) {
  const latest = scheme.revisions?.[0]
  const memberCount = latest?.members.length ?? scheme.draft_member_revision_ids.length
  const versionLabel = latest ? `v${latest.version_no}` : scheme.current_published_revision_id ? '已发布' : '仅草稿'
  return <button className={`writing-scheme-card${active ? ' active' : ''}`} onClick={onClick}>
    <div><span className="writing-card-kind-text">{memberCount} 个准确方法版本</span><span className={scheme.current_published_revision_id ? 'writing-card-status published' : 'writing-card-status'}>{scheme.current_published_revision_id ? '已发布' : '草稿'}</span></div>
    <strong>{scheme.name}</strong>
    <div className="writing-scheme-card-members">{latest?.members.slice(0, 2).map((member) => <span key={member.method_revision_id}>{member.name} v{member.version_no}</span>)}{memberCount > 2 ? <span>＋{memberCount - 2}</span> : null}{!latest?.members.length ? <span>{memberCount ? `${memberCount} 个版本 · 点击查看` : '尚未添加方法版本'}</span> : null}</div>
    <footer><span>{scheme.is_builtin ? '内置 · 只读' : '我的方案'}</span><span>{versionLabel}</span></footer>
  </button>
}

function MethodEditor({ method, onChange, onReload, onDeleted }: {
  method: WritingMethod
  onChange: (method: WritingMethod) => void
  onReload: () => Promise<void>
  onDeleted: () => Promise<void>
}) {
  const appMessage = usePurrToast()
  const [name, setName] = React.useState(method.name)
  const [description, setDescription] = React.useState(method.description)
  const [methodType, setMethodType] = React.useState<WritingMethodType>(method.method_type)
  const [markdown, setMarkdown] = React.useState(method.draft_markdown)
  const [saving, setSaving] = React.useState(false)
  const dirty = name !== method.name || description !== method.description
    || methodType !== method.method_type || markdown !== method.draft_markdown

  React.useEffect(() => {
    if (!dirty || method.is_builtin) return
    const timer = window.setTimeout(async () => {
      setSaving(true)
      try {
        const result = await services.writingMethods.updateMethodDraft({
          methodId: method.id,
          expectedDraftRevision: method.draft_revision,
          name, description, methodType, tags: method.tags, markdown,
          metadata: method.draft_metadata,
        })
        if (!result.success || !result.data) throw new Error(result.error || '保存草稿失败')
        onChange(result.data)
      } catch (error) {
        appMessage.error((error as Error).message)
      } finally {
        setSaving(false)
      }
    }, 700)
    return () => window.clearTimeout(timer)
  }, [appMessage, description, dirty, markdown, method, methodType, name, onChange])

  const publish = async () => {
    if (dirty || saving) return appMessage.info('请等待草稿保存完成')
    const result = await services.writingMethods.publishMethod({ methodId: method.id })
    if (!result.success) return appMessage.error(result.error || '发布失败')
    appMessage.success(`已发布 v${result.data?.version_no}`)
    await onReload()
  }

  const copy = async () => {
    const result = await services.writingMethods.copyMethod({ methodId: method.id })
    if (!result.success) return appMessage.error(result.error || '复制失败')
    appMessage.success('已复制为自定义方法')
    await onReload()
  }

  const remove = async () => {
    if (!window.confirm('删除这个写作方法草稿？已发布且被引用的方法不能删除。')) return
    const result = await services.writingMethods.deleteMethod({ methodId: method.id })
    if (!result.success) return appMessage.error(result.error || '删除失败')
    await onDeleted()
  }

  return <div className="writing-method-editor">
    <div className="writing-method-editor-meta">
      <input value={name} disabled={!!method.is_builtin} onChange={(e) => setName(e.target.value)} aria-label="方法名称" />
      <PurrSelect<WritingMethodType> value={methodType} disabled={!!method.is_builtin} options={[{ value: 'primary', label: '主风格' }, { value: 'technique', label: '专项技法' }]} onChange={setMethodType} />
      <span>{saving ? '保存中…' : dirty ? '待保存' : `草稿 r${method.draft_revision}`}</span>
    </div>
    {method.source_type === 'analysis_candidate' ? <p>来源分析候选 · 原文证据只保存在来源分析档案</p> : null}
    <input className="writing-method-description" value={description} disabled={!!method.is_builtin} onChange={(e) => setDescription(e.target.value)} placeholder="简短说明" />
    <textarea value={markdown} disabled={!!method.is_builtin} onChange={(e) => setMarkdown(e.target.value)} placeholder="自由 Markdown 正文" />
    <div className="writing-method-editor-actions">
      <PurrButton icon={<CopyIcon />} onClick={() => void copy()}>复制</PurrButton>
      {!method.is_builtin ? <PurrButton type="primary" disabled={!markdown.trim() || dirty || saving} onClick={() => void publish()}>主动发布</PurrButton> : null}
      {!method.is_builtin ? <PurrButton type="text" onClick={() => void remove()}>删除</PurrButton> : null}
    </div>
    <VersionHistory revisions={method.revisions ?? []} />
  </div>
}

function SchemeEditor({ scheme, methods, onChange, onReload, onDeleted }: {
  scheme: WritingScheme
  methods: WritingMethod[]
  onChange: (scheme: WritingScheme) => void
  onReload: () => Promise<void>
  onDeleted: () => Promise<void>
}) {
  const appMessage = usePurrToast()
  const [name, setName] = React.useState(scheme.name)
  const [description, setDescription] = React.useState(scheme.description)
  const [members, setMembers] = React.useState(scheme.draft_member_revision_ids)
  const candidateMethodIds = React.useMemo(() => (
    scheme.source_type === 'analysis_candidate' && !scheme.current_published_revision_id
      ? (Array.isArray(scheme.source_ref?.candidateMethodIds)
          ? scheme.source_ref.candidateMethodIds.map(String)
          : [])
      : []
  ), [scheme])
  const [candidateSelection, setCandidateSelection] = React.useState(candidateMethodIds)
  const dirty = name !== scheme.name || description !== scheme.description
    || JSON.stringify(members) !== JSON.stringify(scheme.draft_member_revision_ids)
  const revisions = methods.flatMap((method) => (method.revisions ?? []).slice(0, 1))

  React.useEffect(() => {
    if (!dirty || scheme.is_builtin) return
    const timer = window.setTimeout(async () => {
      const result = await services.writingMethods.updateSchemeDraft({
        schemeId: scheme.id, expectedDraftRevision: scheme.draft_revision,
        name, description, memberRevisionIds: members,
      })
      if (!result.success || !result.data) return appMessage.error(result.error || '保存方案失败')
      onChange(result.data)
    }, 700)
    return () => window.clearTimeout(timer)
  }, [appMessage, description, dirty, members, name, onChange, scheme])

  const publish = async () => {
    if (dirty) return appMessage.info('请等待草稿保存完成')
    const result = await services.writingMethods.publishScheme({ schemeId: scheme.id })
    if (!result.success) return appMessage.error(result.error || '发布失败')
    appMessage.success(`已发布方案 v${result.data?.version_no}`)
    await onReload()
  }

  const copy = async () => {
    const result = await services.writingMethods.copyScheme({ schemeId: scheme.id })
    if (!result.success) return appMessage.error(result.error || '复制失败')
    appMessage.success('已复制为自定义方案')
    await onReload()
  }

  const publishCandidates = async () => {
    if (dirty) return appMessage.info('请等待方案草稿保存完成')
    if (!window.confirm(`一次发布 ${candidateSelection.length} 个候选方法及完整方案？发布后仍不会绑定任何作品。`)) return
    const result = await services.writingMethods.publishCandidateBatch({
      schemeId: scheme.id,
      methodIds: candidateSelection,
    })
    if (!result.success) return appMessage.error(result.error || '候选方案原子发布失败')
    appMessage.success('候选方法和方案已发布；尚未绑定任何作品')
    await onReload()
  }

  const remove = async () => {
    if (!window.confirm('删除这个写作方案草稿？已发布且被作品引用的方案不能删除。')) return
    const result = await services.writingMethods.deleteScheme({ schemeId: scheme.id })
    if (!result.success) return appMessage.error(result.error || '删除失败')
    await onDeleted()
  }

  return <div className="writing-method-editor">
    <div className="writing-method-editor-meta">
      <input value={name} disabled={!!scheme.is_builtin} onChange={(e) => setName(e.target.value)} aria-label="方案名称" />
      <span>{dirty ? '待保存' : `草稿 r${scheme.draft_revision}`}</span>
    </div>
    <input className="writing-method-description" value={description} disabled={!!scheme.is_builtin} onChange={(e) => setDescription(e.target.value)} placeholder="方案说明" />
    {candidateMethodIds.length ? <div className="writing-scheme-members">
      <h3>待审核候选方法</h3>
      {candidateMethodIds.map((methodId) => {
        const method = methods.find((item) => item.id === methodId)
        if (!method) return null
        return <PurrCheckbox key={methodId} checked={candidateSelection.includes(methodId)} onChange={(event) => setCandidateSelection((current) => event.target.checked ? [...current, methodId] : current.filter((id) => id !== methodId))}>
          {method.name} · 草稿 r{method.draft_revision}
        </PurrCheckbox>
      })}
      <p>可逐项编辑或删除；这里只决定本次原子发布包含哪些候选。</p>
    </div> : null}
    <div className="writing-scheme-members">
      <h3>有序方法版本</h3>
      {revisions.map((revision) => <PurrCheckbox
        key={revision.id}
        disabled={!!scheme.is_builtin}
        checked={members.includes(revision.id)}
        onChange={(event) => setMembers((current) => event.target.checked
            ? [...current, revision.id]
            : current.filter((id) => id !== revision.id))}
      >{revision.name} · v{revision.version_no}</PurrCheckbox>)}
    </div>
    <div className="writing-method-editor-actions">
      <PurrButton icon={<CopyIcon />} onClick={() => void copy()}>复制</PurrButton>
      {!scheme.is_builtin && candidateMethodIds.length ? <PurrButton type="primary" disabled={!candidateSelection.length || dirty} onClick={() => void publishCandidates()}>确认并原子发布候选</PurrButton> : null}
      {!scheme.is_builtin && !candidateMethodIds.length ? <PurrButton type="primary" disabled={!members.length || dirty} onClick={() => void publish()}>主动发布方案</PurrButton> : null}
      {!scheme.is_builtin ? <PurrButton type="text" onClick={() => void remove()}>删除</PurrButton> : null}
    </div>
    <div className="writing-method-versions">
      <h3>已发布版本</h3>
      {(scheme.revisions ?? []).map((revision) => <div key={revision.id}>v{revision.version_no} · {revision.members.length} 个方法</div>)}
    </div>
  </div>
}

function VersionHistory({ revisions }: { revisions: WritingMethodRevision[] }) {
  return <div className="writing-method-versions"><h3>已发布版本</h3>
    {revisions.length ? revisions.map((revision) => <details key={revision.id}>
      <summary>v{revision.version_no} · {revision.content_digest.slice(0, 10)}</summary>
      <pre>{revision.markdown_body}</pre>
    </details>) : <span>尚未发布</span>}
  </div>
}

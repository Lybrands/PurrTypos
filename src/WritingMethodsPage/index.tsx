import React from 'react'
import { services } from '@/services'
import { ArrowLeftIcon, CopyIcon, PlusIcon } from '@/purr-components'
import { PurrButton, PurrSpin, PurrTooltip, usePurrToast } from '@/purr-components'
import AppHeader from '../components/AppHeader'
import type {
  WritingMethod,
  WritingMethodRevision,
  WritingMethodType,
  WritingScheme,
} from '../types'
import './index.scss'

interface Props { onBack: () => void }

type LibraryTab = 'methods' | 'schemes'

export default function WritingMethodsPage({ onBack }: Props) {
  const appMessage = usePurrToast()
  const [tab, setTab] = React.useState<LibraryTab>('methods')
  const [methods, setMethods] = React.useState<WritingMethod[]>([])
  const [schemes, setSchemes] = React.useState<WritingScheme[]>([])
  const [selectedMethod, setSelectedMethod] = React.useState<WritingMethod | null>(null)
  const [selectedScheme, setSelectedScheme] = React.useState<WritingScheme | null>(null)
  const [loading, setLoading] = React.useState(true)

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

  const createMethod = async (guided: boolean) => {
    const name = window.prompt('写作方法名称')?.trim()
    if (!name) return
    const result = await services.writingMethods.createMethod({
      name,
      methodType: 'technique',
      markdown: guided
        ? `# ${name}\n\n## 目标\n\n说明这个方法希望改善什么。\n\n## 使用方式\n\n写下可直接执行的创作指导。\n\n## 避免\n\n写下需要避免的做法。`
        : '',
    })
    if (!result.success || !result.data) return appMessage.error('创建写作方法失败')
    await reload()
    await openMethod(result.data.id)
  }

  const createScheme = async () => {
    const name = window.prompt('写作方案名称')?.trim()
    if (!name) return
    const result = await services.writingMethods.createScheme({ name, memberRevisionIds: [] })
    if (!result.success || !result.data) return appMessage.error('创建写作方案失败')
    await reload()
    await openScheme(result.data.id)
  }

  return (
    <div className="writing-methods-page">
      <AppHeader
        title="写作方法库"
        left={<PurrTooltip title="返回书架"><PurrButton type="text" size="small" aria-label="返回书架" icon={<ArrowLeftIcon style={{ fontSize: 14 }} />} onClick={onBack} /></PurrTooltip>}
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
              <>
                <PurrButton onClick={() => void createMethod(true)}>引导创建</PurrButton>
                <PurrButton type="primary" icon={<PlusIcon />} onClick={() => void createMethod(false)}>空白创建</PurrButton>
              </>
            ) : (
              <PurrButton type="primary" icon={<PlusIcon />} onClick={() => void createScheme()}>新建方案</PurrButton>
            )}
          </div>
        </div>
        <nav className="writing-methods-tabs" aria-label="写作方法库分类">
          <button className={tab === 'methods' ? 'active' : ''} onClick={() => setTab('methods')}>写作方法</button>
          <button className={tab === 'schemes' ? 'active' : ''} onClick={() => setTab('schemes')}>写作方案</button>
        </nav>
        {loading ? <div className="writing-methods-loading"><PurrSpin /></div> : (
          <div className="writing-methods-layout">
            <aside className="writing-methods-list">
              {(tab === 'methods' ? methods : schemes).map((item) => (
                <button
                  key={item.id}
                  className={(
                    tab === 'methods' ? selectedMethod?.id === item.id : selectedScheme?.id === item.id
                  ) ? 'active' : ''}
                  onClick={() => void (tab === 'methods' ? openMethod(item.id) : openScheme(item.id))}
                >
                  <strong>{item.name}</strong>
                  <span>{item.is_builtin ? '内置 · 只读' : item.source_type === 'copy' ? '我的 · 副本' : '我的'}</span>
                  <small>{item.current_published_revision_id ? '已发布' : '仅草稿'}{item.status === 'archived' ? ' · 已归档' : ''}</small>
                </button>
              ))}
            </aside>
            <section className="writing-methods-editor">
              {tab === 'methods' ? (
                selectedMethod ? <MethodEditor
                  key={`${selectedMethod.id}:${selectedMethod.draft_revision}`}
                  method={selectedMethod}
                  onChange={setSelectedMethod}
                  onReload={async () => { await reload(); await openMethod(selectedMethod.id) }}
                  onDeleted={async () => { setSelectedMethod(null); await reload() }}
                /> : <EmptyState text="选择或创建一个写作方法" />
              ) : (
                selectedScheme ? <SchemeEditor
                  key={`${selectedScheme.id}:${selectedScheme.draft_revision}`}
                  scheme={selectedScheme}
                  methods={methods}
                  onChange={setSelectedScheme}
                  onReload={async () => { await reload(); await openScheme(selectedScheme.id) }}
                  onDeleted={async () => { setSelectedScheme(null); await reload() }}
                /> : <EmptyState text="选择或创建一个写作方案" />
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  )
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
      <select value={methodType} disabled={!!method.is_builtin} onChange={(e) => setMethodType(e.target.value as WritingMethodType)}>
        <option value="primary">主风格</option><option value="technique">专项技法</option>
      </select>
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
        return <label key={methodId}>
          <input type="checkbox" checked={candidateSelection.includes(methodId)} onChange={(event) => setCandidateSelection((current) => event.target.checked ? [...current, methodId] : current.filter((id) => id !== methodId))} />
          <span>{method.name} · 草稿 r{method.draft_revision}</span>
        </label>
      })}
      <p>可逐项编辑或删除；这里只决定本次原子发布包含哪些候选。</p>
    </div> : null}
    <div className="writing-scheme-members">
      <h3>有序方法版本</h3>
      {revisions.map((revision) => <label key={revision.id}>
        <input
          type="checkbox"
          disabled={!!scheme.is_builtin}
          checked={members.includes(revision.id)}
          onChange={(event) => setMembers((current) => event.target.checked
            ? [...current, revision.id]
            : current.filter((id) => id !== revision.id))}
        />
        <span>{revision.name} · v{revision.version_no}</span>
      </label>)}
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

function EmptyState({ text }: { text: string }) {
  return <div className="writing-methods-empty">{text}</div>
}

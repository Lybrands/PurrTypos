import React from 'react'
import { services } from '@/services'
import { ArrowRightIcon, ImportIcon, PurrButton, PurrEmpty, PurrModal, PurrSegmented, PurrSpin, PurrTag, usePurrConfirm, usePurrToast } from '@/purr-components'
import AppHeader from '../components/AppHeader'
import TechniqueFileTree from '../components/TechniqueFileTree'
import Markdown from '../components/Markdown'
import { skillOperationId, type SkillObject } from '../services/skills'
import './index.scss'

type OriginFilter = 'all' | 'builtin' | 'installed'

interface SelectedSkill { object: SkillObject; paths: string[]; path: string; content: string | null }

export default function SkillsPage({ onBack, onHome, onOpenSettings }: { onBack: () => void; onHome: () => void; onOpenSettings: () => void }) {
  const toast = usePurrToast()
  const confirm = usePurrConfirm()
  const [origin, setOrigin] = React.useState<OriginFilter>('all')
  const [objects, setObjects] = React.useState<SkillObject[]>([])
  const [loading, setLoading] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [detailOpen, setDetailOpen] = React.useState(false)
  const [selected, setSelected] = React.useState<SelectedSkill | null>(null)
  const [preview, setPreview] = React.useState<{ files: Record<string, string>; entryRenamed: boolean } | null>(null)
  const input = React.useRef<HTMLInputElement>(null)
  const operation = React.useRef(skillOperationId())
  const visibleObjects = objects.filter(object => object.status === 'active'
    && (origin === 'all' || (origin === 'builtin') === (object.origin === 'builtin')))

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const result = await services.skills.list(true)
      setObjects(result.data ?? [])
    } catch (error) { toast.error((error as Error).message) } finally { setLoading(false) }
  }, [toast])
  React.useEffect(() => { void reload() }, [reload])

  const open = async (object: SkillObject) => {
    if (!object.publishedHead) return
    setBusy(true)
    try {
      const manifest = await services.skills.versionManifest(object.id, object.publishedHead)
      const paths = (manifest.data?.files ?? []).map(file => file.path).sort()
      const entry = paths.includes('SKILL.md') ? 'SKILL.md' : paths[0] ?? 'SKILL.md'
      const file = await services.skills.readVersionFile(object.id, object.publishedHead, entry)
      setSelected({ object, paths, path: entry, content: file.data?.content ?? '' })
      setDetailOpen(true)
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }

  const selectPath = async (path: string) => {
    if (!selected || path === selected.path) return
    setBusy(true)
    try {
      const version = selected.object.publishedHead!
      const file = await services.skills.readVersionFile(selected.object.id, version, path)
      setSelected({ ...selected, path, content: file.data?.content ?? '' })
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }

  const previewInstall = async (file?: File) => {
    if (!file) return
    setBusy(true)
    try {
      const result = await services.skills.previewInstall(file)
      setPreview(result.data ?? null)
      operation.current = skillOperationId()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
    if (input.current) input.current.value = ''
  }

  const install = async () => {
    if (!preview) return
    setBusy(true)
    try {
      const result = await services.skills.install(preview.files, operation.current)
      const installed = result.data
      setPreview(null); await reload()
      if (installed && !installed.autoUse) toast.info('技能已安装；未声明 metadata.autoUse，Agent 不会自动使用')
      else toast.success('技能已安装')
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }

  const archive = async (object: SkillObject) => {
    setBusy(true)
    try {
      await services.skills.setStatus(object.id, object.status === 'archived' ? 'active' : 'archived', skillOperationId())
      setDetailOpen(false); await reload()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }

  const remove = async (object: SkillObject) => {
    setBusy(true)
    try {
      const previewData = (await services.skills.deletionPreview(object.id)).data
      if (!previewData) return
      if (await confirm({
        title: '永久删除这个技能？',
        content: `将删除 ${previewData.versionCount} 个版本，删除后不可恢复。`,
        confirmText: '永久删除',
      }) !== 'confirm') return
      await services.skills.deleteObject(object.id, previewData.revisionToken, skillOperationId())
      setDetailOpen(false); await reload()
    } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
  }

  const selectedBuiltin = selected?.object.origin === 'builtin'

  return <div className="skills-page">
    <AppHeader
      title="技能库"
      navigation={{ home: { label: '返回首页', onClick: onHome }, back: { label: '返回书架', onClick: onBack } }}
      showActions
      onOpenSettings={onOpenSettings}
    />
    <main className="skills-main">
      <div className="skills-toolbar">
        <div><h1>技能库</h1><p>内置契约与参考技能随应用更新；也可自行安装技能包。</p></div>
        <div className="skills-toolbar-actions">
          <input ref={input} type="file" hidden accept=".md,.zip" onChange={event => void previewInstall(event.target.files?.[0])} />
          <PurrButton disabled={busy} icon={<ImportIcon />} onClick={() => input.current?.click()}>安装技能</PurrButton>
        </div>
      </div>
      <div className="skills-filter-row">
        <nav aria-label="技能来源"><PurrSegmented<OriginFilter> value={origin} onChange={setOrigin} options={[
          { value: 'all', label: '全部' }, { value: 'builtin', label: '内置技能' }, { value: 'installed', label: '已安装技能' },
        ]} /></nav>
        <span className="skills-count">{loading ? '正在读取…' : `共 ${visibleObjects.length} 项`}</span>
      </div>
      <div className="skills-stage" aria-busy={loading}>
        {loading ? <div className="skills-loading" role="status"><PurrSpin /><span>正在读取技能…</span></div>
          : !visibleObjects.length ? <PurrEmpty className="skills-empty" description={<><h2>{origin === 'builtin' ? '暂无内置技能' : origin === 'installed' ? '还没有安装技能' : '暂无技能'}</h2><p>技能是 SKILL.md 格式的包；可以从 .md 或 .zip 安装。</p></>}>
            <PurrButton type="primary" icon={<ImportIcon />} disabled={busy} onClick={() => input.current?.click()}>安装技能包</PurrButton>
          </PurrEmpty>
          : <div className="skills-card-grid">{visibleObjects.map(object => <PurrButton className="skills-card" key={object.id} disabled={!object.publishedHead} onClick={() => void open(object)}>
            <span className="skills-card-meta">
              {object.origin === 'builtin' ? <PurrTag color="purple">内置</PurrTag> : <PurrTag color="blue">已安装</PurrTag>}
              {object.metadata?.autoUse ? <PurrTag color="green">允许自动使用</PurrTag> : <PurrTag>仅手动参考</PurrTag>}
            </span>
            <strong>{object.metadata?.name || '未命名技能'}</strong>
            <span className="skills-card-description">{object.metadata?.description || '暂无描述。'}</span>
            <span className="skills-card-footer">{object.publishedHead ? `查看内容 · ${object.publishedHead.slice(0, 8)}` : '未发布'}<ArrowRightIcon /></span>
          </PurrButton>)}</div>}
      </div>
    </main>

    <PurrModal open={Boolean(preview)} title="安装技能" onCancel={() => setPreview(null)} onOk={install} confirmLoading={busy} okText="安装" okButtonProps={{ disabled: !preview }}>
      <p>{preview?.entryRenamed ? '此 Markdown 将作为入口 SKILL.md。' : '将保留以下文件及目录。'}</p>
      <p>是否允许 Agent 自动使用，由包内 SKILL.md 的 <code>metadata.autoUse</code> 声明决定；未声明时 Agent 不会自动读取。</p>
      <ul className="skills-preview-files">{Object.keys(preview?.files || {}).map(path => <li key={path}>{path}</li>)}</ul>
    </PurrModal>

    <PurrModal
      open={detailOpen}
      title={selected?.object.metadata?.name || '技能'}
      onCancel={() => { if (!busy) setDetailOpen(false) }}
      footer={selected ? <div className="skills-detail-footer">
        {selected.object.publishedHead && <a className="skills-export-link" href={services.skills.exportUrl(selected.object.id, selected.object.publishedHead)} download>导出 ZIP</a>}
        {selectedBuiltin
          ? <span className="skills-builtin-note">内置技能随应用更新，不可修改或删除。</span>
          : <>
            <PurrButton disabled={busy} onClick={() => void archive(selected.object)}>{selected.object.status === 'archived' ? '恢复' : '归档'}</PurrButton>
            {selected.object.status === 'archived' && <PurrButton danger disabled={busy} onClick={() => void remove(selected.object)}>永久删除</PurrButton>}
          </>}
      </div> : null}
    >
      {selected && <div className="skills-detail">
        <div className="skills-detail-meta">
          {selected.object.origin === 'builtin' ? <PurrTag color="purple">内置</PurrTag> : <PurrTag color="blue">已安装</PurrTag>}
          {selected.object.metadata?.autoUse ? <PurrTag color="green">允许自动使用</PurrTag> : <PurrTag>仅手动参考</PurrTag>}
          <span className="skills-detail-path">{selected.path}</span>
        </div>
        <div className="skills-detail-files">
          <nav><TechniqueFileTree rootLabel="技能文件" paths={selected.paths} selectedPath={selected.path} onSelect={path => void selectPath(path)} /></nav>
          <div className="skills-detail-content">{selected.content === null ? <PurrSpin /> : <Markdown yamlFrontmatter={selected.path === 'SKILL.md'}>{selected.content}</Markdown>}</div>
        </div>
      </div>}
    </PurrModal>
  </div>
}

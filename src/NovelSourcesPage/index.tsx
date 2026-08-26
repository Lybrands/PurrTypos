import React from 'react'
import { useNavigate } from 'react-router-dom'
import { services } from '@/services'
import { ArrowLeftIcon, PurrButton, PurrSpin, PurrTooltip, usePurrToast } from '@/purr-components'
import AppHeader from '../components/AppHeader'
import { buildStreamOptions } from '../agent-runtime/streamOptions'
import { normalizeApiProvider } from '../modelCatalog'
import type {
  AiModelConfig,
  Book,
  NovelAnalysisArtifact,
  NovelAnalysisRun,
  NovelSourceImportPreview,
  NovelSourcePickedFile,
  NovelSourceWork,
} from '../types'
import './index.scss'

export default function NovelSourcesPage({
  books,
  modelConfigs,
  onBack,
}: {
  books: Book[]
  modelConfigs: AiModelConfig[]
  onBack(): void
}) {
  const appMessage = usePurrToast()
  const navigate = useNavigate()
  const [works, setWorks] = React.useState<NovelSourceWork[]>([])
  const [picked, setPicked] = React.useState<NovelSourcePickedFile | null>(null)
  const [preview, setPreview] = React.useState<NovelSourceImportPreview | null>(null)
  const [title, setTitle] = React.useState('')
  const [rightsConfirmed, setRightsConfirmed] = React.useState(false)
  const [boundaryConfirmed, setBoundaryConfirmed] = React.useState(false)
  const [singleConfirmed, setSingleConfirmed] = React.useState(false)
  const [freezeBookId, setFreezeBookId] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [analysisRevisionId, setAnalysisRevisionId] = React.useState('')
  const [analysisModelId, setAnalysisModelId] = React.useState(modelConfigs[0]?.id ?? '')
  const [analysisRuns, setAnalysisRuns] = React.useState<NovelAnalysisRun[]>([])
  const [analysisArtifact, setAnalysisArtifact] = React.useState<NovelAnalysisArtifact | null>(null)
  const [factsJson, setFactsJson] = React.useState('[]')
  const [cardsJson, setCardsJson] = React.useState('[]')
  const [publishedAnalysisId, setPublishedAnalysisId] = React.useState('')

  const reload = React.useCallback(async () => {
    const result = await services.novelSources.list()
    if (result.success) setWorks(result.data ?? [])
  }, [])
  React.useEffect(() => { void reload() }, [reload])
  React.useEffect(() => {
    if (!analysisModelId && modelConfigs[0]) setAnalysisModelId(modelConfigs[0].id)
  }, [analysisModelId, modelConfigs])

  const runtime = React.useCallback(() => {
    const model = modelConfigs.find((item) => item.id === analysisModelId)
    if (!model) throw new Error('请先在设置中配置分析模型')
    const { options } = buildStreamOptions({ cfg: model, selectedModel: model.id })
    return {
      apiKey: model.apiKey,
      baseURL: model.baseUrl || undefined,
      apiProvider: normalizeApiProvider(model.apiProvider),
      locale: document.documentElement.lang || 'zh-CN',
      options,
      contextWindow: options.context_window,
    }
  }, [analysisModelId, modelConfigs])

  const reloadAnalysis = React.useCallback(async (revisionId = analysisRevisionId) => {
    if (!revisionId) return
    const result = await services.novelSources.listAnalysisRuns({ revisionId })
    if (!result.success) return appMessage.error(result.error || '读取分析进度失败')
    setAnalysisRevisionId(revisionId)
    setAnalysisRuns(result.data ?? [])
  }, [analysisRevisionId, appMessage])

  const startAnalysis = async (revisionId: string) => {
    setBusy(true)
    try {
      const result = await services.novelSources.startAnalysis({
        revisionId,
        commandId: `novel-analysis-${crypto.randomUUID()}`,
        runtime: runtime(),
      })
      if (!result.success) throw new Error(result.error || '启动分析失败')
      setAnalysisRevisionId(revisionId)
      setAnalysisArtifact(null)
      appMessage.success('分析已进入可恢复任务；来源正文会按章节按需发送')
      await reloadAnalysis(revisionId)
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const openArtifact = async (reference: string) => {
    const artifactId = reference.replace('novel-analysis-artifact://', '')
    const result = await services.novelSources.getAnalysisArtifact({ artifactId })
    if (!result.success || !result.data) return appMessage.error(result.error || '读取分析结果失败')
    setAnalysisArtifact(result.data)
    setFactsJson(JSON.stringify(result.data.facts, null, 2))
    setCardsJson(JSON.stringify(result.data.craftCards, null, 2))
  }

  const controlAnalysis = async (run: NovelAnalysisRun, action: 'pause' | 'resume' | 'cancel') => {
    if (!run.taskId) return
    setBusy(true)
    try {
      const result = action === 'pause'
        ? await services.novelSources.pauseAnalysis({
            taskId: run.taskId,
            expectedTaskRevision: run.taskRevision ?? undefined,
          })
        : action === 'cancel'
          ? await services.novelSources.cancelAnalysis({ taskId: run.taskId })
          : await services.novelSources.resumeAnalysis({
              taskId: run.taskId,
              commandId: `novel-analysis-resume-${crypto.randomUUID()}`,
              retryFailed: run.taskStatus === 'failed',
              runtime: runtime(),
            })
      if (!result.success) throw new Error(result.error || '分析任务操作失败')
      await reloadAnalysis()
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const reviewArtifact = async () => {
    if (!analysisArtifact) return
    try {
      const facts = JSON.parse(factsJson)
      const craftCards = JSON.parse(cardsJson)
      const result = await services.novelSources.reviewAnalysisArtifact({
        artifactId: analysisArtifact.artifactId,
        commandId: `novel-analysis-review-${crypto.randomUUID()}`,
        facts,
        craftCards,
      })
      if (!result.success || !result.data) throw new Error(result.error || '保存审核结果失败')
      setAnalysisArtifact(result.data)
      appMessage.success('审核修订已保存为新的不可变 Artifact')
    } catch (error) {
      appMessage.error((error as Error).message || '审核 JSON 无效')
    }
  }

  const publishArtifact = async () => {
    if (!analysisArtifact) return
    const result = await services.novelSources.publishAnalysisArtifact({
      artifactId: analysisArtifact.artifactId,
    })
    if (!result.success) return appMessage.error(result.error || '发布分析失败')
    setPublishedAnalysisId(result.data?.id ?? '')
    appMessage.success(`已发布正式来源分析 v${result.data?.versionNo ?? ''}`)
  }

  const createMethodCandidates = async () => {
    if (!publishedAnalysisId) return
    const result = await services.writingMethods.createCandidates({
      analysisId: publishedAnalysisId,
    })
    if (!result.success) return appMessage.error(result.error || '生成候选写作方法失败')
    appMessage.success('候选方法和候选方案已创建；发布前不会生效或绑定作品')
    navigate('/writing-methods')
  }

  const pick = async () => {
    const fileResult = await services.files.pickNovelSourceTextFile()
    if (!fileResult.success || !fileResult.data) {
      if (fileResult.error !== 'canceled') appMessage.error(fileResult.error || '选择文件失败')
      return
    }
    setBusy(true)
    try {
      const previewResult = await services.novelSources.previewImport(fileResult.data)
      if (!previewResult.success || !previewResult.data) {
        throw new Error(previewResult.error || '来源预览失败')
      }
      setPicked(fileResult.data)
      setPreview(previewResult.data)
      setTitle(previewResult.data.suggestedTitle)
      setRightsConfirmed(false)
      setBoundaryConfirmed(false)
      setSingleConfirmed(false)
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    if (!picked || !preview) return
    setBusy(true)
    const result = await services.novelSources.confirmImport({
      ...picked,
      title,
      expectedContentDigest: preview.contentDigest,
      confirmSingleSection: singleConfirmed,
      rightsConfirmed,
      modelDataBoundaryConfirmed: boundaryConfirmed,
    })
    setBusy(false)
    if (!result.success) return appMessage.error(result.error || '导入失败')
    setPicked(null)
    setPreview(null)
    appMessage.success('来源已冻结为不可变版本')
    await reload()
  }

  const freeze = async () => {
    if (!freezeBookId) return
    setBusy(true)
    const result = await services.novelSources.freezeBook({ bookId: freezeBookId })
    setBusy(false)
    if (!result.success) return appMessage.error(result.error || '冻结失败')
    appMessage.success('已从当前作品明确创建来源版本')
    await reload()
  }

  return <div className="novel-sources-page">
    <AppHeader
      title="小说来源库"
      left={<PurrTooltip title="返回书架"><PurrButton type="text" size="small" aria-label="返回书架" icon={<ArrowLeftIcon style={{ fontSize: 14 }} />} onClick={onBack} /></PurrTooltip>}
      showActions
    />
    <main className="novel-sources-main">
      <div className="novel-sources-toolbar">
        <div>
          <span className="novel-sources-eyebrow">SOURCE LIBRARY</span>
          <h1>小说来源库</h1>
          <p>来源是只读证据，不会伪装成可编辑作品。</p>
        </div>
        <PurrButton type="primary" onClick={() => void pick()}>导入 TXT / Markdown</PurrButton>
      </div>

      <section className="novel-source-freeze">
      <div><strong>从现有原创作品冻结</strong><p>只有点击冻结时才创建完整来源版本。</p></div>
      <select value={freezeBookId} onChange={(event) => setFreezeBookId(event.target.value)}>
        <option value="">选择原创作品</option>
        {books.filter((book) => book.creation_mode !== 'continuation').map((book) => (
          <option key={book.id} value={book.id}>{book.title}</option>
        ))}
      </select>
      <PurrButton disabled={!freezeBookId || busy} onClick={() => void freeze()}>明确冻结</PurrButton>
      </section>

      {busy ? <div className="novel-sources-loading"><PurrSpin /></div> : null}
      {preview && picked ? <section className="novel-source-preview">
      <h2>导入确认</h2>
      <label>来源名称<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <div className="novel-source-metrics">
        <span>{preview.fileName}</span>
        <span>{preview.byteCount.toLocaleString()} 字节</span>
        <span>{preview.characterCount.toLocaleString()} 字符</span>
        <span>{preview.sectionCount} 节</span>
      </div>
      <p className="novel-source-boundary">{preview.modelDataBoundaryNotice}</p>
      <p>{preview.rightsNotice}</p>
      <div className="novel-source-sections">
        {preview.sections.map((section) => <article key={section.ordinal}>
          <strong>{section.title}</strong><span>{section.characterCount} 字符</span>
          <p>{section.preview}</p>
        </article>)}
      </div>
      {preview.requiresSingleSectionConfirmation ? <label>
        <input type="checkbox" checked={singleConfirmed} onChange={(event) => setSingleConfirmed(event.target.checked)} />
        我确认按单节来源导入
      </label> : null}
      <label><input type="checkbox" checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)} /> 我确认有权使用该作品</label>
      <label><input type="checkbox" checked={boundaryConfirmed} onChange={(event) => setBoundaryConfirmed(event.target.checked)} /> 我理解后续分析的数据发送边界</label>
      <PurrButton
        type="primary"
        disabled={busy || !rightsConfirmed || !boundaryConfirmed || (preview.requiresSingleSectionConfirmation && !singleConfirmed)}
        onClick={() => void confirm()}
      >确认导入不可变版本</PurrButton>
      </section> : null}

      <section className="novel-source-list">
      <h2>只读来源</h2>
      {works.length === 0 ? <p>尚未导入来源。</p> : works.map((work) => <article key={work.id}>
        <div><strong>{work.title}</strong><span>{work.source_type === 'frozen_book' ? '作品冻结' : '外部文本'}</span></div>
        <span>{work.revision_count ?? 0} 个不可变版本</span>
        {work.latest_revision_id ? <PurrButton
          disabled={busy || modelConfigs.length === 0}
          onClick={() => void startAnalysis(work.latest_revision_id!)}
        >分析最新版本</PurrButton> : null}
      </article>)}
      </section>

      <section className="novel-analysis-panel">
      <div className="novel-analysis-heading">
        <div><h2>证据化来源分析</h2><p>分析只读来源，结果审核发布前不会进入正史或写作方法。</p></div>
        <select value={analysisModelId} onChange={(event) => setAnalysisModelId(event.target.value)}>
          <option value="">选择分析模型</option>
          {modelConfigs.map((model) => <option key={model.id} value={model.id}>{model.nickname || model.name}</option>)}
        </select>
        <PurrButton disabled={!analysisRevisionId} onClick={() => void reloadAnalysis()}>刷新进度</PurrButton>
      </div>
      {analysisRuns.map((run) => <article className="novel-analysis-run" key={run.taskId || run.runId}>
        <div><strong>{run.taskStatus || run.runStatus}</strong><span>{run.completedUnits}/{run.totalUnits} Units</span></div>
        <div className="novel-analysis-actions">
          {run.taskStatus === 'running' || run.taskStatus === 'pending' ? <PurrButton onClick={() => void controlAnalysis(run, 'pause')}>暂停</PurrButton> : null}
          {run.taskStatus === 'paused' || run.taskStatus === 'failed' ? <PurrButton onClick={() => void controlAnalysis(run, 'resume')}>{run.taskStatus === 'failed' ? '重试' : '恢复'}</PurrButton> : null}
          {run.taskStatus && !['completed', 'failed', 'canceled'].includes(run.taskStatus) ? <PurrButton onClick={() => void controlAnalysis(run, 'cancel')}>取消</PurrButton> : null}
          {run.artifactRef ? <PurrButton type="primary" onClick={() => void openArtifact(run.artifactRef!)}>审核结果</PurrButton> : null}
        </div>
      </article>)}
      {analysisArtifact ? <div className="novel-analysis-review">
        <h3>审核事实与技法卡</h3>
        <p>引用摘录会在保存和发布时再次逐字校验；无法找到的证据会拒绝提交。</p>
        <label>硬事实 JSON<textarea value={factsJson} onChange={(event) => setFactsJson(event.target.value)} /></label>
        <label>技法卡 JSON<textarea value={cardsJson} onChange={(event) => setCardsJson(event.target.value)} /></label>
        <div>
          <PurrButton onClick={() => void reviewArtifact()}>保存审核修订</PurrButton>
          <PurrButton type="primary" onClick={() => void publishArtifact()}>主动发布正式分析</PurrButton>
          {publishedAnalysisId ? <PurrButton onClick={() => void createMethodCandidates()}>生成候选写作方法</PurrButton> : null}
        </div>
      </div> : null}
      </section>
    </main>
  </div>
}

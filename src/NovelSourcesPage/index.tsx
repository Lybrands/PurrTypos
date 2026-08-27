import React from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { services } from '@/services'
import {
  ArrowLeftIcon,
  BookIcon,
  DeleteIcon,
  FileTextIcon,
  HomeIcon,
  ImportIcon,
  InboxIcon,
  PurrButton,
  PurrCheckbox,
  PurrInput,
  PurrModal,
  PurrSegmented,
  PurrSelect,
  PurrSpin,
  PurrTooltip,
  usePurrConfirm,
  usePurrToast,
} from '@/purr-components'
import AppHeader from '../components/AppHeader'
import {
  AgentConversationPanel,
  type AgentConversationController,
  type AgentConversationExtensions,
} from '../components/AgentConversation'
import Markdown from '../components/Markdown'
import { buildStreamOptions } from '../agent-runtime/streamOptions'
import type { AgentConversationMessage, AiTaskPlan } from '../agent-runtime'
import { normalizeApiProvider } from '../modelCatalog'
import type {
  AiModelConfig,
  Book,
  NovelAnalysisArtifact,
  NovelAnalysisRun,
  NovelSourceImportPreview,
  NovelSourcePickedFile,
  NovelSourceWork,
  PublishedNovelAnalysis,
} from '../types'
import './index.scss'

type SourceFilter = 'all' | 'external_text' | 'frozen_book'
type ImportView = 'choose' | 'freeze'

const ACTIVE_ANALYSIS_STATUSES = new Set(['pending', 'running', 'claimed'])

function isAnalysisRunActive(run: NovelAnalysisRun) {
  return ACTIVE_ANALYSIS_STATUSES.has(run.taskStatus || run.runStatus)
}

function analysisErrorMessage(run: NovelAnalysisRun) {
  if ((run.taskStatus || run.runStatus) === 'canceled') return ''
  const code = run.error || (run.units || []).find((unit) => unit.errorCode)?.errorCode
  if (!code) return ''
  if (code === 'planning_failed') return '任务在进入分析前中断。当前版本已移除前置模型规划，请重新开始分析。'
  if (code === 'upstream_stream_interrupted' || code === 'model_gateway_error') return '模型连接中断；系统会自动重试，仍未恢复时可手动重试。'
  if (code === 'provider_authentication_failed') return '模型凭据无效，请检查模型设置后重试。'
  if (code === 'provider_bad_request') return '模型拒绝了分析请求，请更换兼容模型或检查模型设置。'
  if (code === 'provider_rate_limited') return '模型服务当前繁忙，系统会自动重试。'
  if (code === 'provider_insufficient_balance') return '模型账户余额或额度不足，请处理后重试。'
  if (code === 'model_invocation_failed') return '模型调用中断，可以保留当前任务并重试。'
  if (code === 'user_paused_novel_analysis') return '任务由你暂停，恢复后会从未完成的步骤继续。'
  return `分析未完成：${code}`
}

function displayFactValue(value: unknown) {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value) } catch { return String(value) }
}

function analysisTaskPlan(run: NovelAnalysisRun): AiTaskPlan {
  const status = run.taskStatus || run.runStatus
  const planStatus: AiTaskPlan['status'] = status === 'completed' || status === 'done'
    ? 'done'
    : status === 'paused'
      ? 'paused'
      : status === 'failed'
        ? 'failed'
        : status === 'canceled'
          ? 'canceled'
          : 'running'
  return {
    runId: run.runId,
    title: '来源分析',
    goal: '形成可审核的事实脉络和写作技法',
    status: planStatus,
    steps: (run.units || []).map((unit) => ({
      id: unit.unitId,
      title: unit.title,
      type: unit.kind === 'build_review_artifact' ? 'review' : 'analyze',
      executor: unit.kind === 'validate_evidence' || unit.kind === 'coverage_report' ? 'tool' : 'model',
      status: unit.status === 'completed'
        ? 'done'
        : unit.status === 'failed'
          ? 'failed'
          : ACTIVE_ANALYSIS_STATUSES.has(unit.status)
            ? 'running'
            : 'pending',
      error: unit.errorCode || undefined,
    })),
  }
}

function analysisMessages(
  run: NovelAnalysisRun,
  modelName: string,
): AgentConversationMessage[] {
  const completed = (run.units || []).filter((unit) => unit.summary)
  const current = (run.units || []).find((unit) => ACTIVE_ANALYSIS_STATUSES.has(unit.status))
  const error = analysisErrorMessage(run)
  const content = completed.map((unit) => [
    unit.summary,
    ...(unit.highlights || []).map((highlight) => `- ${highlight}`),
  ].join('\n')).join('\n\n')
  const terminal = run.artifactRef
    ? '分析结果已经生成，可以检查并保存。'
    : ''
  const plan = analysisTaskPlan(run)
  return [{
    role: 'user',
    content: '分析这部小说的事实脉络和写作技法。',
    sentAt: run.createTime || undefined,
    clientTurnId: run.commandId,
  }, {
    role: 'assistant',
    content: [content, terminal].filter(Boolean).join('\n\n'),
    streamingContent: current ? `正在${current.title}…` : undefined,
    sentAt: run.updateTime || undefined,
    agentRunId: run.runId,
    longTaskId: run.taskId || undefined,
    model: modelName,
    taskPlan: plan,
    isError: Boolean(error),
    error: error || undefined,
  }]
}

export default function NovelSourcesPage({
  books,
  modelConfigs,
  onBack,
  onHome,
}: {
  books: Book[]
  modelConfigs: AiModelConfig[]
  onBack(): void
  onHome(): void
}) {
  const appMessage = usePurrToast()
  const confirmDialog = usePurrConfirm()
  const navigate = useNavigate()
  const { workId } = useParams<{ workId?: string }>()
  const [works, setWorks] = React.useState<NovelSourceWork[]>([])
  const [detailWork, setDetailWork] = React.useState<NovelSourceWork | null>(null)
  const [sourceFilter, setSourceFilter] = React.useState<SourceFilter>('all')
  const [importOpen, setImportOpen] = React.useState(false)
  const [importView, setImportView] = React.useState<ImportView>('choose')
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
  const [watchedAnalysisCommandId, setWatchedAnalysisCommandId] = React.useState('')
  const [analysisLastUpdatedAt, setAnalysisLastUpdatedAt] = React.useState<number | null>(null)
  const [publishedAnalyses, setPublishedAnalyses] = React.useState<PublishedNovelAnalysis[]>([])
  const [analysisArtifact, setAnalysisArtifact] = React.useState<NovelAnalysisArtifact | null>(null)
  const [factsJson, setFactsJson] = React.useState('[]')
  const [cardsJson, setCardsJson] = React.useState('[]')
  const [publishedAnalysisId, setPublishedAnalysisId] = React.useState('')

  const reload = React.useCallback(async () => {
    const result = await services.novelSources.list()
    if (result.success) setWorks(result.data ?? [])
  }, [])

  const reloadAnalysis = React.useCallback(async (revisionId = analysisRevisionId) => {
    if (!revisionId) return null
    const [runsResult, publishedResult] = await Promise.all([
      services.novelSources.listAnalysisRuns({ revisionId }),
      services.novelSources.listPublishedAnalyses({ revisionId }),
    ])
    if (!runsResult.success) {
      appMessage.error(runsResult.error || '读取分析进度失败')
      return null
    }
    const nextRuns = runsResult.data ?? []
    setAnalysisRevisionId(revisionId)
    setAnalysisRuns(nextRuns)
    setAnalysisLastUpdatedAt(Date.now())
    if (publishedResult.success) {
      const published = publishedResult.data ?? []
      setPublishedAnalyses(published)
      const latestPublished = published[0]
      const latestRun = nextRuns[0]
      const publishedCoversLatestRun = latestPublished && (
        !latestRun?.createTime
        || Date.parse(latestPublished.createTime) >= Date.parse(latestRun.createTime)
      )
      setPublishedAnalysisId(publishedCoversLatestRun ? latestPublished.id : '')
    }
    return nextRuns
  }, [analysisRevisionId, appMessage])

  const loadDetail = React.useCallback(async (id: string) => {
    const result = await services.novelSources.get({ workId: id })
    if (!result.success || !result.data) {
      appMessage.error(result.error || '来源不存在')
      navigate('/novel-sources', { replace: true })
      return
    }
    setDetailWork(result.data)
    const latestRevisionId = result.data.latest_revision_id || result.data.revisions?.[0]?.id || ''
    setAnalysisArtifact(null)
    setPublishedAnalysisId('')
    if (latestRevisionId) void reloadAnalysis(latestRevisionId)
  }, [appMessage, navigate, reloadAnalysis])

  React.useEffect(() => { void reload() }, [reload])
  React.useEffect(() => {
    if (workId) void loadDetail(workId)
    else setDetailWork(null)
  }, [loadDetail, workId])
  React.useEffect(() => {
    if (!analysisModelId && modelConfigs[0]) setAnalysisModelId(modelConfigs[0].id)
  }, [analysisModelId, modelConfigs])
  const hasActiveAnalysis = analysisRuns.some(isAnalysisRunActive)
  React.useEffect(() => {
    if (!analysisRevisionId || (!watchedAnalysisCommandId && !hasActiveAnalysis)) return
    let disposed = false
    let timer = 0
    const poll = async () => {
      const nextRuns = await reloadAnalysis(analysisRevisionId)
      if (disposed) return
      if (watchedAnalysisCommandId && nextRuns) {
        const watchedRun = nextRuns.find((run) => run.commandId === watchedAnalysisCommandId)
        if (watchedRun && !isAnalysisRunActive(watchedRun)) setWatchedAnalysisCommandId('')
      }
      timer = window.setTimeout(() => { void poll() }, 1500)
    }
    timer = window.setTimeout(() => { void poll() }, 1500)
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [analysisRevisionId, hasActiveAnalysis, reloadAnalysis, watchedAnalysisCommandId])

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

  const startAnalysis = async (revisionId: string) => {
    const decision = await confirmDialog({
      title: '开始来源分析',
      content: '分析时只会按任务需要，把对应章节片段发送给当前模型。',
      confirmText: '开始分析',
    })
    if (decision !== 'confirm') return
    const commandId = `novel-analysis-${crypto.randomUUID()}`
    setBusy(true)
    try {
      const result = await services.novelSources.startAnalysis({
        revisionId,
        commandId,
        runtime: runtime(),
      })
      if (!result.success) throw new Error(result.error || '启动分析失败')
      setAnalysisRevisionId(revisionId)
      setAnalysisRuns([])
      setAnalysisArtifact(null)
      setPublishedAnalysisId('')
      setWatchedAnalysisCommandId(commandId)
      appMessage.success('任务已接收，正在准备分析')
      await reloadAnalysis(revisionId)
    } catch (error) {
      setWatchedAnalysisCommandId('')
      appMessage.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const openArtifact = React.useCallback(async (reference: string) => {
    const artifactId = reference.replace('novel-analysis-artifact://', '')
    const result = await services.novelSources.getAnalysisArtifact({ artifactId })
    if (!result.success || !result.data) return appMessage.error(result.error || '读取分析结果失败')
    setAnalysisArtifact(result.data)
    setFactsJson(JSON.stringify(result.data.facts, null, 2))
    setCardsJson(JSON.stringify(result.data.craftCards, null, 2))
  }, [appMessage])

  React.useEffect(() => {
    const reference = analysisRuns[0]?.artifactRef
    if (!reference || analysisArtifact) return
    void openArtifact(reference)
  }, [analysisArtifact, analysisRuns, openArtifact])

  const controlAnalysis = async (run: NovelAnalysisRun, action: 'pause' | 'resume' | 'cancel') => {
    if (!run.taskId) return
    setBusy(true)
    try {
      const resumeCommandId = `novel-analysis-resume-${crypto.randomUUID()}`
      const result = action === 'pause'
        ? await services.novelSources.pauseAnalysis({ taskId: run.taskId, expectedTaskRevision: run.taskRevision ?? undefined })
        : action === 'cancel'
          ? await services.novelSources.cancelAnalysis({ taskId: run.taskId })
          : await services.novelSources.resumeAnalysis({
              taskId: run.taskId,
              commandId: resumeCommandId,
              retryFailed: run.taskStatus === 'failed',
              runtime: runtime(),
            })
      if (!result.success) throw new Error(result.error || '分析任务操作失败')
      if (action === 'resume') setWatchedAnalysisCommandId(resumeCommandId)
      await reloadAnalysis()
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const saveAnalysis = async () => {
    if (!analysisArtifact) return
    setBusy(true)
    try {
      const reviewed = await services.novelSources.reviewAnalysisArtifact({
        artifactId: analysisArtifact.artifactId,
        commandId: `novel-analysis-review-${crypto.randomUUID()}`,
        facts: JSON.parse(factsJson),
        craftCards: JSON.parse(cardsJson),
      })
      if (!reviewed.success || !reviewed.data) throw new Error(reviewed.error || '保存审核结果失败')
      const published = await services.novelSources.publishAnalysisArtifact({ artifactId: reviewed.data.artifactId })
      if (!published.success || !published.data) throw new Error(published.error || '保存分析失败')
      setAnalysisArtifact(reviewed.data)
      setPublishedAnalysisId(published.data.id)
      await reloadAnalysis()
      appMessage.success('分析结果已保存')
    } catch (error) {
      appMessage.error((error as Error).message || '审核 JSON 无效')
    } finally {
      setBusy(false)
    }
  }

  const createMethodCandidates = async () => {
    if (!publishedAnalysisId) return
    const result = await services.writingMethods.createCandidates({ analysisId: publishedAnalysisId })
    if (!result.success) return appMessage.error(result.error || '生成候选写作方法失败')
    appMessage.success('候选方法和方案已创建')
    navigate('/writing-methods')
  }

  const resetImport = () => {
    setImportOpen(false)
    setImportView('choose')
    setPicked(null)
    setPreview(null)
    setTitle('')
    setFreezeBookId('')
    setRightsConfirmed(false)
    setBoundaryConfirmed(false)
    setSingleConfirmed(false)
  }

  const pick = async (mode: 'file' | 'folder') => {
    const fileResult = await services.files.pickNovelSourceTextFile({ mode })
    if (!fileResult.success || !fileResult.data) {
      if (fileResult.error !== 'canceled') appMessage.error(fileResult.error || '选择来源失败')
      return
    }
    setBusy(true)
    try {
      const result = await services.novelSources.previewImport(fileResult.data)
      if (!result.success || !result.data) throw new Error(result.error || '来源预览失败')
      setPicked(fileResult.data)
      setPreview(result.data)
      setTitle(result.data.suggestedTitle)
      setRightsConfirmed(false)
      setBoundaryConfirmed(false)
      setSingleConfirmed(false)
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const confirmImport = async () => {
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
    if (!result.success || !result.data) return appMessage.error(result.error || '导入失败')
    resetImport()
    await reload()
    navigate(`/novel-sources/${encodeURIComponent(result.data.work_id)}`)
  }

  const freeze = async () => {
    if (!freezeBookId) return
    setBusy(true)
    const result = await services.novelSources.freezeBook({ bookId: freezeBookId })
    setBusy(false)
    if (!result.success || !result.data) return appMessage.error(result.error || '冻结失败')
    resetImport()
    await reload()
    navigate(`/novel-sources/${encodeURIComponent(result.data.work_id)}`)
  }

  const deleteWork = async (work: NovelSourceWork) => {
    const decision = await confirmDialog({
      title: `删除“${work.title}”？`,
      content: '将删除这部来源的全部未引用版本和正文。已经进入分析或续写的来源不能删除。',
      confirmText: '删除来源',
      confirmVariant: 'danger',
    })
    if (decision !== 'confirm') return
    const result = await services.novelSources.delete({ workId: work.id })
    if (!result.success) return appMessage.error(result.error || '删除失败')
    appMessage.success('来源已删除')
    if (workId === work.id) navigate('/novel-sources', { replace: true })
    await reload()
  }

  const filteredWorks = works.filter((work) => sourceFilter === 'all' || work.source_type === sourceFilter)
  const originalBooks = books.filter((book) => book.creation_mode !== 'continuation')

  const importModal = <PurrModal
    open={importOpen}
    title={preview ? '确认导入内容' : importView === 'freeze' ? '从原创作品冻结' : '添加小说来源'}
    width="min(780px, calc(100vw - 32px))"
    footer={null}
    onCancel={resetImport}
    styles={{ body: { maxHeight: 'min(72vh, 720px)', overflowY: 'auto' } }}
  >
    {preview && picked ? <div className="novel-source-preview">
      <div className="novel-source-preview-heading"><h2>检查导入内容</h2><span>确认后冻结为只读版本</span></div>
      <label className="novel-source-title-field"><span>来源名称</span><PurrInput value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <div className="novel-source-metrics">
        <span>{preview.documentCount} 个文稿</span><span>{preview.characterCount.toLocaleString()} 字符</span><span>{preview.sectionCount} 节</span>
        {preview.skippedFileCount > 0 ? <span>忽略 {preview.skippedFileCount} 个文件</span> : null}
      </div>
      <div className="novel-source-sections">{preview.sections.map((section) => <article key={section.ordinal}>
        <div><strong>{section.title}</strong><span>{section.characterCount} 字符</span></div><p>{section.preview}</p>
      </article>)}</div>
      <div className="novel-source-boundary">确认前不会发送正文；分析时只发送任务所需章节片段。</div>
      <div className="novel-source-confirmations">
        {preview.requiresSingleSectionConfirmation ? <PurrCheckbox checked={singleConfirmed} onChange={(event) => setSingleConfirmed(event.target.checked)}>按单节来源导入</PurrCheckbox> : null}
        <PurrCheckbox checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)}>我确认有权使用该作品</PurrCheckbox>
        <PurrCheckbox checked={boundaryConfirmed} onChange={(event) => setBoundaryConfirmed(event.target.checked)}>我理解模型数据边界</PurrCheckbox>
      </div>
      <div className="novel-source-preview-actions">
        <PurrButton onClick={() => { setPicked(null); setPreview(null) }}>重新选择</PurrButton>
        <PurrButton type="primary" disabled={busy || !rightsConfirmed || !boundaryConfirmed || (preview.requiresSingleSectionConfirmation && !singleConfirmed)} onClick={() => void confirmImport()}>确认导入</PurrButton>
      </div>
    </div> : importView === 'freeze' ? <div className="novel-freeze-flow">
      <PurrButton type="text" size="small" icon={<ArrowLeftIcon />} onClick={() => setImportView('choose')}>返回</PurrButton>
      <label><span>原创作品</span><PurrSelect
        aria-label="选择原创作品"
        value={freezeBookId || null}
        placeholder="请选择"
        options={originalBooks.map((book) => ({ value: book.id, label: book.title }))}
        onChange={(value) => setFreezeBookId(String(value ?? ''))}
      /></label>
      <PurrButton type="primary" disabled={!freezeBookId || busy} onClick={() => void freeze()}>冻结为来源</PurrButton>
    </div> : <div className="novel-import-options">
      <button type="button" disabled={busy} onClick={() => void pick('file')}><span><FileTextIcon /></span><strong>文件或 ZIP</strong><small>TXT、Markdown 或 ZIP</small></button>
      <button type="button" disabled={busy} onClick={() => void pick('folder')}><span><InboxIcon /></span><strong>章节文件夹</strong><small>按文件名顺序读取</small></button>
      <button type="button" disabled={busy || originalBooks.length === 0} onClick={() => setImportView('freeze')}><span><BookIcon /></span><strong>原创作品</strong><small>冻结当前作品内容</small></button>
    </div>}
  </PurrModal>

  if (!workId) {
    return <div className="novel-sources-page">
      <AppHeader
        title="小说来源库"
        left={<div className="library-header-nav">
          <PurrTooltip title="返回首页"><PurrButton type="text" size="small" aria-label="返回首页" icon={<HomeIcon style={{ fontSize: 15 }} />} onClick={onHome} /></PurrTooltip>
          <PurrTooltip title="返回书架"><PurrButton type="text" size="small" aria-label="返回书架" icon={<ArrowLeftIcon style={{ fontSize: 14 }} />} onClick={onBack} /></PurrTooltip>
        </div>}
        showActions
      />
      <main className="novel-sources-main">
        <div className="novel-sources-toolbar"><h1>小说来源库</h1><PurrButton type="primary" icon={<ImportIcon />} onClick={() => setImportOpen(true)}>添加来源</PurrButton></div>
        <div className="novel-source-filter-row">
          <PurrSegmented<SourceFilter> value={sourceFilter} onChange={setSourceFilter} options={[
            { value: 'all', label: `全部 ${works.length}` },
            { value: 'external_text', label: '本地导入' },
            { value: 'frozen_book', label: '原创冻结' },
          ]} />
        </div>
        {filteredWorks.length ? <div className="novel-source-library-grid">{filteredWorks.map((work, index) => <article className={`novel-source-library-card tone-${index % 5}`} key={work.id}>
          <button className="novel-source-card-open" onClick={() => navigate(`/novel-sources/${encodeURIComponent(work.id)}`)}>
            <span className="novel-source-card-type">{work.source_type === 'frozen_book' ? '原创冻结' : '本地导入'}</span>
            <strong>{work.title}</strong>
            <div><span>只读来源</span><span className={(work.analysis_count ?? 0) > 0 ? 'is-ready' : ''}>{(work.analysis_count ?? 0) > 0 ? '已分析' : '待分析'}</span></div>
          </button>
          <PurrTooltip title="删除来源"><PurrButton className="novel-source-card-delete" type="text" size="small" aria-label={`删除 ${work.title}`} icon={<DeleteIcon />} onClick={() => void deleteWork(work)} /></PurrTooltip>
        </article>)}</div> : <div className="novel-source-library-empty"><InboxIcon /><strong>还没有这类来源</strong><PurrButton onClick={() => setImportOpen(true)}>添加来源</PurrButton></div>}
      </main>
      {importModal}
    </div>
  }

  const selectedAnalysisModel = modelConfigs.find((model) => model.id === analysisModelId)
  const waitingForRun = Boolean(watchedAnalysisCommandId) && !analysisRuns.some((run) => run.commandId === watchedAnalysisCommandId)
  const latestRun = analysisRuns[0]
  const currentPlan = latestRun ? analysisTaskPlan(latestRun) : undefined
  const conversationMessages: AgentConversationMessage[] = latestRun
    ? analysisMessages(latestRun, selectedAnalysisModel?.name || '')
    : waitingForRun
      ? [{
          role: 'user',
          content: '分析这部小说的事实脉络和写作技法。',
          clientTurnId: watchedAnalysisCommandId,
        }, {
          role: 'assistant',
          content: '',
          streamingContent: '正在建立可恢复的来源分析任务…',
          model: selectedAnalysisModel?.name,
        }]
      : []
  const resultPanel = analysisArtifact ? <section className="novel-analysis-result">
    <div className="novel-analysis-result-head"><div><span>分析结果</span><h2>{analysisArtifact.facts.length} 条硬事实 · {analysisArtifact.craftCards.length} 个写作技法</h2></div>{publishedAnalysisId ? <em>已保存</em> : <em>待保存</em>}</div>
    <div className="novel-analysis-result-grid">
      <div><h3>事实脉络</h3>{analysisArtifact.facts.length ? <div className="novel-analysis-result-list">{analysisArtifact.facts.map((fact, index) => <article key={fact.id || `${fact.subjectKey}:${fact.predicate}:${index}`}><strong>{fact.subjectKey}</strong><p>{fact.predicate} · {displayFactValue(fact.value)}</p><small>{fact.evidence.length} 条原文证据</small></article>)}</div> : <p className="novel-analysis-result-empty">没有形成可验证的硬事实。</p>}</div>
      <div><h3>写作技法</h3>{analysisArtifact.craftCards.length ? <div className="novel-analysis-result-list">{analysisArtifact.craftCards.map((card, index) => <article key={card.id || `${card.title}:${index}`}><strong>{card.title}</strong><Markdown>{card.bodyMarkdown}</Markdown><small>{card.evidence.length} 条原文证据</small></article>)}</div> : <p className="novel-analysis-result-empty">没有形成可验证的写作技法。</p>}</div>
    </div>
    <details className="novel-analysis-raw"><summary>调整结构化结果</summary><div><label>硬事实 JSON<textarea value={factsJson} onChange={(event) => setFactsJson(event.target.value)} /></label><label>写作技法 JSON<textarea value={cardsJson} onChange={(event) => setCardsJson(event.target.value)} /></label></div></details>
    <div className="novel-analysis-result-actions">
      {publishedAnalysisId ? <PurrButton onClick={() => void createMethodCandidates()}>提炼为写作方法</PurrButton> : null}
      {publishedAnalysisId ? <PurrButton onClick={() => navigate('/bookshelf', { state: { createContinuationFrom: { workId, revisionId: analysisRevisionId, analysisId: publishedAnalysisId } } })}>创建续写</PurrButton> : null}
      <PurrButton type="primary" disabled={busy} onClick={() => void saveAnalysis()}>{publishedAnalysisId ? '保存修改' : '保存分析结果'}</PurrButton>
    </div>
  </section> : null
  const analysisController: AgentConversationController = {
    capabilities: {
      inputDisabled: true,
      sessionNavigationDisabled: true,
      submitMode: 'send',
    },
    conversation: {
      identity: `novel-analysis:${workId}:${latestRun?.runId || watchedAnalysisCommandId || 'empty'}`,
      sessions: [],
      activeSessionId: workId,
      messages: conversationMessages,
      activities: {},
      queuedSubmissions: [],
      initializing: false,
      running: Boolean(hasActiveAnalysis || waitingForRun),
      stopping: busy && Boolean(hasActiveAnalysis),
      paused: latestRun?.taskStatus === 'paused',
      resuming: busy && latestRun?.taskStatus === 'paused',
      attachmentsVersion: analysisArtifact?.artifactId || '',
    },
    composer: {
      value: '',
      setValue: () => undefined,
      placeholder: '来源分析由宿主任务控制',
      ariaLabel: '来源分析',
      submitDisabled: true,
      selectedModel: selectedAnalysisModel || null,
      modelConfigs,
      selectModel: setAnalysisModelId,
      openModelSettings: () => navigate('/settings'),
      taskPlan: currentPlan,
    },
    actions: {
      selectSession: () => undefined,
      createSession: () => undefined,
      closeSession: () => undefined,
      renameSession: () => undefined,
      send: () => undefined,
      abort: () => latestRun ? controlAnalysis(latestRun, 'cancel') : undefined,
      resume: () => latestRun ? controlAnalysis(latestRun, 'resume') : undefined,
      editMessage: () => undefined,
      resolveToolApproval: async () => ({ success: false, error: '来源分析不开放工具审批' }),
    },
  }
  const analysisExtensions: AgentConversationExtensions = {
    renderAssistantAttachment: (message) => (
      message.agentRunId === latestRun?.runId ? resultPanel : null
    ),
  }
  const analysisPanel = <section className="novel-analysis-workspace">
    <div className="novel-analysis-toolbar">
      <div>
        <strong>来源分析</strong>
        <span>{hasActiveAnalysis || waitingForRun ? <><i />自动更新中</> : analysisLastUpdatedAt ? `更新于 ${new Date(analysisLastUpdatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : '选择模型后开始'}</span>
      </div>
      <PurrSelect aria-label="分析模型" value={analysisModelId || null} placeholder="选择分析模型" options={modelConfigs.map((model) => ({ value: model.id, label: model.nickname || model.name }))} onChange={(value) => setAnalysisModelId(String(value ?? ''))} />
      <PurrButton type="primary" disabled={!analysisRevisionId || !modelConfigs.length || busy || hasActiveAnalysis || waitingForRun} onClick={() => void startAnalysis(analysisRevisionId)}>开始分析</PurrButton>
      {latestRun?.taskStatus === 'running' || latestRun?.taskStatus === 'pending' ? <PurrButton onClick={() => void controlAnalysis(latestRun, 'pause')}>暂停</PurrButton> : null}
      {latestRun?.taskStatus === 'paused' || latestRun?.taskStatus === 'failed' ? <PurrButton onClick={() => void controlAnalysis(latestRun, 'resume')}>{latestRun.taskStatus === 'failed' ? '重试' : '恢复'}</PurrButton> : null}
      {latestRun?.taskStatus && !['completed', 'failed', 'canceled'].includes(latestRun.taskStatus) ? <PurrButton onClick={() => void controlAnalysis(latestRun, 'cancel')}>取消</PurrButton> : null}
    </div>
    <AgentConversationPanel
      className="novel-analysis-agent-panel"
      controller={analysisController}
      extensions={analysisExtensions}
      indexOpen={false}
    />
  </section>

  return <div className="novel-sources-page">
    <AppHeader
      title={detailWork?.title || '来源详情'}
      left={<div className="library-header-nav">
        <PurrTooltip title="返回首页"><PurrButton type="text" size="small" aria-label="返回首页" icon={<HomeIcon style={{ fontSize: 15 }} />} onClick={onHome} /></PurrTooltip>
        <PurrTooltip title="返回来源库"><PurrButton type="text" size="small" aria-label="返回来源库" icon={<ArrowLeftIcon style={{ fontSize: 14 }} />} onClick={() => navigate('/novel-sources')} /></PurrTooltip>
      </div>}
      showActions
    />
    <main className="novel-source-detail-main">
      {!detailWork ? <div className="novel-sources-loading"><PurrSpin /></div> : <>
        <div className="novel-detail-heading">
          <div><span>{detailWork.source_type === 'frozen_book' ? '原创冻结' : '本地导入'}</span><h1>{detailWork.title}</h1><p>{detailWork.revisions?.[0]?.character_count.toLocaleString() ?? '—'} 字符 · {publishedAnalyses.length ? '分析结果已保存' : '等待分析'} · 只读</p></div>
          <PurrButton danger icon={<DeleteIcon />} onClick={() => void deleteWork(detailWork)}>删除来源</PurrButton>
        </div>
        {analysisPanel}
      </>}
    </main>
    {busy ? <div className="novel-source-busy"><PurrSpin /></div> : null}
  </div>
}

import { analysisSessions, type AnalysisSession } from '../services/analysisSessions'
import SourceTechniqueResults from './SourceTechniqueResults'
import AnalysisMaterials, { belongsToMaterialGroup, materialGroups } from './AnalysisMaterials'
import WritingTechniqueEvidence from './WritingTechniqueEvidence'
import React from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { services } from '@/services'
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BookIcon,
  DeleteIcon,
  FileTextIcon,
  ImportIcon,
  InboxIcon,
  SearchIcon,
  PurrButton,
  PurrInput,
  PurrModal,
  PurrSegmented,
  PurrSelect,
  PurrSpin,
  PurrTabs,
  PurrTooltip,
  usePurrConfirm,
  usePurrToast,
} from '@/purr-components'
import AppHeader from '../components/AppHeader'
import { recordAgentConversationDebugChunk, setAiDebugInspectorVisible } from '../components/AiDevInspector/store'
import {
  AgentConversationPanel,
  type AgentConversationController,
  type AgentConversationExtensions,
} from '../components/AgentConversation'
import Markdown from '../components/Markdown'
import { WritingSkillReview } from './WritingSkillReview'
import {
  NovelAnalysisEvidenceList,
  type NovelSourceEvidenceTarget,
} from '../components/NovelAnalysisEvidenceModal'
import { buildStreamOptions } from '../agent-runtime/streamOptions'
import {
  type AgentConversationMessage,
} from '../agent-runtime'
import { normalizeApiProvider } from '../modelCatalog'
import { createScopedComposer } from '../components/AgentConversation/scopedComposer'
import { createAnalysisConversationController } from './analysisController'
import type {
  AiModelConfig,
  Book,
  NovelAnalysisArtifact,
  NovelAnalysisEvidence,
  NovelAnalysisRun,
  NovelSourceImportPreview,
  NovelSourcePickedFile,
  NovelSourceRevision,
  NovelSourceSearchResult,
  NovelSourceSection,
  NovelSourceWork,
  PublishedNovelAnalysis,
} from '../types'
import { buildNovelAnalysisTaskPlan } from './analysisTaskPlan'
import {
  buildNovelAnalysisMessages,
  NovelAnalysisConversationStream,
  novelAnalysisArtifactId,
} from './analysisConversation'
import './index.scss'

type SourceFilter = 'all' | 'external_text' | 'frozen_book'
type ImportView = 'choose' | 'freeze'

const SOURCE_TEXT_WINDOW_SIZE = 20_000
const LONG_SOURCE_SECTION_CHARACTERS = 100_000

function analysisRuntimeForModel(model: AiModelConfig) {
  const { options } = buildStreamOptions({ cfg: model, selectedModel: model.id })
  return { modelConfigId: model.id, apiKey: model.apiKey, baseURL: model.baseUrl || undefined,
    apiProvider: normalizeApiProvider(model.apiProvider), locale: document.documentElement.lang || 'zh-CN',
    options, contextWindow: options.context_window }
}
type AnalysisSubmissionSnapshot = { replaceRunId?: string; conversationId: string; revisionId: string; runtime: ReturnType<typeof analysisRuntimeForModel> }

const ACTIVE_ANALYSIS_STATUSES = new Set(['queued', 'pending', 'running', 'claimed'])
const BLOCKING_ANALYSIS_STATUSES = new Set([...ACTIVE_ANALYSIS_STATUSES, 'paused'])
function durableAnalysisStatus(run?: NovelAnalysisRun) {
  if (!run) return ''
  return run.workflowStatus || ''
}
function isAnalysisRunActive(run: NovelAnalysisRun) {
  return run.conversationStatus === 'streaming'
    || ACTIVE_ANALYSIS_STATUSES.has(durableAnalysisStatus(run))
}

function isAnalysisRunBlocking(run: NovelAnalysisRun) {
  return BLOCKING_ANALYSIS_STATUSES.has(durableAnalysisStatus(run))
}

function analysisStatusText(runs: NovelAnalysisRun[], hasPublishedAnalysis: boolean) {
  if (hasPublishedAnalysis) return '已有保存结果'
  if (runs.some(isAnalysisRunActive)) return '分析进行中'
  if (runs.some(run => durableAnalysisStatus(run) === 'paused')) return '分析已暂停'
  return '等待分析'
}

function pausedAnalysisWorkflowNotice(run?: NovelAnalysisRun) {
  if (!run || durableAnalysisStatus(run) !== 'paused') return null
  const total = Math.max(0, Number(run.totalUnits) || 0)
  const completed = Math.min(total, Math.max(0, Number(run.completedUnits) || 0))
  const remaining = Math.max(0, total - completed)
  const progress = total > 0
    ? `已保留 ${completed}/${total} 个步骤的成果，剩余 ${remaining} 个步骤。`
    : '已保留当前可用成果。'
  const dueAt = Number(run.workflowAutoResumeAtMs || 0)
  const dueText = Number.isFinite(dueAt) && dueAt > Date.now()
    ? `系统预计将在 ${new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit', minute: '2-digit', hour12: false,
      }).format(new Date(dueAt))} 再次尝试。`
    : ''
  if (run.workflowPauseKind === 'budget') {
    return {
      text: `本轮模型调用预算已用完，分析已安全暂停。${progress}继续执行只会为这些未完成步骤补充一轮受限调用额度。`,
      resumeLabel: '继续并补充一轮预算',
    }
  }
  if (run.workflowPauseKind === 'user') {
    return {
      text: `分析已按你的要求暂停。${progress}恢复后会从未完成步骤继续。`,
      resumeLabel: '继续执行',
    }
  }
  if (run.workflowPauseKind === 'system') {
    return {
      text: `模型服务暂时不可用，分析已安全暂停。${progress}${dueText}`,
      resumeLabel: dueText ? '立即继续' : '继续执行',
    }
  }
  return {
    text: `分析已安全暂停。${progress}你可以继续执行，或结束这次未完成的分析。`,
    resumeLabel: '继续执行',
  }
}

function importSectionPreview(content: string, start: number, end: number) {
  return content.slice(start, end).trim().slice(0, 240)
}

function importSectionTitle(content: string, start: number, fallback: string) {
  const line = content.slice(start).split(/\r?\n/, 1)[0]?.trim() || ''
  return line.replace(/^#{1,6}\s+/, '').slice(0, 300) || fallback
}

function displayFactValue(value: unknown) {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value) } catch { return String(value) }
}

function sourceSectionCharacterCount(section?: NovelSourceSection) {
  if (!section) return null
  const start = Number(section.locator.startCharacter)
  const end = Number(section.locator.endCharacter)
  return Number.isFinite(start) && Number.isFinite(end) && end >= start
    ? end - start
    : null
}

function sourceTextWindowStart(characterOffset: number) {
  const offset = Math.max(0, Number.isFinite(characterOffset) ? characterOffset : 0)
  return Math.floor(offset / SOURCE_TEXT_WINDOW_SIZE) * SOURCE_TEXT_WINDOW_SIZE
}

function sourceEvidenceWindowStart(target: NovelSourceEvidenceTarget) {
  const excerptLength = Math.min(target.excerpt.length, SOURCE_TEXT_WINDOW_SIZE)
  return Math.max(0, target.start - Math.floor((SOURCE_TEXT_WINDOW_SIZE - excerptLength) / 2))
}

function highlightedSourceText(text: string, query: string, expectedIndex?: number) {
  if (!query.trim()) return text
  const expected = Number.isFinite(expectedIndex) ? Number(expectedIndex) : -1
  const exactMatch = expected >= 0 && text.slice(expected, expected + query.length) === query
  const index = exactMatch
    ? expected
    : text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase())
  if (index < 0) return text
  return <>{text.slice(0, index)}<mark>{text.slice(index, index + query.length)}</mark>{text.slice(index + query.length)}</>
}

function NovelSourceReader({
  work,
  revisionId,
  target,
  onRevisionChange,
}: {
  work: NovelSourceWork
  revisionId: string
  target?: NovelSourceEvidenceTarget | null
  onRevisionChange(revisionId: string): void
}) {
  const appMessage = usePurrToast()
  const [revision, setRevision] = React.useState<NovelSourceRevision | null>(null)
  const [selectedSectionId, setSelectedSectionId] = React.useState('')
  const [textWindowStart, setTextWindowStart] = React.useState(0)
  const [sectionWindow, setSectionWindow] = React.useState<NovelSourceSection | null>(null)
  const [revisionLoading, setRevisionLoading] = React.useState(false)
  const [textLoading, setTextLoading] = React.useState(false)
  const [searchDraft, setSearchDraft] = React.useState('')
  const [searchedQuery, setSearchedQuery] = React.useState('')
  const [searchResults, setSearchResults] = React.useState<NovelSourceSearchResult[]>([])
  const [searchLoading, setSearchLoading] = React.useState(false)
  const [highlightQuery, setHighlightQuery] = React.useState('')
  const [highlightOffset, setHighlightOffset] = React.useState<number | null>(null)
  const documentScrollRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!revisionId) return
    let current = true
    setRevisionLoading(true)
    setSearchDraft('')
    setSearchedQuery('')
    setSearchResults([])
    setHighlightQuery('')
    setHighlightOffset(null)
    void services.novelSources.getRevision({ revisionId }).then((result) => {
      if (!current) return
      if (!result.success || !result.data) {
        appMessage.error(result.error || '读取来源版本失败')
        setRevision(null)
        setSelectedSectionId('')
        return
      }
      const sections = result.data.sections ?? []
      const targeted = target?.revisionId === revisionId
        && sections.some((section) => section.id === target.sectionId)
      setRevision(result.data)
      setSelectedSectionId(targeted ? target.sectionId : sections[0]?.id ?? '')
      setTextWindowStart(targeted ? sourceEvidenceWindowStart(target) : 0)
      setHighlightQuery(targeted ? target.excerpt : '')
      setHighlightOffset(targeted ? target.start : null)
    }).finally(() => {
      if (current) setRevisionLoading(false)
    })
    return () => { current = false }
  }, [appMessage, revisionId, target?.excerpt, target?.revisionId, target?.sectionId, target?.start])

  React.useEffect(() => {
    if (!revision || revision.id !== revisionId || !selectedSectionId) {
      setSectionWindow(null)
      return
    }
    let current = true
    setTextLoading(true)
    void services.novelSources.getSection({
      revisionId,
      sectionId: selectedSectionId,
      startCharacter: textWindowStart,
      characterLimit: SOURCE_TEXT_WINDOW_SIZE,
    }).then((result) => {
      if (!current) return
      if (!result.success || !result.data) {
        appMessage.error(result.error || '读取原文失败')
        setSectionWindow(null)
        return
      }
      setSectionWindow(result.data)
    }).finally(() => {
      if (current) setTextLoading(false)
    })
    return () => { current = false }
  }, [appMessage, revision, revisionId, selectedSectionId, textWindowStart])

  const searchSource = async (value: string) => {
    const query = value.trim()
    setSearchDraft(value)
    setSearchedQuery(query)
    setSearchResults([])
    if (!query || !revisionId) return
    setSearchLoading(true)
    try {
      const result = await services.novelSources.searchSections({
        revisionId,
        query,
        limit: 20,
      })
      if (!result.success) throw new Error(result.error || '搜索原文失败')
      setSearchResults(result.data ?? [])
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setSearchLoading(false)
    }
  }

  const openSection = (sectionId: string, characterOffset = 0, query = '') => {
    setSelectedSectionId(sectionId)
    setTextWindowStart(sourceTextWindowStart(characterOffset))
    setHighlightQuery(query)
    setHighlightOffset(query ? characterOffset : null)
  }

  const sections = revision?.sections ?? []
  const selectedSection = sections.find((section) => section.id === selectedSectionId)
  const totalCharacters = Number(sectionWindow?.total_character_count ?? sourceSectionCharacterCount(selectedSection ?? sections[0])) || 0
  const currentStart = Number(sectionWindow?.text_start_character ?? textWindowStart)
  const currentEnd = Number(sectionWindow?.text_end_character ?? currentStart)
  const highlightIndex = highlightOffset == null ? undefined : highlightOffset - currentStart
  const revisions = work.revisions ?? []

  React.useEffect(() => {
    const container = documentScrollRef.current
    if (!container || textLoading || !highlightQuery || !sectionWindow) return
    const frame = window.requestAnimationFrame(() => {
      const mark = container.querySelector('mark')
      if (!mark) return
      const containerRect = container.getBoundingClientRect()
      const markRect = mark.getBoundingClientRect()
      const centeredTop = container.scrollTop + markRect.top - containerRect.top
        - (container.clientHeight - markRect.height) / 2
      container.scrollTo({ top: Math.max(0, centeredTop), behavior: 'auto' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [highlightIndex, highlightQuery, sectionWindow, textLoading])

  return <section className="novel-source-reader">
    <header className="novel-source-reader-toolbar">
      <div>
        <strong>只读原文</strong>
        <span>{revision ? `${sections.length} 节 · ${revision.character_count.toLocaleString()} 字符` : '正在读取来源结构'}</span>
      </div>
      {revisions.length > 1 ? <PurrSelect
        aria-label="来源版本"
        value={revisionId || null}
        options={revisions.map((item) => ({
          value: item.id,
          label: `版本 ${item.version_no} · ${item.character_count.toLocaleString()} 字符`,
        }))}
        onChange={(value) => onRevisionChange(String(value ?? ''))}
      /> : <span className="novel-source-reader-version">版本 {revisions[0]?.version_no ?? 1}</span>}
    </header>
    <div className="novel-source-reader-body">
      <aside className="novel-source-reader-sidebar">
        <PurrInput.Search
          aria-label="搜索原文"
          value={searchDraft}
          allowClear
          prefix={<SearchIcon />}
          placeholder="搜索原文"
          enterButton="搜索"
          loading={searchLoading}
          onChange={(event) => {
            setSearchDraft(event.target.value)
            if (!event.target.value) {
              setSearchedQuery('')
              setSearchResults([])
              setHighlightQuery('')
              setHighlightOffset(null)
            }
          }}
          onSearch={(value) => void searchSource(value)}
        />
        {searchedQuery ? <div className="novel-source-search-results">
          <div><strong>搜索结果</strong><span>{searchResults.length} 节</span></div>
          {searchResults.length ? searchResults.map((result) => <button
            type="button"
            key={`${result.id}:${result.start_character}`}
            onClick={() => openSection(result.id, result.start_character, searchedQuery)}
          >
            <strong>{result.title}</strong>
            <span>{result.excerpt}</span>
          </button>) : !searchLoading ? <p>没有找到匹配原文。</p> : null}
        </div> : null}
        <nav className="novel-source-section-list" aria-label="原文章节目录">
          <div><strong>章节目录</strong><span>{sections.length}</span></div>
          {sections.map((section) => {
            const characterCount = sourceSectionCharacterCount(section)
            const isOnlySection = sections.length === 1
            return <button
              type="button"
              key={section.id}
              className={section.id === selectedSectionId ? 'is-active' : undefined}
              aria-current={section.id === selectedSectionId ? 'location' : undefined}
              onClick={() => openSection(section.id)}
            >
              <span>{String(section.ordinal + 1).padStart(2, '0')}</span>
              <div><strong>{section.title}</strong><small>{characterCount == null ? '—' : `${characterCount.toLocaleString()} 字符`}</small></div>
              {isOnlySection ? <em>单节</em> : characterCount != null && characterCount > LONG_SOURCE_SECTION_CHARACTERS ? <em>超长</em> : null}
            </button>
          })}
        </nav>
      </aside>
      <article className="novel-source-document">
        {revisionLoading ? <div className="novel-source-document-state"><PurrSpin /></div> : selectedSection ? <>
          <header>
            <div><span>第 {selectedSection.ordinal + 1} 节</span><h2>{selectedSection.title}</h2></div>
            <span>{totalCharacters ? `${Math.min(currentStart + 1, totalCharacters).toLocaleString()}–${currentEnd.toLocaleString()} / ${totalCharacters.toLocaleString()} 字符` : '0 字符'}</span>
          </header>
          <div ref={documentScrollRef} className="novel-source-document-scroll">
            {textLoading ? <div className="novel-source-document-state"><PurrSpin /></div> : <pre>{highlightedSourceText(sectionWindow?.text_content ?? '', highlightQuery, highlightIndex)}</pre>}
          </div>
          <footer>
            <PurrButton
              size="small"
              disabled={textLoading || currentStart <= 0}
              onClick={() => setTextWindowStart(Math.max(0, currentStart - SOURCE_TEXT_WINDOW_SIZE))}
            >上一段</PurrButton>
            <span>{totalCharacters > SOURCE_TEXT_WINDOW_SIZE ? '长章节已分段显示' : '完整章节'}</span>
            <PurrButton
              size="small"
              disabled={textLoading || currentEnd >= totalCharacters}
              onClick={() => setTextWindowStart(currentEnd)}
            >下一段</PurrButton>
          </footer>
        </> : <div className="novel-source-document-state">这个版本没有可读取的原文章节。</div>}
      </article>
    </div>
  </section>
}

export default function NovelSourcesPage({
  books,
  modelConfigs,
  onUpdateModelConfig,
  onBack,
  onHome,
  onOpenSettings,
}: {
  books: Book[]
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: AgentConversationController['composer']['updateModel']
  onBack(): void
  onHome(): void
  onOpenSettings(): void
}) {
  const appMessage = usePurrToast()
  const confirmDialog = usePurrConfirm()
  const location = useLocation()
  const navigate = useNavigate()
  const { workId } = useParams<{ workId?: string }>()
  const [works, setWorks] = React.useState<NovelSourceWork[]>([])
  const [detailWork, setDetailWork] = React.useState<NovelSourceWork | null>(null)
  const [sourceFilter, setSourceFilter] = React.useState<SourceFilter>('all')
  const [sourceRevisionId, setSourceRevisionId] = React.useState('')
  const [sourceReaderOpen, setSourceReaderOpen] = React.useState(false)
  const [sourceReaderTarget, setSourceReaderTarget] = React.useState<NovelSourceEvidenceTarget | null>(null)
  const [analysisResultOpen, setAnalysisResultOpen] = React.useState(false)
  const [evidenceView, setEvidenceView] = React.useState<{
    title: string
    evidence: NovelAnalysisEvidence[]
    focus?: {
      kind: 'overview' | 'fact' | 'craft'
      heading: string
      body: string
    }
  } | null>(null)
  const [importOpen, setImportOpen] = React.useState(false)
  const [importView, setImportView] = React.useState<ImportView>('choose')
  const [picked, setPicked] = React.useState<NovelSourcePickedFile | null>(null)
  const [preview, setPreview] = React.useState<NovelSourceImportPreview | null>(null)
  const [importSections, setImportSections] = React.useState<NovelSourceImportPreview['sections']>([])
  const [splitSection, setSplitSection] = React.useState<{ index: number; marker: string } | null>(null)
  const [title, setTitle] = React.useState('')
  const [freezeBookId, setFreezeBookId] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [analysisRevisionId, setAnalysisRevisionId] = React.useState('')
  const [analysisModelId, setAnalysisModelId] = React.useState(modelConfigs[0]?.id ?? '')
  const [analysisComposer] = React.useState(() => createScopedComposer<AnalysisSubmissionSnapshot>())
  const [composerVersion, refreshComposer] = React.useReducer(value => value + 1, 0)
  const [sessions, setSessions] = React.useState<AnalysisSession[]>([])
  const [selectedSession, setSelectedSession] = React.useState<{revision: string; id: string} | null>(null)
  const activeSession = selectedSession?.revision === analysisRevisionId ? selectedSession.id : `legacy:${analysisRevisionId}`
  const activeSessionRef = React.useRef(activeSession)
  activeSessionRef.current = activeSession
  const loadSessions = React.useCallback(async (revision: string) => {
    const result = await analysisSessions.list(revision)
    if (analysisRevisionRef.current !== revision) return
    if (result.success && result.data) {
      setSessions(result.data)
      if (!result.data.some(session => session.id === activeSessionRef.current && !session.closed))
        setSelectedSession({revision, id: result.data.find(session => !session.closed)?.id || ''})
    }
    else appMessage.error(result.error || '读取对话列表失败')
  }, [appMessage])
  React.useEffect(() => {
    setSessions([])
    if (analysisRevisionId) void loadSessions(analysisRevisionId)
  }, [analysisRevisionId, loadSessions])
  const analysisScope = workId ? `${workId}:${detailWork?.id === workId && analysisRevisionId ? analysisRevisionId : 'pending'}:${activeSession}` : ''
  const analysisInput = analysisScope ? analysisComposer.draft(analysisScope) : ''
  const setAnalysisInput = (value: string) => {
    if (!analysisScope) return
    analysisComposer.setDraft(analysisScope, value)
    refreshComposer()
  }
  const [analysisRuns, setAnalysisRuns] = React.useState<NovelAnalysisRun[]>([])
  const [hydratedAnalysisRevision, setHydratedAnalysisRevision] = React.useState('')
  const [recoveryError, setRecoveryError] = React.useState('')
  const [recoveryProgress, setRecoveryProgress] = React.useState(0)
  const [recoveryAttempt, setRecoveryAttempt] = React.useState(0)
  const [replayedAnalysis, setReplayedAnalysis] = React.useState<{
    runId: string
    message: AgentConversationMessage
    history?: Record<string, AgentConversationMessage>
  } | null>(null)
  const [watchedAnalysisCommandId, setWatchedAnalysisCommandId] = React.useState('')
  const watchedAnalysisCommandRef = React.useRef(watchedAnalysisCommandId)
  watchedAnalysisCommandRef.current = watchedAnalysisCommandId
  const analysisRevisionRef = React.useRef('')
  const detailGeneration = React.useRef(0)
  const currentWorkRef = React.useRef(workId)
  if (currentWorkRef.current !== workId) {
    currentWorkRef.current = workId
    analysisRevisionRef.current = ''
  }
  const [publishedAnalyses, setPublishedAnalyses] = React.useState<PublishedNovelAnalysis[]>([])
  const [analysisArtifact, setAnalysisArtifact] = React.useState<NovelAnalysisArtifact | null>(null)
  const [publishedAnalysisId, setPublishedAnalysisId] = React.useState('')
  const [loadingLibraryAnalysis, setLoadingLibraryAnalysis] = React.useState('')

  const openLibraryAnalysis = async (work: NovelSourceWork) => {
    const generation = ++detailGeneration.current
    setLoadingLibraryAnalysis(work.id)
    setAnalysisResultOpen(false)
    setEvidenceView(null)
    try {
      const detail = await services.novelSources.get({ workId: work.id })
      if (!detail.success || !detail.data) throw new Error(detail.error || '读取来源失败')
      if (work.unsaved_analysis_artifact_id) {
        const artifact = await services.novelSources.getAnalysisArtifact({ artifactId: work.unsaved_analysis_artifact_id })
        if (!artifact.success || !artifact.data) throw new Error(artifact.error || '读取未保存结果失败')
        if (generation !== detailGeneration.current) return
        setDetailWork(detail.data)
        setSourceRevisionId(work.unsaved_analysis_revision_id || artifact.data.sourceRevisionId)
        setAnalysisArtifact(artifact.data)
        setPublishedAnalysisId('')
        setAnalysisResultOpen(true)
        return
      }
      const results = await Promise.all((detail.data.revisions ?? []).map(revision => services.novelSources.listPublishedAnalyses({ revisionId: revision.id })))
      const failed = results.find(result => !result.success)
      if (failed) throw new Error(failed.error || '读取分析结果失败')
      const latest = results.flatMap(result => result.data ?? []).sort((a, b) => b.createTime.localeCompare(a.createTime) || b.versionNo - a.versionNo)[0]
      if (!latest) throw new Error('该来源尚无已保存的分析结果')
      const artifactId = latest.summary.artifactId
      if (typeof artifactId !== 'string' || !artifactId) throw new Error('该历史分析缺少结果引用，无法打开')
      const artifact = await services.novelSources.getAnalysisArtifact({ artifactId })
      if (!artifact.success || !artifact.data) throw new Error(artifact.error || '读取分析结果失败')
      if (generation !== detailGeneration.current) return
      setDetailWork(detail.data)
      setSourceRevisionId(latest.sourceRevisionId)
      setAnalysisArtifact(artifact.data)
      setPublishedAnalysisId(latest.id)
      setAnalysisResultOpen(true)
    } catch (error) {
      if (generation === detailGeneration.current) appMessage.error((error as Error).message)
    } finally {
      if (generation === detailGeneration.current) setLoadingLibraryAnalysis('')
    }
  }

  const reload = React.useCallback(async () => {
    const result = await services.novelSources.list()
    if (result.success) setWorks(result.data ?? [])
  }, [])

  const reloadAnalysis = React.useCallback(async (revisionId: string) => {
    if (!revisionId) return null
    const generation = detailGeneration.current
    const runsResult = await services.novelSources.listAnalysisRuns({ revisionId })
    if (analysisRevisionRef.current !== revisionId || detailGeneration.current !== generation) return null
    if (!runsResult.success) {
      appMessage.error(runsResult.error || '读取分析进度失败')
      return null
    }
    const nextRuns = runsResult.data ?? []
    setAnalysisRuns(nextRuns)
    return nextRuns
  }, [appMessage])

  const reloadPublishedAnalysis = React.useCallback(async (revisionId: string) => {
    const generation = detailGeneration.current
    const publishedResult = await services.novelSources.listPublishedAnalyses({ revisionId })
    if (analysisRevisionRef.current !== revisionId || detailGeneration.current !== generation) return
    if (publishedResult.success) {
      setPublishedAnalyses(publishedResult.data ?? [])
    }
  }, [])

  React.useEffect(() => {
    if (analysisResultOpen) return
    const published = publishedAnalyses[0]
    const run = analysisRuns[0]
    const covers = published && (run?.interactionKind === 'follow_up'
      || !run?.createTime || Date.parse(published.createTime) >= Date.parse(run.createTime))
    setPublishedAnalysisId(covers ? published.id : '')
  }, [publishedAnalyses, analysisRuns, analysisResultOpen])

  const loadDetail = React.useCallback(async (id: string) => {
    const generation = ++detailGeneration.current
    analysisRevisionRef.current = ''
    setBusy(false)
    setHydratedAnalysisRevision('')
    setWatchedAnalysisCommandId('')
    setReplayedAnalysis(null)
    const result = await services.novelSources.get({ workId: id })
    if (generation !== detailGeneration.current || currentWorkRef.current !== id) return
    if (!result.success || !result.data) {
      appMessage.error(result.error || '来源不存在')
      navigate('/novel-sources', { replace: true })
      return
    }
    setDetailWork(result.data)
    const latestRevisionId = result.data.latest_revision_id || result.data.revisions?.[0]?.id || ''
    analysisComposer.adoptDraft(`${id}:pending`, `${id}:${latestRevisionId}`)
    analysisRevisionRef.current = latestRevisionId
    setSourceReaderOpen(false)
    setSourceReaderTarget(null)
    setAnalysisResultOpen(false)
    setEvidenceView(null)
    setSourceRevisionId(latestRevisionId)
    setAnalysisRevisionId(latestRevisionId)
    setAnalysisRuns([])
    setPublishedAnalyses([])
    setAnalysisArtifact(null)
    setPublishedAnalysisId('')
  }, [analysisComposer, appMessage, navigate])

  const selectSourceRevision = React.useCallback((revisionId: string) => {
    if (!revisionId) return
    detailGeneration.current += 1
    setBusy(false)
    setHydratedAnalysisRevision('')
    analysisRevisionRef.current = revisionId
    setSourceRevisionId(revisionId)
    setSourceReaderTarget(null)
    setAnalysisResultOpen(false)
    setAnalysisRevisionId(revisionId)
    setAnalysisRuns([])
    setReplayedAnalysis(null)
    setWatchedAnalysisCommandId('')
    setPublishedAnalyses([])
    setAnalysisArtifact(null)
    setPublishedAnalysisId('')
  }, [])

  React.useEffect(() => { void reload() }, [reload])
  React.useEffect(() => {
    setLoadingLibraryAnalysis('')
    if (workId) void loadDetail(workId)
    else {
      setDetailWork(null)
      setSourceRevisionId('')
      setAnalysisRevisionId('')
      setAnalysisRuns([])
      setReplayedAnalysis(null)
      setSourceReaderOpen(false)
    }
  }, [loadDetail, workId])
  React.useEffect(() => () => {
    detailGeneration.current += 1
    analysisRevisionRef.current = ''
  }, [])
  React.useEffect(() => {
    const target = (location.state as { sourceEvidenceTarget?: NovelSourceEvidenceTarget } | null)?.sourceEvidenceTarget
    if (!detailWork || !target || detailWork.id !== workId) return
    setSourceRevisionId(target.revisionId)
    setSourceReaderTarget(target)
    setSourceReaderOpen(true)
    navigate(location.pathname, { replace: true, state: null })
  }, [detailWork, location.pathname, location.state, navigate, workId])
  React.useEffect(() => {
    if (!modelConfigs.some(model => model.id === analysisModelId)) setAnalysisModelId(modelConfigs[0]?.id ?? '')
  }, [analysisModelId, modelConfigs])
  const hasActiveAnalysis = analysisRuns.some(isAnalysisRunActive)
  const hasBlockingAnalysis = analysisRuns.some(isAnalysisRunBlocking)

  const runtime = React.useCallback(() => {
    const model = modelConfigs.find((item) => item.id === analysisModelId)
    if (!model) throw new Error('请先在设置中配置分析模型')
    return analysisRuntimeForModel(model)
  }, [analysisModelId, modelConfigs])

  const startAnalysis = async (
    revisionId: string,
    prompt = '提取人物、背景、设定与情节资料，保留来源依据，提炼语言、节奏、铺垫和风格等可执行写作技法。',
    frozenRuntime = runtime(),
  ) => {
    const generation = detailGeneration.current
    const isCurrent = () => generation === detailGeneration.current && analysisRevisionRef.current === revisionId
    const decision = await confirmDialog({
      title: analysisRuns.length ? '重新分析来源' : '开始来源分析',
      content: '按需读取原文，生成新的分析结果。已有结果和历史记录会保留。',
      confirmText: '开始分析',
    })
    if (decision !== 'confirm' || !isCurrent()) return false
    const commandId = `novel-analysis-${crypto.randomUUID()}`
    let accepted = false
    setBusy(true)
    try {
      const result = await services.novelSources.startAnalysis({
        revisionId,
        conversationId: activeSession,
        commandId,
        prompt,
        runtime: frozenRuntime,
      })
      if (!result.success) throw new Error(result.error || '启动分析失败')
      accepted = true
      if (!isCurrent()) return true
      if (import.meta.env.DEV) setAiDebugInspectorVisible(true)
      setAnalysisRevisionId(revisionId)
      setWatchedAnalysisCommandId(commandId)
      appMessage.success('任务已接收，正在准备分析')
      await reloadAnalysis(revisionId)
      return true
    } catch (error) {
      if (isCurrent()) {
        if (!accepted) setWatchedAnalysisCommandId('')
        appMessage.error((error as Error).message)
      }
      return accepted
    } finally {
      if (isCurrent()) setBusy(false)
    }
  }

  const followUpAnalysis = async (prompt: string, frozenRuntime = runtime(), conversationId = activeSession, replaceRunId?: string) => {
    if (!analysisRevisionId) return false
    const generation = detailGeneration.current
    const isCurrent = () => generation === detailGeneration.current && analysisRevisionRef.current === analysisRevisionId
    const commandId = `novel-analysis-follow-up-${crypto.randomUUID()}`
    let accepted = false
    setBusy(true)
    try {
      const resultRun = analysisRuns.find(run =>
        (run.conversationId || `legacy:${analysisRevisionId}`) === conversationId
        && (run.artifactRef || run.analysisArtifactRef))
      const resultRef = resultRun?.artifactRef || resultRun?.analysisArtifactRef
      const result = await services.novelSources.followUpAnalysis({
        revisionId: analysisRevisionId,
        conversationId,
        artifactId: replaceRunId || !resultRef ? undefined : novelAnalysisArtifactId(resultRef),
        replaceRunId,
        prompt,
        commandId,
        runtime: frozenRuntime,
      })
      if (!result.success) throw new Error(result.error || '发送追问失败')
      accepted = true
      if (!isCurrent()) return true
      if (import.meta.env.DEV) setAiDebugInspectorVisible(true)
      setWatchedAnalysisCommandId(commandId)
      appMessage.success(replaceRunId ? '已重新发送' : '追问已发送')
      await reloadAnalysis(analysisRevisionId)
      return true
    } catch (error) {
      if (isCurrent()) {
        if (!accepted) setWatchedAnalysisCommandId('')
        appMessage.error((error as Error).message)
      }
      return accepted
    } finally {
      if (isCurrent()) setBusy(false)
    }
  }

  const openArtifact = React.useCallback(async (reference: string) => {
    const generation = detailGeneration.current
    const artifactId = novelAnalysisArtifactId(reference)
    if (!artifactId) return appMessage.error('分析结果引用无效')
    const result = await services.novelSources.getAnalysisArtifact({ artifactId })
    if (generation !== detailGeneration.current) return
    if (!result.success || !result.data) return appMessage.error(result.error || '读取分析结果失败')
    setAnalysisArtifact(result.data)
    setPublishedAnalysisId('')
  }, [appMessage])

  React.useEffect(() => {
    if (analysisResultOpen) return
    const reference = analysisRuns[0]?.artifactRef || analysisRuns[0]?.analysisArtifactRef
    if (!reference || novelAnalysisArtifactId(reference) === analysisArtifact?.artifactId) return
    void openArtifact(reference)
  }, [analysisArtifact, analysisRuns, openArtifact, analysisResultOpen])

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
              retryFailed: durableAnalysisStatus(run) === 'failed',
              runtime: runtime(),
            })
      if (!result.success) throw new Error(result.error || '分析任务操作失败')
      if (action === 'resume') setWatchedAnalysisCommandId(resumeCommandId)
      await reloadAnalysis(analysisRevisionId)
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
        facts: analysisArtifact.facts,
        craftCards: analysisArtifact.craftCards,
        storyOverview: analysisArtifact.storyOverview,
        techniqueResult: analysisArtifact.techniqueResult,
        analysisTechniqueResult: analysisArtifact.analysisTechniqueResult,
      })
      if (!reviewed.success || !reviewed.data) throw new Error(reviewed.error || '保存审核结果失败')
      const published = await services.novelSources.publishAnalysisArtifact({ artifactId: reviewed.data.artifactId })
      if (!published.success || !published.data) throw new Error(published.error || '保存分析失败')
      setAnalysisArtifact(reviewed.data)
      setPublishedAnalysisId(published.data.id)
      if (workId) await reloadAnalysis(analysisRevisionId)
      else await reload()
      appMessage.success('分析结果已保存')
    } catch (error) {
      appMessage.error((error as Error).message || '保存分析失败')
    } finally {
      setBusy(false)
    }
  }

  const resetImport = () => {
    setImportOpen(false)
    setImportView('choose')
    setPicked(null)
    setPreview(null)
    setImportSections([])
    setSplitSection(null)
    setTitle('')
    setFreezeBookId('')
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
      setImportSections(result.data.sections)
      setTitle(result.data.suggestedTitle)
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
      sections: importSections.map((section) => ({
        title: section.title,
        startCharacter: section.startCharacter,
        endCharacter: section.endCharacter,
      })),
    })
    setBusy(false)
    if (!result.success || !result.data) return appMessage.error(result.error || '导入失败')
    resetImport()
    await reload()
    navigate(`/novel-sources/${encodeURIComponent(result.data.work_id)}`)
  }

  const updateImportSectionTitle = (index: number, nextTitle: string) => {
    setImportSections((current) => current.map((section, itemIndex) => (
      itemIndex === index ? { ...section, title: nextTitle } : section
    )))
  }

  const mergeImportSectionWithNext = (index: number) => {
    setImportSections((current) => {
      const first = current[index]
      const second = current[index + 1]
      if (!picked || !first || !second) return current
      const merged = {
        ...first,
        endCharacter: second.endCharacter,
        characterCount: second.endCharacter - first.startCharacter,
        preview: importSectionPreview(picked.content, first.startCharacter, second.endCharacter),
      }
      const next = current.filter((_, itemIndex) => itemIndex !== index + 1)
      next[index] = merged
      return next.map((section, ordinal) => ({ ...section, ordinal }))
    })
    setSplitSection(null)
  }

  const splitImportSectionAtMarker = () => {
    if (!picked || !splitSection) return
    const section = importSections[splitSection.index]
    const marker = splitSection.marker
    if (!section || !marker.trim()) return appMessage.info('请粘贴新章节开头的一小段原文')
    const text = picked.content.slice(section.startCharacter, section.endCharacter)
    const firstMatch = text.indexOf(marker)
    if (firstMatch <= 0) return appMessage.info('没有在当前章节中找到这段文字，或它已经位于章节开头')
    if (text.indexOf(marker, firstMatch + marker.length) >= 0) {
      return appMessage.info('这段文字在当前章节中出现多次，请多粘贴一些文字以便唯一定位')
    }
    const splitAt = section.startCharacter + firstMatch
    const first = {
      ...section,
      endCharacter: splitAt,
      characterCount: splitAt - section.startCharacter,
      preview: importSectionPreview(picked.content, section.startCharacter, splitAt),
    }
    const second = {
      ...section,
      title: importSectionTitle(picked.content, splitAt, `第 ${splitSection.index + 2} 节`),
      startCharacter: splitAt,
      characterCount: section.endCharacter - splitAt,
      preview: importSectionPreview(picked.content, splitAt, section.endCharacter),
    }
    setImportSections((current) => [
      ...current.slice(0, splitSection.index),
      first,
      second,
      ...current.slice(splitSection.index + 1),
    ].map((item, ordinal) => ({ ...item, ordinal })))
    setSplitSection(null)
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
      content: '这会永久删除来源正文、全部冻结版本、分析结果和原文证据，无法恢复。续写与正史快照会保留；已被写作方法或方案引用的来源不能删除，可以改为归档。',
      confirmText: '仍要删除',
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
  const selectedAnalysisModel = modelConfigs.find((model) => model.id === analysisModelId)
  const waitingForRun = Boolean(watchedAnalysisCommandId) && !analysisRuns.some((run) => run.commandId === watchedAnalysisCommandId)
  const sessionRuns = analysisRuns.filter(run => (run.conversationId || `legacy:${analysisRevisionId}`) === activeSession)
  const latestRun = sessionRuns[0]
  const analysisReady = Boolean(analysisRevisionId && detailWork?.id === workId
    && analysisRevisionRef.current === analysisRevisionId && hydratedAnalysisRevision === analysisRevisionId)
  const analysisPending = Boolean(analysisScope && analysisComposer.pending(analysisScope))
  const analysisQueued = analysisScope ? analysisComposer.queue(analysisScope) : []
  const sendAnalysisMessage = (content?: string, replaceRunId?: string) => {
    const prompt = String(content ?? analysisInput).trim()
    if (!prompt || !activeSession || !analysisReady || !selectedAnalysisModel
      || analysisRevisionRef.current !== analysisRevisionId || currentWorkRef.current !== workId) return
    analysisComposer.enqueue(analysisScope, `novel-analysis-queue-${crypto.randomUUID()}`,
      prompt, { replaceRunId, revisionId: analysisRevisionId, conversationId: activeSession, runtime: runtime() }, content == null)
    refreshComposer()
  }
  React.useEffect(() => {
    if (!analysisReady || hasActiveAnalysis || waitingForRun || busy) return
    if ((latestRun?.artifactRef || latestRun?.analysisArtifactRef) && !analysisArtifact) return
    const submission = analysisComposer.claim(analysisScope)
    if (!submission) return
    refreshComposer()
    void (async () => {
      let accepted = false
      try {
        accepted = await followUpAnalysis(submission.content, submission.snapshot.runtime, submission.snapshot.conversationId, submission.snapshot.replaceRunId)
      } catch (error) {
        if (analysisRevisionRef.current === submission.snapshot.revisionId) appMessage.error((error as Error).message)
      } finally {
        analysisComposer.settle(submission, accepted)
        refreshComposer()
      }
    })()
  }, [analysisReady, analysisScope, analysisComposer, composerVersion, hasActiveAnalysis,
    waitingForRun, busy, latestRun, analysisArtifact, startAnalysis, followUpAnalysis])
  React.useEffect(() => {
    if (!analysisRevisionId || analysisRevisionRef.current !== analysisRevisionId) return
    const controller = new AbortController()
    const stream = new NovelAnalysisConversationStream()
    setRecoveryError('')
    setRecoveryProgress(0)
    setHydratedAnalysisRevision('')
    let publishedId: string | null | undefined
    let currentRun: NovelAnalysisRun | undefined
    let initialHistoryHydrated = false
    void services.novelSources.consumeAnalysisEvents({
      revisionId: analysisRevisionId, signal: controller.signal,
      onEvent: async page => {
        if (controller.signal.aborted || analysisRevisionRef.current !== analysisRevisionId) return
        if (page.runs) {
          currentRun = page.runs[0]
          setAnalysisRuns(page.runs)
          const watched = page.runs.find(run => run.commandId === watchedAnalysisCommandRef.current)
          if (watched && !isAnalysisRunActive(watched)) setWatchedAnalysisCommandId('')
          initialHistoryHydrated = true
        }
        const replayed = await stream.applyAsync(page, selectedAnalysisModel, value => {
          setReplayedAnalysis(value)
        }, () => !controller.signal.aborted && analysisRevisionRef.current === analysisRevisionId)
        if (controller.signal.aborted || analysisRevisionRef.current !== analysisRevisionId) return
        if (!page.chunks.length) setReplayedAnalysis(replayed ?? null)
        if (page.hasMore) setRecoveryProgress(value => value + page.chunks.length)
        // The first response page only establishes the subscription. Keep the
        // shared conversation in its recovery state until all historical event
        // pages have been replayed.
        if (initialHistoryHydrated && !page.hasMore) {
          setHydratedAnalysisRevision(analysisRevisionId)
        }
        if (import.meta.env.DEV) {
          for (const event of page.chunks) recordAgentConversationDebugChunk({
            runId: event.runId, turnId: currentRun?.commandId,
            conversationRootRunId: currentRun?.runId,
            source: '小说来源分析', prompt: currentRun?.prompt || '',
            model: selectedAnalysisModel?.name, chunk: event.chunk,
          })
        }
        if ('publishedId' in page && page.publishedId !== publishedId) {
          publishedId = page.publishedId
          void reloadPublishedAnalysis(analysisRevisionId).catch(error => {
            if (!controller.signal.aborted) appMessage.error(error.message)
          })
        }
      },
    }).catch(error => {
      if (!controller.signal.aborted) {
        setRecoveryError(error instanceof Error ? error.message : '恢复对话失败')
      }
    })
    return () => controller.abort()
  }, [workId, analysisRevisionId, selectedAnalysisModel, reloadPublishedAnalysis, recoveryAttempt])

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
        <span>{preview.documentCount} 个文稿</span><span>{preview.characterCount.toLocaleString()} 字符</span><span>{importSections.length} 节</span>
        {preview.skippedFileCount > 0 ? <span>忽略 {preview.skippedFileCount} 个文件</span> : null}
      </div>
      <div className="novel-source-sections">{importSections.map((section, index) => <article key={`${section.startCharacter}:${section.endCharacter}`}>
        <div className="novel-source-section-edit-heading">
          <PurrInput aria-label={`第 ${index + 1} 节标题`} value={section.title} maxLength={300} onChange={(event) => updateImportSectionTitle(index, event.target.value)} />
          <span>{section.characterCount.toLocaleString()} 字符</span>
        </div>
        <p>{section.preview}</p>
        <div className="novel-source-section-edit-actions">
          <PurrButton type="text" size="small" onClick={() => setSplitSection({ index, marker: '' })}>拆分</PurrButton>
          {index < importSections.length - 1 ? <PurrButton type="text" size="small" onClick={() => mergeImportSectionWithNext(index)}>与下一节合并</PurrButton> : null}
        </div>
        {splitSection?.index === index ? <div className="novel-source-section-split">
          <span>粘贴新章节开头的一小段原文，系统会在这段文字前拆分。</span>
          <PurrInput.TextArea value={splitSection.marker} rows={3} onChange={(event) => setSplitSection({ index, marker: event.target.value })} placeholder="例如：第二章 雨夜" />
          <div><PurrButton size="small" onClick={() => setSplitSection(null)}>取消</PurrButton><PurrButton type="primary" size="small" onClick={splitImportSectionAtMarker}>确认拆分</PurrButton></div>
        </div> : null}
      </article>)}</div>
      <div className="novel-source-boundary">导入后保存为只读来源。发起 AI 分析时，相关正文会发送给你配置的模型服务。</div>
      <div className="novel-source-preview-actions">
        <PurrButton onClick={() => { setPicked(null); setPreview(null); setImportSections([]); setSplitSection(null) }}>重新选择</PurrButton>
        <PurrButton type="primary" disabled={busy || importSections.some((section) => !section.title.trim()) || !importSections.length} onClick={() => void confirmImport()}>确认导入</PurrButton>
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

  const selectedRevision = detailWork?.revisions?.find((item) => item.id === sourceRevisionId)
  const currentPlan = latestRun && latestRun.interactionKind !== 'follow_up'
    ? buildNovelAnalysisTaskPlan(latestRun)
    : undefined
  const workflowNotice = pausedAnalysisWorkflowNotice(latestRun)
  const conversationMessages: AgentConversationMessage[] = sessionRuns.length
    ? [...sessionRuns].reverse().flatMap(run => buildNovelAnalysisMessages(
        run, selectedAnalysisModel?.name || '',
        replayedAnalysis?.history?.[run.runId]
          ?? (replayedAnalysis?.runId === run.runId ? replayedAnalysis.message : undefined),
      ))
    : waitingForRun
      ? [{ role: 'assistant', content: '', model: selectedAnalysisModel?.name }]
      : []
  const renderAnalysisResult = (runId?: string) => {
    const run = sessionRuns.find(item => item.runId === runId)
    if (!run?.artifactRef) return null
    return <section className="novel-analysis-result-summary">
      <div className="novel-analysis-result-summary-copy"><span>分析结果</span></div>
      <div className="novel-analysis-result-summary-actions">
        <em className={run.publishedAnalysisId ? 'is-saved' : ''}>{run.publishedAnalysisId ? '已保存' : run.artifactRef.startsWith('novel-analysis-v1://') ? '待审核' : '待保存'}</em>
        <PurrButton size="small" onClick={async () => {
          const generation = detailGeneration.current
          const artifactId = novelAnalysisArtifactId(run.artifactRef!)
          if (!artifactId) { appMessage.error('分析结果引用无效'); return }
          const result = await services.novelSources.getAnalysisArtifact({ artifactId })
          if (generation !== detailGeneration.current) return
          if (!result.success || !result.data) { appMessage.error(result.error || '读取分析结果失败'); return }
          setAnalysisArtifact(result.data)
          setPublishedAnalysisId(run.publishedAnalysisId || '')
          setEvidenceView(null)
          setAnalysisResultOpen(true)
        }}>查看分析结果</PurrButton>
      </div>
    </section>
  }
  const showEvidence = (
    title: string,
    evidence: NovelAnalysisEvidence[],
    focus?: { kind: 'overview' | 'fact' | 'craft'; heading: string; body: string },
  ) => {
    setEvidenceView({ title, evidence, focus })
  }
  const locateEvidence = (target: NovelSourceEvidenceTarget) => {
    setSourceRevisionId(target.revisionId)
    setSourceReaderTarget(target)
    setSourceReaderOpen(true)
  }
  const analysisResultActions = analysisArtifact ? <div className="novel-analysis-result-actions">
    {analysisArtifact.publicationSupported === false
      ? <small>当前分析结果仅供查看，尚不能写入正式来源资料。</small>
      : analysisArtifact.artifactContract !== 'purrtypos.novel_analysis.review.v1' && analysisArtifact.analysisSchemaVersion !== 3
        ? <small>旧版分析仅供查看，请重新发起分析以生成新版写作技法。</small>
        : <PurrButton type="primary" disabled={busy} onClick={() => void saveAnalysis()}>{publishedAnalysisId ? '保存修改' : '保存分析结果'}</PurrButton>}
  </div> : null
  const analysisResultDetail = analysisArtifact ? <section className="novel-analysis-result-detail">
    <PurrTabs
      className="novel-analysis-result-tabs"
      defaultActiveKey="characters"
      destroyOnHidden
      items={[
        {
          key: 'overview',
          label: '历史概览',
          children: <section className="novel-analysis-result-tab">
            {analysisArtifact.storyOverview ? <>
              <div className="novel-analysis-result-overview"><Markdown>{analysisArtifact.storyOverview.summaryMarkdown}</Markdown></div>
              <PurrButton type="text" size="small" aria-label={`查看 ${analysisArtifact.storyOverview.evidence.length} 条原文证据`} icon={<ArrowRightIcon />} iconPosition="end" onClick={() => showEvidence('故事概览 · 原文证据', analysisArtifact.storyOverview!.evidence, { kind: 'overview', heading: '全局故事概览', body: analysisArtifact.storyOverview!.summaryMarkdown })}>原文证据 {analysisArtifact.storyOverview.evidence.length}</PurrButton>
            </> : <p className="novel-analysis-result-empty">当前分析未保留历史概览。</p>}
          </section>,
        },
        {
          key: 'craft',
          label: '写作技法',
          children: <section className="novel-analysis-result-tab">
            {analysisArtifact.analysisTechniqueResult?.status === 'generated'
              ? analysisArtifact.analysisTechniqueResult.techniques.map((technique, index) => <article key={`${technique.title}-${index}`} className="novel-analysis-result-overview">
                <h4>{technique.title}</h4>
                <Markdown>{technique.bodyMarkdown}</Markdown>
              </article>)
              : analysisArtifact.analysisTechniqueResult?.status === 'empty'
                ? <p className="novel-analysis-result-empty">{analysisArtifact.analysisTechniqueResult.reason || '当前材料不足以形成可复用写作技法。'}</p>
                : null}
            {analysisArtifact.artifactContract !== 'purrtypos.novel_analysis.review.v1' ? <>
              <SourceTechniqueResults key={publishedAnalysisId || 'unsaved'} analysisId={publishedAnalysisId}
                canAdd={analysisArtifact.techniqueResult?.status === 'generated'} />
              <WritingSkillReview artifact={analysisArtifact} onTechniqueResultChange={(techniqueResult) => {
                setAnalysisArtifact(current => current ? { ...current, techniqueResult } : current)
              }} />
            </> : null}
            <WritingTechniqueEvidence scopeNotes={analysisArtifact.techniqueResult?.scopeNotes ?? []} cards={analysisArtifact.craftCards} onShowEvidence={(title, evidence, body) => showEvidence(title, evidence, { kind: 'craft', heading: title, body })} />
          </section>,
        },
      ].filter(item => item.key !== 'overview' || Boolean(analysisArtifact.storyOverview)).concat(materialGroups.filter(group => group.key !== 'unclassified' || analysisArtifact.facts.some(fact => belongsToMaterialGroup(fact.factKind, group))).map(group => ({
        key: group.key, label: group.label,
        children: <section className="novel-analysis-result-tab">
          {group.key === 'outline' && <p>整理原作已发生的情节与时间线，创建续写时写入故事大纲；后续剧情规划留空。</p>}
          {group.key === 'threads' && <p>整理未决情节与伏笔，创建续写时将仍有效的事项加入剧情线管理，保留已解决事项作为来源资料。</p>}
          <AnalysisMaterials facts={analysisArtifact.facts.filter(fact => belongsToMaterialGroup(fact.factKind, group))}
            onChange={(previous, next) => { setAnalysisArtifact(current => current ? { ...current, facts: current.facts.map(fact => fact === previous ? next : fact) } : current); setPublishedAnalysisId('') }}
            onEvidence={fact => showEvidence(`${fact.subjectKey} · 来源依据`, fact.evidence, { kind: 'fact', heading: fact.subjectKey, body: `${fact.predicate} · ${displayFactValue(fact.value)}` })} />
        </section>,
      }))).sort((left, right) => ['characters', 'background', 'world', 'outline', 'threads', 'craft', 'unclassified', 'overview'].indexOf(left.key) - ['characters', 'background', 'world', 'outline', 'threads', 'craft', 'unclassified', 'overview'].indexOf(right.key))}
    />
  </section> : null
  const resultModals = <>
    {detailWork ? <PurrModal
      title={`${detailWork.title} · 原文`}
      open={sourceReaderOpen}
      width="min(1280px, calc(100vw - 48px))"
      zIndex={1310}
      footer={null}
      onCancel={() => {
        setSourceReaderOpen(false)
        setSourceReaderTarget(null)
      }}
      className="novel-source-reader-modal"
    >
      {sourceReaderOpen ? <NovelSourceReader
        work={detailWork}
        revisionId={sourceRevisionId}
        target={sourceReaderTarget}
        onRevisionChange={selectSourceRevision}
      /> : null}
    </PurrModal> : null}
    {analysisArtifact ? <PurrModal
      title={`分析结果 · ${analysisArtifact.facts.length} 条创作资料 · ${analysisArtifact.analysisTechniqueResult?.status === 'generated' || analysisArtifact.techniqueResult?.status === 'generated' || analysisArtifact.writingSkill ? '1 个写作技法' : '未生成写作技法'}`}
      open={analysisResultOpen}
      width="min(1180px, calc(100vw - 48px))"
      footer={analysisResultActions}
      onCancel={() => {
        setAnalysisResultOpen(false)
        setEvidenceView(null)
      }}
      className="novel-analysis-result-modal"
    >
      <div className="novel-analysis-result-layout">
        <div className={`novel-analysis-result-content${evidenceView?.focus ? ' is-hidden' : ''}`}>{analysisResultDetail}</div>
        {evidenceView?.focus ? <section className="novel-analysis-evidence-focus">
          <header>
            <PurrButton type="text" size="small" icon={<ArrowLeftIcon />} onClick={() => setEvidenceView(null)}>返回全部分析</PurrButton>
            <span>{evidenceView.focus.kind === 'overview' ? '历史概览与来源依据' : evidenceView.focus.kind === 'fact' ? '创作资料与来源依据' : '写作技法与来源依据'}</span>
          </header>
          <div className="novel-analysis-evidence-focus-body">
            <article className="novel-analysis-evidence-context">
              <span>{evidenceView.focus.kind === 'overview' ? '历史概览' : evidenceView.focus.kind === 'fact' ? '当前创作资料' : '当前写作技法'}</span>
              <h2>{evidenceView.focus.heading}</h2>
              {evidenceView.focus.kind !== 'fact'
                ? <Markdown>{evidenceView.focus.body}</Markdown>
                : <p>{evidenceView.focus.body}</p>}
              <small>{evidenceView.evidence.length} 条原文证据支撑这项分析</small>
            </article>
            <aside className="novel-analysis-evidence-panel" aria-label={evidenceView.title}>
              <header><strong>原文证据</strong><span>{evidenceView.evidence.length} 条</span></header>
              <div className="novel-analysis-evidence-panel-body">
                <NovelAnalysisEvidenceList
                  evidence={evidenceView.evidence}
                  sourceRevisionId={analysisArtifact.sourceRevisionId ?? analysisRevisionId}
                  onLocate={locateEvidence}
                />
              </div>
            </aside>
          </div>
        </section> : null}
      </div>
    </PurrModal> : null}
  </>

  if (!workId) {
    return <div className="novel-sources-page">
      <AppHeader
        title="小说来源库"
        navigation={{
          home: { label: '返回首页', onClick: onHome },
          back: { label: '返回书架', onClick: onBack },
        }}
        showActions
        onOpenSettings={onOpenSettings}
      />
      <main className="novel-sources-main">
        <div className="novel-sources-toolbar"><div><h1>小说来源库</h1><p>收藏原文，提取创作资料与写作技法。</p></div><PurrButton type="primary" icon={<ImportIcon />} onClick={() => setImportOpen(true)}>添加来源</PurrButton></div>
        <div className="novel-source-filter-row">
          <PurrSegmented<SourceFilter> value={sourceFilter} onChange={setSourceFilter} options={[
            { value: 'all', label: `全部 ${works.length}` },
            { value: 'external_text', label: '本地导入' },
            { value: 'frozen_book', label: '原创冻结' },
          ]} />
        </div>
        {filteredWorks.length ? <div className="novel-source-library-grid">{filteredWorks.map((work) => <article className={`novel-source-library-card ${work.source_type === 'frozen_book' ? 'tone-1' : ''}`} key={work.id}>
          <button className="novel-source-card-open" onClick={() => navigate(`/novel-sources/${encodeURIComponent(work.id)}`)}>
            <span className="novel-source-card-type">{work.source_type === 'frozen_book' ? '原创冻结' : '本地导入'}</span>
            <strong>{work.title}</strong>
            <div><span>只读原文</span><span className={(work.analysis_count ?? 0) > 0 ? 'is-ready' : ''}>{work.unsaved_analysis_artifact_id ? '分析结果未保存' : (work.analysis_count ?? 0) > 0 ? '已保存分析' : '待分析'}</span></div>
          </button>
          <div className="novel-source-card-actions">
            <PurrButton type="text" size="small" loading={loadingLibraryAnalysis === work.id} disabled={Boolean(loadingLibraryAnalysis) && loadingLibraryAnalysis !== work.id} onClick={() => work.unsaved_analysis_artifact_id || (work.analysis_count ?? 0) > 0 ? void openLibraryAnalysis(work) : navigate(`/novel-sources/${encodeURIComponent(work.id)}`)}>{work.unsaved_analysis_artifact_id ? '查看未保存结果' : (work.analysis_count ?? 0) > 0 ? '查看分析' : '开始分析'}</PurrButton>
            {(work.analysis_count ?? 0) > 0 &&
            <PurrButton type="text" size="small" icon={<ArrowRightIcon />} iconPosition="end" onClick={() => navigate('/bookshelf', { state: { createContinuationFrom: { workId: work.id }, returnTo: `${location.pathname}${location.search}${location.hash}` } })}>创建续写</PurrButton>}
          </div>
          <PurrTooltip title="删除来源"><PurrButton className="novel-source-card-delete" type="text" size="small" aria-label={`删除 ${work.title}`} icon={<DeleteIcon />} onClick={() => void deleteWork(work)} /></PurrTooltip>
        </article>)}</div> : <div className="novel-source-library-empty"><InboxIcon /><strong>还没有这类来源</strong><PurrButton onClick={() => setImportOpen(true)}>添加来源</PurrButton></div>}
      </main>
      {importModal}
      {resultModals}
    </div>
  }

  const analysisController = createAnalysisConversationController({
    conversation: {
      identity: `novel-analysis:${analysisScope}`,
      sessions: sessions.filter(session => !session.closed),
      activeSessionId: activeSession,
      history: { sessions, loading: false },
      messages: conversationMessages,
      activities: {},
      queuedSubmissions: analysisQueued.map(item => ({ id: item.id, sessionId: analysisScope, content: item.content })),
      queuePaused: analysisComposer.held(analysisScope),
      initializing: !analysisReady && !recoveryError,
      running: Boolean(hasActiveAnalysis || waitingForRun || analysisPending),
      stopping: busy && !analysisPending && Boolean(hasActiveAnalysis),
      abortDisabled: !analysisReady || analysisPending || waitingForRun || !latestRun,
      paused: durableAnalysisStatus(latestRun) === 'paused' && Boolean(latestRun?.workflowResumable),
      resuming: busy && durableAnalysisStatus(latestRun) === 'paused',
      resumeLabel: workflowNotice?.resumeLabel,
      attachmentsVersion: sessionRuns.map(run => `${run.runId}:${run.artifactRef || ''}:${run.publishedAnalysisId || ''}`).join('|'),
    },
    composer: {
      value: analysisInput,
      setValue: setAnalysisInput,
      placeholder: analysisArtifact
        ? '继续追问这份分析，或说明你想调整的方向'
        : '就原文提问；完整分析请点击“快速分析”',
      ariaLabel: '来源分析',
      ready: analysisReady && (!busy || analysisPending),
      submitDisabled: !activeSession || !analysisInput.trim() || !analysisReady || !selectedAnalysisModel
        || (busy && !analysisPending),
      selectedModel: selectedAnalysisModel || null,
      modelConfigs,
      selectModel: setAnalysisModelId,
      updateModel: onUpdateModelConfig,
      openModelSettings: () => navigate('/settings'),
      taskPlan: currentPlan,
    },
    actions: {
      selectSession: id => setSelectedSession({revision: analysisRevisionId, id: String(id)}),
      createSession: async () => {
        const revision = analysisRevisionId
        const result = await analysisSessions.create(revision)
        if (!result.success || !result.data) { appMessage.error(result.error || '新建对话失败'); return }
        if (analysisRevisionRef.current !== revision) return
        setSelectedSession({revision, id: result.data.id})
        await loadSessions(revision)
      },
      closeSession: async id => {
        const result = await analysisSessions.update(analysisRevisionId, String(id), {closed: true})
        if (!result.success) { appMessage.error(result.error || '关闭对话失败'); return }
        if (id === activeSession) setSelectedSession({revision: analysisRevisionId, id: sessions.find(s => !s.closed && s.id !== id)?.id || ''})
        await loadSessions(analysisRevisionId)
      },
      renameSession: async (id, title) => {
        const result = await analysisSessions.update(analysisRevisionId, String(id), {title})
        if (!result.success) appMessage.error(result.error || '重命名失败')
        await loadSessions(analysisRevisionId)
      },
      loadSessionHistory: () => loadSessions(analysisRevisionId),
      openHistorySession: async id => {
        const result = await analysisSessions.update(analysisRevisionId, String(id), {closed: false})
        if (!result.success) { appMessage.error(result.error || '打开对话失败'); return }
        setSelectedSession({revision: analysisRevisionId, id: String(id)})
        await loadSessions(analysisRevisionId)
      },
      send: sendAnalysisMessage,
      retryQueued: () => { analysisComposer.retry(analysisScope); refreshComposer() },
      clearQueued: () => { analysisComposer.cancelQueue(analysisScope); refreshComposer() },
      updateQueuedSubmission: (id, patch) => {
        const changed = analysisComposer.updateQueued(analysisScope, id, patch)
        if (changed) refreshComposer()
        return changed
      },
      abort: async () => {
        if (!analysisReady || analysisRevisionRef.current !== analysisRevisionId || currentWorkRef.current !== workId) return
        analysisComposer.cancelQueue(analysisScope)
        refreshComposer()
        if (!latestRun) return
        if (latestRun.taskId && BLOCKING_ANALYSIS_STATUSES.has(durableAnalysisStatus(latestRun))) {
          await controlAnalysis(latestRun, 'cancel')
          return
        }
        const result = await services.ai.cancelAgentRun({ runId: latestRun.runId })
        if (!result.success) appMessage.error(result.error || '停止追问失败')
        await reloadAnalysis(analysisRevisionId)
      },
      resume: () => latestRun?.workflowResumable
        ? controlAnalysis(latestRun, 'resume')
        : undefined,
      editMessage: (index, content) => {
        const message = conversationMessages[index]
        if (message?.role !== 'user' || !message.clientTurnId) return
        const target = sessionRuns.find(run => run.commandId === message.clientTurnId)
        if (target) sendAnalysisMessage(content, target.runId)
      },
      resolveToolApproval: async () => ({ success: false, error: '来源分析不开放工具审批' }),
    },
  })
  const analysisExtensions: AgentConversationExtensions = {
    composerActionMenu: { triggers: ['/'], title: '来源分析操作', render: ({ close }) => <>
      <PurrButton onClick={() => { close(); setSourceReaderTarget(null); setSourceReaderOpen(true) }}>查看原文</PurrButton>
      <PurrButton disabled={!analysisArtifact} onClick={() => { close(); setAnalysisResultOpen(true) }}>查看分析结果</PurrButton>
      <PurrButton disabled={!activeSession || !analysisReady || !selectedAnalysisModel || busy || analysisPending || hasBlockingAnalysis || waitingForRun} onClick={() => { close(); void startAnalysis(analysisRevisionId) }}>{analysisRuns.some(run => run.interactionKind !== 'follow_up') || analysisArtifact ? '重新分析' : '快速分析'}</PurrButton>
    </> },
    renderAssistantAttachment: (message) => (
      renderAnalysisResult(message.agentRunId)
    ),
  }
  const analysisPanel = <section className="novel-analysis-workspace">
    <header className="novel-analysis-workspace-header">
      <span>{!analysisReady && !recoveryError && recoveryProgress > 0
        ? `正在恢复对话 · 已处理 ${recoveryProgress.toLocaleString()} 条历史记录`
        : '理解来源，提炼可用的写作技法'}</span>
    </header>
    {recoveryError ? <div role="alert" className="novel-analysis-recovery-error">
      <span>对话恢复失败：{recoveryError}</span>
      <PurrButton size="small" onClick={() => setRecoveryAttempt(value => value + 1)}>重新恢复</PurrButton>
    </div> : null}
    {workflowNotice && latestRun ? <div className="novel-analysis-workflow-notice" role="status">
      <p>{workflowNotice.text}</p>
      <div>
        {latestRun.workflowResumable ? <PurrButton size="small" disabled={busy}
          onClick={() => void controlAnalysis(latestRun, 'resume')}>{workflowNotice.resumeLabel}</PurrButton> : null}
        <PurrButton size="small" type="text" disabled={busy}
          onClick={() => void controlAnalysis(latestRun, 'cancel')}>结束本次分析</PurrButton>
      </div>
    </div> : null}
    <AgentConversationPanel
      className="novel-analysis-agent-panel"
      controller={analysisController}
      extensions={analysisExtensions}
    />
  </section>

  return <div className="novel-sources-page">
    <AppHeader
      title={<span className="novel-source-header-title">
        <span className="app-title">{detailWork?.title || '来源详情'}</span>
        <span className="novel-source-header-capability">来源分析</span>
      </span>}
      navigation={{
        home: { label: '返回首页', onClick: onHome },
        back: { label: '返回来源库', onClick: () => navigate('/novel-sources') },
      }}
      showActions
      onOpenSettings={onOpenSettings}
    />
    <main className="novel-source-detail-main">
      {!detailWork ? <div className="novel-sources-loading"><PurrSpin /></div> : <section className="novel-source-analysis-layout">
        <header className="novel-detail-heading">
          <div><span>{detailWork.source_type === 'frozen_book' ? '原创冻结' : '本地导入'}</span><h1>{detailWork.title}</h1><p>{selectedRevision?.character_count.toLocaleString() ?? '—'} 字符 · 版本 {selectedRevision?.version_no ?? '—'} · 只读</p></div>
          <div className="novel-detail-actions">
            <PurrButton type="primary" disabled={!activeSession || !analysisReady || !selectedAnalysisModel || busy || analysisPending || hasBlockingAnalysis || waitingForRun} onClick={() => void startAnalysis(analysisRevisionId)}>{analysisRuns.some(run => run.interactionKind !== 'follow_up') || analysisArtifact ? '重新分析' : '快速分析'}</PurrButton>
            <PurrButton danger icon={<DeleteIcon />} onClick={() => void deleteWork(detailWork)}>删除来源</PurrButton>
          </div>
        </header>
        {analysisPanel}
        <aside className="novel-source-context-card">
          <div>
            <span>ORIGINAL SOURCE</span>
            <h2>来源原文</h2>
            <p>分析所依据的只读版本，可按章节、关键词和位置查阅。</p>
          </div>
          <dl>
            <div><dt>当前版本</dt><dd>版本 {selectedRevision?.version_no ?? '—'}</dd></div>
            <div><dt>原文长度</dt><dd>{selectedRevision?.character_count.toLocaleString() ?? '—'} 字符</dd></div>
            <div><dt>分析状态</dt><dd>{analysisStatusText(analysisRuns, publishedAnalyses.length > 0)}</dd></div>
          </dl>
          <PurrButton icon={<BookIcon />} onClick={() => {
            setSourceReaderTarget(null)
            setSourceReaderOpen(true)
          }}>查看原文</PurrButton>
        </aside>
      </section>}
    </main>
    {resultModals}
    {busy ? <div className="novel-source-busy"><PurrSpin /></div> : null}
  </div>
}

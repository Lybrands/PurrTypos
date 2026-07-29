import { services } from '@/services'
import React from 'react'
import AppHeader from '../components/AppHeader'
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  BookOutlined,
  Button,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  ExportOutlined,
  FileTextOutlined,
  HistoryOutlined,
  Input,
  InputNumber,
  LoadingOutlined,
  Modal,
  MultiSelect,
  PlusOutlined,
  RobotOutlined,
  SaveOutlined,
  TeamOutlined,
  UndoOutlined,
  VideoCameraOutlined,
} from '../ui'
import { useAppFeedback } from '../hooks/useAppFeedback'
import type {
  AiModelConfig,
  Book,
  Chapter,
  Conversation,
  EntityId,
  ScreenplayDocument,
  ScreenplayDocumentProposal,
  ScreenplayFormat,
  ScreenplayProject,
  ScreenplaySourceRef,
  ScreenplaySourceKind,
  ScreenplaySourceScopeMode,
  ScreenplaySourceScopeRequest,
} from '../types'
import { buildStreamOptions } from '../Workspace/AiPanel/hooks/streamOptions'
import { createAiStreamId } from '../utils/aiStream'
import { buildScreenplayLineDiff } from './versionDiff'
import {
  buildSourceStructure,
  describePersistedSourceScope,
  resolveSourceScopeSelection,
} from './sourceScope'
import './index.scss'

type EntryStage = 'source' | 'brief' | 'handoff' | 'project'
type SourceKind = ScreenplaySourceKind

interface ScreenplayLaunchDraft {
  sourceKind: SourceKind
  sourceBookId: EntityId | null
  sourceBookTitle: string | null
  projectTitle: string
  format: ScreenplayFormat
  approach: string
  premise: string
  sourceScope?: ScreenplaySourceScopeRequest
  sourceScopeSummary: string
}

interface ScreenplayAgentPageProps {
  books: Book[]
  lastOpenedBookId: EntityId | null
  modelConfigs: AiModelConfig[]
  onOpenBookshelf: () => void
  onOpenSettings: () => void
  onBack: () => void
}

const FORMAT_OPTIONS: ScreenplayFormat[] = ['短片', '电影', '单集剧', '连续剧', '竖屏短剧']
const ADAPTATION_OPTIONS = ['忠实改编', '结构重组', '自由改编']
const ORIGINAL_OPTIONS = ['先找人物', '先建世界', '先推情节']
const SOURCE_SCOPE_OPTIONS: Array<{
  value: ScreenplaySourceScopeMode
  label: string
  requiresVolumes?: boolean
}> = [
  { value: 'whole_book', label: '整本作品' },
  { value: 'first_chapters', label: '前几章' },
  { value: 'first_volumes', label: '前几卷', requiresVolumes: true },
  { value: 'selected_chapters', label: '指定章节' },
  { value: 'selected_volumes', label: '指定卷', requiresVolumes: true },
]
const STAGE_LABELS: Record<ScreenplayProject['active_stage'], string> = {
  orientation: '素材梳理',
  brief: '创作简报',
  structure: '结构设计',
  scenes: '场景规划',
  draft: '剧本正文',
  review: '审阅修订',
  completed: '创作完成',
}
const DOCUMENT_KIND_LABELS: Record<ScreenplayDocument['kind'], string> = {
  source_analysis: '原作范围分析',
  creative_brief: '创作简报',
  beat_sheet: '节拍表',
  episode_outline: '分集结构',
  scene_list: '场景表',
  scene_draft: '场景正文',
  review: '审阅报告',
}
const SERIES_FORMATS = new Set<ScreenplayFormat>(['连续剧', '竖屏短剧'])

function previousDocumentVersion(
  document: ScreenplayDocument,
  documents: ScreenplayDocument[],
): ScreenplayDocument | null {
  return documents
    .filter((candidate) => (
      candidate.kind === document.kind
      && candidate.version < document.version
    ))
    .sort((left, right) => right.version - left.version)[0] ?? null
}

function stageAgentPrompt(
  project: ScreenplayProject,
  documents: ScreenplayDocument[] = [],
): string {
  if (project.active_stage === 'completed') {
    return project.delivery_manifest
      ? '这个剧本已经通过最终复审，权威版本链与内容摘要已固化为交付清单。你可以查看历史版本，或导出完整稿和交付清单。'
      : '这个剧本已经通过审阅并完成。你可以直接查看版本或导出当前完整稿。'
  }
  if (project.active_stage === 'orientation' && project.source_kind === 'book') {
    if (!project.source_book_id) {
      return '来源作品已经移除，当前无法完成原作范围分析。请保留已有文档，或从书架中的有效作品新建改编项目。'
    }
    return '请先调用 getSourceCoveragePlan 获取原作范围和阅读批次；将全部 readSourceCoverageBatch 放在一个并列只读步骤中，再按需用 readSourcePassages 精读决定创作结论的关键章节。梳理人物、事件、冲突、可改编资产、连续性风险和待确认缺口，为每项事实保留 sourceType 与 sourceId，并区分全文精读、抽样和未覆盖章节；最后调用 proposeSourceAnalysis 提交可审阅的原作范围分析。'
  }
  if (project.active_stage === 'brief' && project.source_kind === 'book') {
    return '请把已接受的原作范围分析转成可执行的改编方案：确定目标时长或集数、叙事终点，并逐条说明保留、压缩、合并、删减、重排、转化或新增的内容。除新增内容外，每条决策都要用 sourceType 与 sourceId 锚定原作分析证据；同时完整承接分析中的阅读局限，最后调用 proposeCreativeBrief 提交可审阅提案。'
  }
  if (project.active_stage === 'structure') {
    return SERIES_FORMATS.has(project.format)
      ? '请基于已接受的创作简报设计分集结构；每集使用稳定 id 和连续 number，集数必须符合简报，并用 decisionCoverage 完整说明每条改编决策落在哪些集。删减决策使用空映射并说明执行方式，最后调用 proposeEpisodeOutline。'
      : '请基于已接受的创作简报设计故事节拍；每个节拍使用稳定 id 和连续 order，并用 decisionCoverage 完整说明每条改编决策落在哪些节拍。删减决策使用空映射并说明执行方式，最后调用 proposeBeatSheet。'
  }
  if (project.active_stage === 'scenes') {
    return SERIES_FORMATS.has(project.format)
      ? '请基于已接受的分集结构拆解完整场景表。每场提供稳定 id、连续 order、人物目标、冲突和转折；通过 structureUnitIds 严格归属一个分集，并让 episodeNumber 与该分集一致。确保每集至少有一个场景承接，最后调用 proposeSceneList。'
      : '请基于已接受的节拍表拆解完整场景表。每场提供稳定 id、连续 order、人物目标、冲突和转折，并通过 structureUnitIds 标明所承接的节拍；确保每个节拍至少有一个场景承接，最后调用 proposeSceneList。'
  }
  if (project.active_stage === 'draft') {
    const sceneList = [...documents].reverse().find(
      (document) => document.kind === 'scene_list'
        && document.status === 'accepted',
    )
    const latestDraft = [...documents].reverse().find(
      (document) => document.kind === 'scene_draft'
        && document.status === 'accepted',
    )
    const scenes = Array.isArray(sceneList?.content_json?.scenes)
      ? sceneList.content_json.scenes
      : []
    const completed = Array.isArray(latestDraft?.content_json?.completedSceneIds)
      ? new Set(latestDraft.content_json.completedSceneIds.map(String))
      : new Set<string>()
    const nextScene = scenes.find((scene) => {
      if (!scene || typeof scene !== 'object') return false
      return !completed.has(String((scene as { id?: unknown }).id || ''))
    }) as { id?: unknown; heading?: unknown } | undefined
    if (nextScene) {
      return `请创作场景 ${String(nextScene.id || '')}「${String(nextScene.heading || '')}」。继承已接受整稿，contentText 必须包含截至本场的完整正文；execution 要具体说明本场如何完成目标、推进冲突、兑现转折和形成场尾连续性状态，并如实列出未解决事项。每次只追加当前这一场，不能改写既有 sceneExecutions；角色提示使用 @人物名，最后调用 proposeSceneDraft。`
    }
    return '场景表中的场景已经全部完成。请检查滚动整稿完整性，并调用 proposeSceneDraft 提交 isComplete=true 的最终正文提案。'
  }
  if (project.active_stage === 'review') {
    const acceptedDraft = [...documents].reverse().find(
      (document) => document.kind === 'scene_draft'
        && document.status === 'accepted',
    )
    const acceptedReview = [...documents].reverse().find(
      (document) => document.kind === 'review'
        && document.status === 'accepted',
    )
    if (
      acceptedReview
      && String(acceptedReview.content_json?.reviewedDraftId || '')
        === String(acceptedDraft?.id || '')
    ) {
      const issues = Array.isArray(acceptedReview.content_json?.issues)
        ? acceptedReview.content_json.issues
        : []
      return `请根据已接受的审阅报告修订完整剧本，逐项回应其中 ${issues.length} 个结构化问题；为每项问题提交解决状态和正文证据，并通过 executionUpdates 重新评估所有受影响场景，最后调用 proposeScreenplayRevision 提交完整修订稿。`
    }
    const previousReviewId = String(
      acceptedDraft?.content_json?.reviewId || '',
    )
    const previousReview = documents.find(
      (document) => document.kind === 'review'
        && document.id === previousReviewId,
    )
    if (previousReviewId && previousReview) {
      const previousIssues = Array.isArray(previousReview.content_json?.issues)
        ? previousReview.content_json.issues
        : []
      return `请复审当前修订稿，逐项核验上一轮 ${previousIssues.length} 个问题的 acceptanceCriteria，并用 verificationResults 标记 verified、still_open 或 regressed，附上正文核验证据。未通过项必须继续保留在 issues 中；全部通过且没有新问题时才能判定 ready，最后调用 proposeScreenplayReview。`
    }
    return '请从连贯性、人物弧光、结构节奏、对白和剧本格式审阅当前完整稿。每个问题必须绑定具体场景、对应的 sceneExecutions 字段，并给出明确验收标准，最后调用 proposeScreenplayReview 提交结构化审阅报告。'
  }
  if (project.premise) {
    return '请基于当前项目资料完善创作简报，指出仍需我决定的关键取舍，并调用 proposeCreativeBrief 提交一份可审阅提案。'
  }
  return '请先提出不超过三个最关键的澄清问题；信息足够后，调用 proposeCreativeBrief 提交创作简报提案。'
}

function nextMilestone(project: ScreenplayProject): {
  title: string
  description: string
} {
  if (project.active_stage === 'completed') {
    return {
      title: '剧本创作已完成',
      description: project.delivery_manifest
        ? '最终剧本、复审结果和全部上游版本已固化，可以导出完整稿或 JSON 交付清单。'
        : '当前完整稿已通过结构化审阅，可以导出或查看历史版本。',
    }
  }
  if (project.active_stage === 'orientation' && project.source_kind === 'book') {
    return {
      title: '先建立原作范围分析',
      description: project.source_book_id
        ? 'Agent 会自动分批覆盖锁定章节、按需精读关键段落，再形成带来源证据的人物、事件、冲突与改编资产分析。'
        : '来源作品已经移除，当前无法继续读取原作素材。',
    }
  }
  if (project.active_stage === 'brief') {
    return {
      title: '让 Agent 完善创作简报',
      description: project.source_kind === 'book'
        ? '创作简报会继承已接受的原作范围分析，把事实底座转化为明确的改编取舍。'
        : '原创项目会围绕现有简报与你澄清创作方向。',
    }
  }
  if (project.active_stage === 'structure') {
    return {
      title: SERIES_FORMATS.has(project.format)
        ? '让 Agent 设计分集结构'
        : '让 Agent 设计故事节拍',
      description: '结构提案必须从已接受的创作简报出发，接受后项目才会进入场景规划。',
    }
  }
  if (project.active_stage === 'scenes') {
    return {
      title: '让 Agent 拆解场景表',
      description: '场景表会给每场稳定编号、目标、冲突与转折；接受后进入逐场正文。',
    }
  }
  if (project.active_stage === 'draft') {
    return {
      title: '按场完成滚动整稿',
      description: '每次接受都会保存一份包含此前全部场景的新正文版本；完成所有场景后进入审阅。',
    }
  }
  if (project.active_stage === 'review') {
    return {
      title: '审阅并导出完整剧本',
      description: '当前已接受正文可以导出为 Fountain、Markdown 或纯文本。',
    }
  }
  return {
    title: '让 Agent 完善创作简报',
    description: project.source_kind === 'book'
      ? '它可以只读检索原作，并为命中的人物、设定、大纲和正文保留来源。'
      : '原创项目会围绕现有简报与你澄清创作方向。',
  }
}

export default function ScreenplayAgentPage({
  books,
  lastOpenedBookId,
  modelConfigs,
  onOpenBookshelf,
  onOpenSettings,
  onBack,
}: ScreenplayAgentPageProps) {
  const { message } = useAppFeedback()
  const [stage, setStage] = React.useState<EntryStage>('source')
  const [sourceKind, setSourceKind] = React.useState<SourceKind>('book')
  const [selectedBookId, setSelectedBookId] = React.useState<EntityId | null>(null)
  const [projectTitle, setProjectTitle] = React.useState('')
  const [format, setFormat] = React.useState(FORMAT_OPTIONS[2])
  const [approach, setApproach] = React.useState(ADAPTATION_OPTIONS[1])
  const [premise, setPremise] = React.useState('')
  const [sourceScopeMode, setSourceScopeMode] = React.useState<ScreenplaySourceScopeMode>('whole_book')
  const [sourceScopeCount, setSourceScopeCount] = React.useState(10)
  const [selectedSourceChapterIds, setSelectedSourceChapterIds] = React.useState<EntityId[]>([])
  const [selectedSourceVolumeIds, setSelectedSourceVolumeIds] = React.useState<EntityId[]>([])
  const [sourceChapters, setSourceChapters] = React.useState<Chapter[]>([])
  const [sourceChaptersLoading, setSourceChaptersLoading] = React.useState(false)
  const [sourceChaptersError, setSourceChaptersError] = React.useState('')
  const [launchDraft, setLaunchDraft] = React.useState<ScreenplayLaunchDraft | null>(null)
  const [projects, setProjects] = React.useState<ScreenplayProject[]>([])
  const [projectsLoading, setProjectsLoading] = React.useState(true)
  const [creatingProject, setCreatingProject] = React.useState(false)
  const [openedProject, setOpenedProject] = React.useState<ScreenplayProject | null>(null)
  const [projectDocuments, setProjectDocuments] = React.useState<ScreenplayDocument[]>([])
  const [projectSourceRefs, setProjectSourceRefs] = React.useState<ScreenplaySourceRef[]>([])
  const [projectLoading, setProjectLoading] = React.useState(false)
  const [selectedModelId, setSelectedModelId] = React.useState('')
  const [agentPrompt, setAgentPrompt] = React.useState('')
  const [agentResponse, setAgentResponse] = React.useState('')
  const [agentThinking, setAgentThinking] = React.useState('')
  const [agentRunId, setAgentRunId] = React.useState('')
  const [agentActivity, setAgentActivity] = React.useState('')
  const [agentProposal, setAgentProposal] = React.useState<ScreenplayDocumentProposal | null>(null)
  const [savedAgentDocumentId, setSavedAgentDocumentId] = React.useState<EntityId | null>(null)
  const [acceptedAgentDocumentId, setAcceptedAgentDocumentId] = React.useState<EntityId | null>(null)
  const [agentRunning, setAgentRunning] = React.useState(false)
  const [agentSessionId, setAgentSessionId] = React.useState<number | null>(null)
  const [agentHistory, setAgentHistory] = React.useState<Conversation[]>([])
  const [savingAgentDraft, setSavingAgentDraft] = React.useState(false)
  const [acceptingAgentDraft, setAcceptingAgentDraft] = React.useState(false)
  const [exportFormat, setExportFormat] = React.useState<
    'fountain' | 'markdown' | 'txt' | 'pdf' | 'json'
  >('fountain')
  const [exportingScreenplay, setExportingScreenplay] = React.useState(false)
  const [selectedDocument, setSelectedDocument] = React.useState<ScreenplayDocument | null>(null)
  const [comparisonDocument, setComparisonDocument] = React.useState<ScreenplayDocument | null>(null)
  const [documentTitleDraft, setDocumentTitleDraft] = React.useState('')
  const [documentTextDraft, setDocumentTextDraft] = React.useState('')
  const [savingDocument, setSavingDocument] = React.useState(false)
  const [restoringDocumentId, setRestoringDocumentId] = React.useState<EntityId | null>(null)
  const [updatingProjectStatus, setUpdatingProjectStatus] = React.useState(false)
  const agentStreamIdRef = React.useRef<string | null>(null)
  const agentUnsubscribeRef = React.useRef<(() => void) | null>(null)

  React.useEffect(() => {
    if (
      selectedModelId
      && modelConfigs.some((config) => config.id === selectedModelId)
    ) {
      return
    }
    setSelectedModelId(modelConfigs[0]?.id ?? '')
  }, [modelConfigs, selectedModelId])

  React.useEffect(() => () => {
    if (agentStreamIdRef.current) {
      services.ai.abortAiStream(agentStreamIdRef.current)
    }
    agentUnsubscribeRef.current?.()
  }, [])

  const loadProjects = React.useCallback(async () => {
    setProjectsLoading(true)
    try {
      const result = await services.screenplay.listScreenplayProjects({
        includeArchived: true,
      })
      if (result.success && Array.isArray(result.data)) {
        setProjects(result.data)
      }
    } finally {
      setProjectsLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void loadProjects()
  }, [loadProjects])

  const sortedBooks = React.useMemo(() => {
    if (lastOpenedBookId == null) return books
    return [...books].sort((left, right) => {
      if (left.id === lastOpenedBookId) return -1
      if (right.id === lastOpenedBookId) return 1
      return 0
    })
  }, [books, lastOpenedBookId])

  const selectedBook = React.useMemo(
    () => books.find((book) => book.id === selectedBookId) ?? null,
    [books, selectedBookId],
  )

  const sourceStructure = React.useMemo(
    () => buildSourceStructure(sourceChapters),
    [sourceChapters],
  )

  const sourceScopeSelection = React.useMemo(
    () => resolveSourceScopeSelection({
      structure: sourceStructure,
      mode: sourceScopeMode,
      count: sourceScopeCount,
      chapterIds: selectedSourceChapterIds,
      volumeIds: selectedSourceVolumeIds,
    }),
    [
      selectedSourceChapterIds,
      selectedSourceVolumeIds,
      sourceScopeCount,
      sourceScopeMode,
      sourceStructure,
    ],
  )

  React.useEffect(() => {
    if (sourceKind !== 'book' || !selectedBookId) {
      setSourceChapters([])
      setSourceChaptersError('')
      setSourceChaptersLoading(false)
      return
    }
    let canceled = false
    setSourceChaptersLoading(true)
    setSourceChaptersError('')
    void (async () => {
      try {
        const outlineResult = await services.outlines.getWritingOutline(
          selectedBookId,
        )
        if (canceled) return
        if (!outlineResult.success || !outlineResult.data?.id) {
          setSourceChapters([])
          setSourceChaptersError(
            outlineResult.error || '读取来源作品目录失败',
          )
          return
        }
        const chapterResult = await services.chapters.getChapters({
          outlineId: outlineResult.data.id,
        })
        if (canceled) return
        if (!chapterResult.success || !Array.isArray(chapterResult.data)) {
          setSourceChapters([])
          setSourceChaptersError(
            chapterResult.error || '读取来源作品章节失败',
          )
          return
        }
        setSourceChapters(chapterResult.data)
        const structure = buildSourceStructure(chapterResult.data)
        setSourceScopeCount(Math.min(10, Math.max(1, structure.chapters.length)))
      } catch {
        if (canceled) return
        setSourceChapters([])
        setSourceChaptersError('读取来源作品章节失败，请返回后重试')
      } finally {
        if (!canceled) setSourceChaptersLoading(false)
      }
    })()
    return () => {
      canceled = true
    }
  }, [selectedBookId, sourceKind])

  const chooseSourceScopeMode = React.useCallback((
    mode: ScreenplaySourceScopeMode,
  ) => {
    setSourceScopeMode(mode)
    if (mode === 'first_chapters') {
      setSourceScopeCount(
        Math.min(10, Math.max(1, sourceStructure.chapters.length)),
      )
    } else if (mode === 'first_volumes') {
      setSourceScopeCount(1)
    }
  }, [sourceStructure])

  const startFromBook = React.useCallback((book: Book) => {
    setSourceKind('book')
    setSelectedBookId(book.id)
    setProjectTitle(`《${book.title}》剧本改编`)
    setApproach(ADAPTATION_OPTIONS[1])
    setPremise('')
    setSourceScopeMode('whole_book')
    setSourceScopeCount(10)
    setSelectedSourceChapterIds([])
    setSelectedSourceVolumeIds([])
    setStage('brief')
  }, [])

  const startOriginal = React.useCallback(() => {
    setSourceKind('original')
    setSelectedBookId(null)
    setProjectTitle('未命名原创剧本')
    setApproach(ORIGINAL_OPTIONS[0])
    setPremise('')
    setSourceScopeMode('whole_book')
    setSelectedSourceChapterIds([])
    setSelectedSourceVolumeIds([])
    setStage('brief')
  }, [])

  const buildHandoff = React.useCallback(() => {
    if (sourceKind === 'book' && sourceChaptersLoading) {
      message.warning('正在读取来源作品章节，请稍候')
      return
    }
    if (sourceKind === 'book' && sourceChaptersError) {
      message.warning(sourceChaptersError)
      return
    }
    if (sourceKind === 'book' && sourceScopeSelection.error) {
      message.warning(sourceScopeSelection.error)
      return
    }
    const normalizedTitle = projectTitle.trim() || '未命名剧本'
    setProjectTitle(normalizedTitle)
    setLaunchDraft({
      sourceKind,
      sourceBookId: selectedBook?.id ?? null,
      sourceBookTitle: selectedBook?.title ?? null,
      projectTitle: normalizedTitle,
      format,
      approach,
      premise: premise.trim(),
      sourceScope: sourceKind === 'book'
        ? sourceScopeSelection.request ?? undefined
        : undefined,
      sourceScopeSummary: sourceKind === 'book'
        ? sourceScopeSelection.summary
        : '不适用',
    })
    setStage('handoff')
  }, [
    approach,
    format,
    message,
    premise,
    projectTitle,
    selectedBook,
    sourceChaptersError,
    sourceChaptersLoading,
    sourceKind,
    sourceScopeSelection,
  ])

  const resetToSource = React.useCallback(() => {
    setLaunchDraft(null)
    setOpenedProject(null)
    setProjectDocuments([])
    setProjectSourceRefs([])
    setAgentProposal(null)
    setSavedAgentDocumentId(null)
    setAcceptedAgentDocumentId(null)
    setAgentSessionId(null)
    setAgentHistory([])
    setSelectedDocument(null)
    setComparisonDocument(null)
    setSourceChapters([])
    setSourceChaptersError('')
    setStage('source')
  }, [])

  const loadProjectDocuments = React.useCallback(async (projectId: EntityId) => {
    const result = await services.screenplay.listScreenplayDocuments({
      projectId,
    })
    if (result.success && Array.isArray(result.data)) {
      setProjectDocuments(result.data)
      return result.data
    }
    message.error(result.error || '读取剧本文档失败')
    return null
  }, [message])

  const loadProjectSourceRefs = React.useCallback(async (projectId: EntityId) => {
    const result = await services.screenplay.listScreenplaySourceRefs({
      projectId,
    })
    if (result.success && Array.isArray(result.data)) {
      setProjectSourceRefs(result.data)
      return result.data
    }
    return null
  }, [])

  const openProject = React.useCallback(async (project: ScreenplayProject) => {
    if (agentStreamIdRef.current) {
      services.ai.abortAiStream(agentStreamIdRef.current)
    }
    agentUnsubscribeRef.current?.()
    agentUnsubscribeRef.current = null
    agentStreamIdRef.current = null
    setOpenedProject(project)
    setProjectDocuments([])
    setProjectSourceRefs([])
    setAgentSessionId(null)
    setAgentHistory([])
    setAgentPrompt(stageAgentPrompt(project))
    setAgentResponse('')
    setAgentThinking('')
    setAgentRunId('')
    setAgentActivity('')
    setAgentProposal(null)
    setSavedAgentDocumentId(null)
    setAcceptedAgentDocumentId(null)
    setAgentRunning(false)
    setProjectLoading(true)
    setStage('project')
    try {
      const [documents, , sessionResult] = await Promise.all([
        loadProjectDocuments(project.id),
        loadProjectSourceRefs(project.id),
        services.screenplay.getOrCreateScreenplaySession({
          projectId: project.id,
        }),
      ])
      if (sessionResult.success && sessionResult.data) {
        setAgentSessionId(sessionResult.data.id)
        const historyResult = await services.conversations.getConversations({
          sessionId: sessionResult.data.id,
        })
        if (historyResult.success && Array.isArray(historyResult.data)) {
          setAgentHistory(historyResult.data)
        }
      } else {
        message.error(sessionResult.error || '初始化剧本 Agent 会话失败')
      }
      if (documents) {
        setAgentPrompt(stageAgentPrompt(project, documents))
      }
    } finally {
      setProjectLoading(false)
    }
  }, [loadProjectDocuments, loadProjectSourceRefs, message])

  const stopAgent = React.useCallback(() => {
    if (agentStreamIdRef.current) {
      services.ai.abortAiStream(agentStreamIdRef.current)
    }
  }, [])

  const runAgent = React.useCallback(() => {
    if (!openedProject || agentRunning) return
    if (openedProject.status === 'archived') {
      message.warning('项目已归档，请先恢复项目')
      return
    }
    if (agentSessionId == null) {
      message.warning('剧本 Agent 会话尚未就绪，请重新打开项目')
      return
    }
    const prompt = agentPrompt.trim()
    if (!prompt) {
      message.warning('先告诉 Agent 这轮要解决什么')
      return
    }
    const model = modelConfigs.find((item) => item.id === selectedModelId)
    if (!model?.apiKey?.trim()) {
      message.warning('请先在设置中添加可用模型')
      onOpenSettings()
      return
    }

    const stageKinds: ScreenplayDocument['kind'][] = openedProject.active_stage === 'completed'
      ? ['review', 'scene_draft']
      : openedProject.active_stage === 'review'
      ? ['review', 'scene_draft']
      : openedProject.active_stage === 'draft'
      ? ['scene_draft', 'scene_list']
      : openedProject.active_stage === 'scenes'
        ? ['scene_list', 'episode_outline', 'beat_sheet']
        : openedProject.active_stage === 'structure'
          ? ['episode_outline', 'beat_sheet', 'creative_brief']
          : openedProject.active_stage === 'brief'
            ? ['creative_brief', 'source_analysis']
            : openedProject.source_kind === 'book'
              ? ['source_analysis', 'creative_brief']
              : ['creative_brief']
    const activeDocument = [...projectDocuments].reverse().find(
      (document) => document.status === 'accepted'
        && stageKinds.includes(document.kind),
    ) ?? [...projectDocuments].reverse().find(
      (document) => stageKinds.includes(document.kind),
    )
    const { options } = buildStreamOptions({
      cfg: model,
      modelConfigs: { [model.id]: { max_tokens: 8192 } },
      selectedModel: model.id,
    })
    const streamId = createAiStreamId(`screenplay-${openedProject.id}`)
    setAgentResponse('')
    setAgentThinking('')
    setAgentRunId('')
    setAgentActivity('正在建立剧本任务…')
    setAgentProposal(null)
    setSavedAgentDocumentId(null)
    setAcceptedAgentDocumentId(null)
    setAgentRunning(true)
    let currentAgentRunId = ''
    let accumulatedResponse = ''
    let accumulatedThinking = ''
    let terminalHandled = false
    let resolvedModel = model.name
    const startedAt = Date.now()
    agentStreamIdRef.current = streamId
    agentUnsubscribeRef.current?.()
    agentUnsubscribeRef.current = services.ai.onAiChunk((chunk) => {
      if (chunk.agentRunStarted?.runId) {
        currentAgentRunId = chunk.agentRunStarted.runId
        setAgentRunId(currentAgentRunId)
      }
      if (chunk.toolCallsInProgress) {
        const names = (chunk.toolCalls || [])
          .map((call) => call.function.name)
          .filter(Boolean)
        setAgentActivity(
          names.some((name) => name.startsWith('propose'))
            ? '正在整理结构化提案…'
            : names.some((name) => name.startsWith('getSource') || name === 'searchSourceMaterial' || name === 'readSourcePassages')
            ? '正在查阅原作素材…'
            : '正在读取剧本项目…',
        )
      }
      if (chunk.proposedScreenplayDocument) {
        setAgentProposal(chunk.proposedScreenplayDocument)
        setAgentActivity('提案已生成，等待你的审阅…')
      }
      if (chunk.delta) {
        setAgentActivity('正在形成可审阅提案…')
        accumulatedResponse += chunk.delta
        setAgentResponse((current) => current + chunk.delta)
      }
      if (chunk.thinkingDelta) {
        accumulatedThinking += chunk.thinkingDelta
        setAgentThinking((current) => current + chunk.thinkingDelta)
      }
      if (chunk.model) resolvedModel = chunk.model
      if (chunk.error) {
        message.error(chunk.error)
      }
      if ((chunk.done || chunk.error) && !terminalHandled) {
        terminalHandled = true
        const finish = async () => {
          if (chunk.done && !chunk.aborted) {
            const saved = await services.conversations.saveConversation({
              sessionId: agentSessionId,
              bookId: openedProject.source_book_id,
              prompt,
              response: accumulatedResponse,
              thinking: accumulatedThinking || undefined,
              model: resolvedModel,
              durationMs: Date.now() - startedAt,
              agentRunId: currentAgentRunId || undefined,
            })
            if (saved.success) {
              setAgentHistory((current) => [
                ...current,
                {
                  id: saved.data?.id ?? Date.now(),
                  session_id: agentSessionId,
                  chapter_id: '',
                  prompt,
                  response: accumulatedResponse,
                  thinking: accumulatedThinking || undefined,
                  model: resolvedModel,
                },
              ])
            } else {
              message.error(saved.error || '保存剧本 Agent 对话失败')
            }
          }
          setAgentRunning(false)
          setAgentActivity('')
          if (currentAgentRunId) {
            void loadProjectSourceRefs(openedProject.id)
          }
          agentStreamIdRef.current = null
          agentUnsubscribeRef.current?.()
          agentUnsubscribeRef.current = null
        }
        void finish()
      }
    }, streamId)

    services.ai.aiChatStream({
      streamId,
      apiKey: model.apiKey,
      baseURL: model.baseUrl || undefined,
      apiProvider: model.apiProvider === 'anthropic' ? 'anthropic' : 'openai',
      sessionId: agentSessionId,
      messages: [
        ...agentHistory.flatMap((conversation) => [
          { role: 'user', content: conversation.prompt },
          { role: 'assistant', content: conversation.response },
        ]),
        { role: 'user', content: prompt },
      ],
      options,
      enableAgentTools: true,
      chatAgentMode: 'agent',
      contextWindow: options.context_window,
      agentProfile: 'screenplay',
      screenplayProjectId: openedProject.id,
      sourceBookId: openedProject.source_book_id,
      activeDocumentId: activeDocument?.id ?? null,
      activeStage: openedProject.active_stage,
    })
  }, [
    agentHistory,
    agentPrompt,
    agentRunning,
    agentSessionId,
    message,
    loadProjectSourceRefs,
    modelConfigs,
    onOpenSettings,
    openedProject,
    projectDocuments,
    selectedModelId,
  ])

  const saveAgentProposal = React.useCallback(async (): Promise<EntityId | null> => {
    if (!openedProject || !agentProposal) return null
    if (savedAgentDocumentId) return savedAgentDocumentId
    setSavingAgentDraft(true)
    try {
      const result = await services.screenplay.createScreenplayDocument({
        projectId: openedProject.id,
        kind: agentProposal.kind,
        title: agentProposal.title,
        contentJson: agentProposal.contentJson,
        contentText: agentProposal.contentText,
        derivedFromIds: agentProposal.derivedFromIds,
        sourceRunId: agentRunId || undefined,
      })
      if (!result.success || !result.data) {
        message.error(result.error || '保存剧本文档提案失败')
        return null
      }
      setSavedAgentDocumentId(result.data.id)
      await Promise.all([
        loadProjectDocuments(openedProject.id),
        loadProjectSourceRefs(openedProject.id),
      ])
      return result.data.id
    } finally {
      setSavingAgentDraft(false)
    }
  }, [
    agentProposal,
    agentRunId,
    loadProjectDocuments,
    loadProjectSourceRefs,
    message,
    openedProject,
    savedAgentDocumentId,
  ])

  const acceptAgentProposal = React.useCallback(async () => {
    if (!openedProject || !agentProposal || acceptingAgentDraft) return
    setAcceptingAgentDraft(true)
    try {
      const documentId = await saveAgentProposal()
      if (!documentId) return
      const accepted = await services.screenplay.acceptScreenplayDocument({
        documentId,
      })
      if (!accepted.success) {
        message.error(accepted.error || '接受剧本文档提案失败')
        return
      }
      const refreshed = await services.screenplay.getScreenplayProject({
        projectId: openedProject.id,
      })
      const [documents] = await Promise.all([
        loadProjectDocuments(openedProject.id),
        loadProjectSourceRefs(openedProject.id),
      ])
      if (refreshed.success && refreshed.data) {
        setOpenedProject(refreshed.data)
        setProjects((current) => current.map((project) => (
          project.id === refreshed.data?.id ? refreshed.data : project
        )))
        setAgentPrompt(stageAgentPrompt(refreshed.data, documents || []))
      }
      setAcceptedAgentDocumentId(documentId)
      message.success(
        refreshed.success
        && refreshed.data
        && refreshed.data.active_stage !== openedProject.active_stage
          ? '已接受提案并推进项目阶段'
          : '已接受为当前剧本文档版本',
      )
    } finally {
      setAcceptingAgentDraft(false)
    }
  }, [
    acceptingAgentDraft,
    agentProposal,
    loadProjectDocuments,
    loadProjectSourceRefs,
    message,
    openedProject,
    saveAgentProposal,
  ])

  const acceptedScreenplayDraft = React.useMemo(
    () => [...projectDocuments].reverse().find(
      (document) => document.kind === 'scene_draft'
        && document.status === 'accepted',
    ) ?? null,
    [projectDocuments],
  )

  const exportScreenplay = React.useCallback(async () => {
    if (!openedProject || !acceptedScreenplayDraft || exportingScreenplay) return
    setExportingScreenplay(true)
    try {
      if (exportFormat === 'json') {
        if (!openedProject.delivery_manifest) {
          message.error('项目尚未生成最终交付清单')
          return
        }
        const result = await services.files.writeScreenplayFile({
          defaultName: `${openedProject.title || '剧本'}-交付清单`,
          content: `${JSON.stringify(
            openedProject.delivery_manifest,
            null,
            2,
          )}\n`,
          format: 'json',
        })
        if (result.success) {
          message.success('最终交付清单导出成功')
        } else if (result.error !== 'canceled') {
          message.error(result.error || '交付清单导出失败')
        }
        return
      }
      if (exportFormat === 'pdf') {
        const result = await services.exports.exportScreenplayPdf({
          projectId: openedProject.id,
          defaultName: openedProject.title || '剧本',
        })
        if (result.success) {
          message.success('标准剧本 PDF 导出成功')
        } else if (result.error !== 'canceled') {
          message.error(result.error || 'PDF 导出失败')
        }
        return
      }
      const raw = acceptedScreenplayDraft.content_text.trim()
      const content = exportFormat === 'markdown'
        ? `# ${openedProject.title}\n\n${raw}\n`
        : raw
      const result = await services.files.writeScreenplayFile({
        defaultName: openedProject.title || '剧本',
        content,
        format: exportFormat,
      })
      if (result.success) {
        message.success('剧本导出成功')
      } else if (result.error !== 'canceled') {
        message.error(result.error || '剧本导出失败')
      }
    } finally {
      setExportingScreenplay(false)
    }
  }, [
    acceptedScreenplayDraft,
    exportFormat,
    exportingScreenplay,
    message,
    openedProject,
  ])

  const saveAgentDraft = React.useCallback(async () => {
    if (
      !openedProject
      || !agentResponse.trim()
      || savingAgentDraft
      || savedAgentDocumentId
    ) return
    const parentKinds: ScreenplayDocument['kind'][] = openedProject.active_stage === 'review'
      ? ['review', 'scene_draft']
      : openedProject.active_stage === 'draft'
      ? ['scene_draft', 'scene_list']
      : openedProject.active_stage === 'scenes'
        ? ['scene_list', 'episode_outline', 'beat_sheet']
        : openedProject.active_stage === 'brief'
          && openedProject.source_kind === 'book'
          ? ['source_analysis']
          : ['creative_brief']
    const parents = projectDocuments.filter(
      (document) => document.status === 'accepted'
        && parentKinds.includes(document.kind),
    )
    setSavingAgentDraft(true)
    try {
      const fallbackKind: ScreenplayDocument['kind'] = openedProject.active_stage === 'review'
        ? 'review'
        : openedProject.active_stage === 'draft'
        ? 'scene_draft'
        : openedProject.active_stage === 'scenes'
          ? 'scene_list'
          : openedProject.active_stage === 'structure'
            ? SERIES_FORMATS.has(openedProject.format)
              ? 'episode_outline'
              : 'beat_sheet'
            : openedProject.active_stage === 'orientation'
              && openedProject.source_kind === 'book'
              ? 'source_analysis'
              : 'creative_brief'
      const result = await services.screenplay.createScreenplayDocument({
        projectId: openedProject.id,
        kind: fallbackKind,
        title: `Agent ${DOCUMENT_KIND_LABELS[fallbackKind]}候选`,
        contentJson: {
          schemaVersion: 1,
          generatedBy: 'screenplay-agent',
          stage: openedProject.active_stage,
        },
        contentText: agentResponse.trim(),
        derivedFromIds: parents.map((parent) => parent.id),
        sourceRunId: agentRunId || undefined,
      })
      if (!result.success) {
        message.error(result.error || '保存简报提案失败')
        return
      }
      setSavedAgentDocumentId(result.data?.id ?? null)
      await Promise.all([
        loadProjectDocuments(openedProject.id),
        loadProjectSourceRefs(openedProject.id),
      ])
      message.success(`已保存为新的${DOCUMENT_KIND_LABELS[fallbackKind]}草稿`)
    } finally {
      setSavingAgentDraft(false)
    }
  }, [
    agentResponse,
    agentRunId,
    loadProjectDocuments,
    loadProjectSourceRefs,
    message,
    openedProject,
    projectDocuments,
    savedAgentDocumentId,
    savingAgentDraft,
  ])

  const createProject = React.useCallback(async () => {
    if (!launchDraft || creatingProject) return
    setCreatingProject(true)
    try {
      const result = await services.screenplay.createScreenplayProject({
        title: launchDraft.projectTitle,
        sourceKind: launchDraft.sourceKind,
        sourceBookId: launchDraft.sourceBookId,
        format: launchDraft.format,
        approach: launchDraft.approach,
        premise: launchDraft.premise,
        sourceScope: launchDraft.sourceScope,
      })
      if (!result.success || !result.data) {
        message.error(result.error || '创建剧本项目失败')
        return
      }
      const { project } = result.data
      setProjects((current) => [
        project,
        ...current.filter((item) => item.id !== project.id),
      ])
      await openProject(project)
      message.success('剧本项目已创建')
    } finally {
      setCreatingProject(false)
    }
  }, [creatingProject, launchDraft, message, openProject])

  const openDocument = React.useCallback((
    document: ScreenplayDocument,
    compareWithPrevious = false,
  ) => {
    setSelectedDocument(document)
    setDocumentTitleDraft(document.title)
    setDocumentTextDraft(document.content_text)
    setComparisonDocument(
      compareWithPrevious
        ? previousDocumentVersion(document, projectDocuments)
        : null,
    )
  }, [projectDocuments])

  const saveSelectedDocument = React.useCallback(async () => {
    if (
      !openedProject
      || !selectedDocument
      || selectedDocument.status !== 'draft'
      || openedProject.status === 'archived'
      || savingDocument
    ) return
    const title = documentTitleDraft.trim()
    if (!title) {
      message.warning('文档标题不能为空')
      return
    }
    setSavingDocument(true)
    try {
      const result = await services.screenplay.updateScreenplayDocument({
        documentId: selectedDocument.id,
        patch: {
          title,
          contentText: documentTextDraft,
        },
      })
      if (!result.success || !result.data) {
        message.error(result.error || '保存文档失败')
        return
      }
      setSelectedDocument(result.data)
      setProjectDocuments((current) => current.map((document) => (
        document.id === result.data?.id ? result.data : document
      )))
      message.success('文档草稿已保存')
    } finally {
      setSavingDocument(false)
    }
  }, [
    documentTextDraft,
    documentTitleDraft,
    message,
    openedProject,
    savingDocument,
    selectedDocument,
  ])

  const restoreDocumentVersion = React.useCallback(async (
    document: ScreenplayDocument,
  ) => {
    if (!openedProject || openedProject.status === 'archived' || restoringDocumentId) return
    setRestoringDocumentId(document.id)
    try {
      const result = await services.screenplay.restoreScreenplayDocument({
        documentId: document.id,
      })
      if (!result.success || !result.data) {
        message.error(result.error || '恢复历史版本失败')
        return
      }
      await Promise.all([
        loadProjectDocuments(openedProject.id),
        loadProjectSourceRefs(openedProject.id),
      ])
      openDocument(result.data)
      message.success(`已恢复为 ${DOCUMENT_KIND_LABELS[result.data.kind]} v${result.data.version} 草稿`)
    } finally {
      setRestoringDocumentId(null)
    }
  }, [
    loadProjectDocuments,
    loadProjectSourceRefs,
    message,
    openDocument,
    openedProject,
    restoringDocumentId,
  ])

  const toggleProjectArchived = React.useCallback(async () => {
    if (!openedProject || updatingProjectStatus) return
    const nextStatus = openedProject.status === 'archived' ? 'active' : 'archived'
    if (nextStatus === 'archived' && agentRunning) {
      stopAgent()
    }
    setUpdatingProjectStatus(true)
    try {
      const result = await services.screenplay.updateScreenplayProject({
        projectId: openedProject.id,
        patch: { status: nextStatus },
      })
      if (!result.success || !result.data) {
        message.error(result.error || '更新项目状态失败')
        return
      }
      setOpenedProject(result.data)
      setProjects((current) => current.map((project) => (
        project.id === result.data?.id ? result.data : project
      )))
      message.success(nextStatus === 'archived' ? '项目已归档并切换为只读' : '项目已恢复，可以继续创作')
    } finally {
      setUpdatingProjectStatus(false)
    }
  }, [
    agentRunning,
    message,
    openedProject,
    stopAgent,
    updatingProjectStatus,
  ])

  const stageLabel = stage === 'source'
    ? '选择起点'
    : stage === 'brief'
      ? '创作简报'
      : stage === 'handoff'
        ? 'Agent 交接预览'
        : '剧本项目'
  const milestone = openedProject ? nextMilestone(openedProject) : null
  const proposalAdvancesStage = agentProposal?.kind !== 'scene_draft'
    || agentProposal.contentJson.isComplete === true
  const proposalWillAdvance = openedProject?.active_stage !== 'review'
    && proposalAdvancesStage
    && !(
      openedProject?.active_stage === 'brief'
      && agentProposal?.kind === 'source_analysis'
    )
  const proposalReviewIssues = agentProposal?.kind === 'review'
    && Array.isArray(agentProposal.contentJson.issues)
    ? agentProposal.contentJson.issues
    : []
  const criticalReviewIssueCount = proposalReviewIssues.filter((issue) => (
    issue
    && typeof issue === 'object'
    && (issue as { severity?: unknown }).severity === 'critical'
  )).length
  const proposalReviewAffectedSceneCount = new Set(
    proposalReviewIssues.flatMap((issue) => (
      issue
      && typeof issue === 'object'
      && Array.isArray((issue as { sceneIds?: unknown }).sceneIds)
        ? (issue as { sceneIds: unknown[] }).sceneIds.map(String)
        : []
    )),
  ).size
  const proposalReviewExecutionFieldCount = new Set(
    proposalReviewIssues.flatMap((issue) => (
      issue
      && typeof issue === 'object'
      && Array.isArray((issue as { executionFields?: unknown }).executionFields)
        ? (issue as { executionFields: unknown[] }).executionFields.map(String)
        : []
    )),
  ).size
  const proposalVerificationResults = agentProposal?.kind === 'review'
    && Array.isArray(agentProposal.contentJson.verificationResults)
    ? agentProposal.contentJson.verificationResults
    : []
  const verifiedPriorIssueCount = proposalVerificationResults.filter(
    (result) => (
      result
      && typeof result === 'object'
      && (result as { status?: unknown }).status === 'verified'
    ),
  ).length
  const failedVerificationCount = (
    proposalVerificationResults.length - verifiedPriorIssueCount
  )
  const proposalIssueResolutions = agentProposal?.kind === 'scene_draft'
    && Array.isArray(agentProposal.contentJson.issueResolutions)
    ? agentProposal.contentJson.issueResolutions
    : []
  const resolvedReviewIssueCount = proposalIssueResolutions.filter(
    (resolution) => (
      resolution
      && typeof resolution === 'object'
      && (resolution as { status?: unknown }).status === 'resolved'
    ),
  ).length
  const partiallyResolvedReviewIssueCount = proposalIssueResolutions.filter(
    (resolution) => (
      resolution
      && typeof resolution === 'object'
      && (resolution as { status?: unknown }).status === 'partially_resolved'
    ),
  ).length
  const reassessedSceneCount = agentProposal?.kind === 'scene_draft'
    && Array.isArray(agentProposal.contentJson.reassessedSceneIds)
    ? agentProposal.contentJson.reassessedSceneIds.length
    : 0
  const proposalSourceAnalysis = agentProposal?.kind === 'source_analysis'
    && agentProposal.contentJson.analysis
    && typeof agentProposal.contentJson.analysis === 'object'
    ? agentProposal.contentJson.analysis as Record<string, unknown>
    : null
  const sourceAnalysisCharacterCount = Array.isArray(
    proposalSourceAnalysis?.characters,
  ) ? proposalSourceAnalysis.characters.length : 0
  const sourceAnalysisEventCount = Array.isArray(
    proposalSourceAnalysis?.plotEvents,
  ) ? proposalSourceAnalysis.plotEvents.length : 0
  const sourceAnalysisEvidenceCount = Array.isArray(
    proposalSourceAnalysis?.evidence,
  ) ? proposalSourceAnalysis.evidence.length : 0
  const sourceAnalysisCoverage = proposalSourceAnalysis?.coverage
    && typeof proposalSourceAnalysis.coverage === 'object'
    ? proposalSourceAnalysis.coverage as Record<string, unknown>
    : null
  const sourceAnalysisSelectedChapterCount = Number(
    sourceAnalysisCoverage?.selectedChapterCount || 0,
  )
  const sourceAnalysisReadChapterCount = Array.isArray(
    sourceAnalysisCoverage?.readChapterIds,
  ) ? sourceAnalysisCoverage.readChapterIds.length : 0
  const sourceAnalysisSampledChapterCount = Array.isArray(
    sourceAnalysisCoverage?.sampledChapterIds,
  ) ? sourceAnalysisCoverage.sampledChapterIds.length : 0
  const proposalCreativeBrief = agentProposal?.kind === 'creative_brief'
    && agentProposal.contentJson.brief
    && typeof agentProposal.contentJson.brief === 'object'
    ? agentProposal.contentJson.brief as Record<string, unknown>
    : null
  const proposalFormatPlan = proposalCreativeBrief?.formatPlan
    && typeof proposalCreativeBrief.formatPlan === 'object'
    ? proposalCreativeBrief.formatPlan as Record<string, unknown>
    : null
  const proposalAdaptationDecisions = Array.isArray(
    proposalCreativeBrief?.adaptationDecisions,
  ) ? proposalCreativeBrief.adaptationDecisions : []
  const anchoredAdaptationDecisionCount = proposalAdaptationDecisions.filter(
    (decision) => (
      decision
      && typeof decision === 'object'
      && Array.isArray((decision as { sourceAnchors?: unknown }).sourceAnchors)
      && (decision as { sourceAnchors: unknown[] }).sourceAnchors.length > 0
    ),
  ).length
  const acknowledgedSourceLimitationCount = Array.isArray(
    proposalCreativeBrief?.acknowledgedSourceLimitations,
  ) ? proposalCreativeBrief.acknowledgedSourceLimitations.length : 0
  const proposalFormatScale = proposalFormatPlan?.episodeCount
    ? `${String(proposalFormatPlan.episodeCount)} 集 × ${
      String(proposalFormatPlan.episodeDurationMinutes || '?')
    } 分钟`
    : proposalFormatPlan?.targetDurationMinutes
      ? `${String(proposalFormatPlan.targetDurationMinutes)} 分钟`
      : '规模待确认'
  const proposalStructureUnits = agentProposal?.kind === 'beat_sheet'
    && Array.isArray(agentProposal.contentJson.beats)
    ? agentProposal.contentJson.beats
    : agentProposal?.kind === 'episode_outline'
      && Array.isArray(agentProposal.contentJson.episodes)
      ? agentProposal.contentJson.episodes
      : []
  const proposalDecisionCoverage = (
    agentProposal?.kind === 'beat_sheet'
    || agentProposal?.kind === 'episode_outline'
  ) && Array.isArray(agentProposal.contentJson.decisionCoverage)
    ? agentProposal.contentJson.decisionCoverage
    : []
  const omittedDecisionCoverageCount = proposalDecisionCoverage.filter(
    (coverage) => (
      coverage
      && typeof coverage === 'object'
      && Array.isArray(
        (coverage as { structureUnitIds?: unknown }).structureUnitIds,
      )
      && (
        coverage as { structureUnitIds: unknown[] }
      ).structureUnitIds.length === 0
    ),
  ).length
  const proposalScenes = agentProposal?.kind === 'scene_list'
    && Array.isArray(agentProposal.contentJson.scenes)
    ? agentProposal.contentJson.scenes
    : []
  const proposalCoveredStructureUnitCount = new Set(
    proposalScenes.flatMap((scene) => (
      scene
      && typeof scene === 'object'
      && Array.isArray((scene as { structureUnitIds?: unknown }).structureUnitIds)
        ? (scene as { structureUnitIds: unknown[] }).structureUnitIds.map(String)
        : []
    )),
  ).size
  const proposalSceneEpisodeCount = new Set(
    proposalScenes.flatMap((scene) => (
      scene
      && typeof scene === 'object'
      && (scene as { episodeNumber?: unknown }).episodeNumber
        ? [String((scene as { episodeNumber: unknown }).episodeNumber)]
        : []
    )),
  ).size
  const proposalSceneExecutions = agentProposal?.kind === 'scene_draft'
    && Array.isArray(agentProposal.contentJson.sceneExecutions)
    ? agentProposal.contentJson.sceneExecutions
    : []
  const proposalUnresolvedSceneNoteCount = proposalSceneExecutions.reduce(
    (total, execution) => (
      total + (
        execution
        && typeof execution === 'object'
        && Array.isArray(
          (execution as { unresolvedNotes?: unknown }).unresolvedNotes,
        )
          ? (
            execution as { unresolvedNotes: unknown[] }
          ).unresolvedNotes.length
          : 0
      )
    ),
    0,
  )
  const selectedPreviousDocument = selectedDocument
    ? previousDocumentVersion(selectedDocument, projectDocuments)
    : null
  const selectedDocumentRefs = selectedDocument
    ? projectSourceRefs.filter((ref) => ref.document_id === selectedDocument.id)
    : []
  const selectedDocumentDiff = React.useMemo(
    () => comparisonDocument && selectedDocument
      ? buildScreenplayLineDiff(
        comparisonDocument.content_text,
        selectedDocument.content_text,
      )
      : [],
    [comparisonDocument, selectedDocument],
  )

  return (
    <div className="screenplay-agent-page">
      <AppHeader
        title="剧本 Agent"
        left={(
          <Button
            type="text"
            size="small"
            icon={<ArrowLeftOutlined />}
            onClick={onBack}
            aria-label="返回首页"
          />
        )}
        right={<span className="screenplay-agent-stage">{stageLabel}</span>}
      />

      <main className="screenplay-agent-main">
        {stage === 'source' && (
          <div className="screenplay-entry">
            <section className="screenplay-agent-hero">
              <span className="screenplay-agent-kicker">SCREENPLAY AGENT</span>
              <h1>从你的故事出发，或从一个念头开始</h1>
              <p>Agent 会先与你建立创作简报，再逐步完成故事梗概、分集结构、场景表与剧本正文。</p>
            </section>

            <section className="screenplay-source-grid" aria-label="选择剧本创作起点">
              <article className="screenplay-source-card screenplay-source-card--books">
                <div className="screenplay-source-card__header">
                  <span className="screenplay-source-icon"><BookOutlined /></span>
                  <div>
                    <span className="screenplay-source-eyebrow">FROM BOOKSHELF</span>
                    <h2>改编书架作品</h2>
                    <p>引用已有的人物、世界观、大纲与章节内容，原作始终保持只读。</p>
                  </div>
                </div>

                {sortedBooks.length > 0 ? (
                  <div className="screenplay-book-strip">
                    {sortedBooks.map((book) => {
                      const isLastOpened = book.id === lastOpenedBookId
                      return (
                        <button
                          key={book.id}
                          type="button"
                          className="screenplay-book-option"
                          onClick={() => startFromBook(book)}
                          aria-label={`引用《${book.title}》创作剧本`}
                        >
                          <span
                            className="screenplay-book-cover"
                            style={{ '--screenplay-book-color': book.cover_color || '#c94361' } as React.CSSProperties}
                          >
                            <span className="screenplay-book-cover__brand">PURR TYPOS</span>
                            <strong>{book.title}</strong>
                            <span className="screenplay-book-cover__mark">✦</span>
                          </span>
                          <span className="screenplay-book-option__meta">
                            <strong>{book.title}</strong>
                            <span>{isLastOpened ? '上次打开 · ' : ''}引用此书 <ArrowRightOutlined /></span>
                          </span>
                        </button>
                      )
                    })}
                  </div>
                ) : (
                  <div className="screenplay-books-empty">
                    <span><FileTextOutlined /></span>
                    <div>
                      <strong>书架还是空的</strong>
                      <p>先创建一部作品，之后就能在这里直接引用。</p>
                    </div>
                    <Button onClick={onOpenBookshelf}>前往书架</Button>
                  </div>
                )}
              </article>

              <button
                type="button"
                className="screenplay-source-card screenplay-source-card--original"
                onClick={startOriginal}
              >
                <span className="screenplay-source-card__spark" aria-hidden>✦</span>
                <span className="screenplay-source-icon"><PlusOutlined /></span>
                <span className="screenplay-source-eyebrow">NEW STORY</span>
                <h2>开启一个新故事</h2>
                <p>只有一句灵感也可以。Agent 会通过提问，和你一起找到人物、冲突与主题。</p>
                <span className="screenplay-source-cta">从零开始 <ArrowRightOutlined /></span>
              </button>
            </section>

            {(projectsLoading || projects.length > 0) && (
              <section className="screenplay-recent-projects">
                <div className="screenplay-recent-projects__header">
                  <div>
                    <span className="screenplay-source-eyebrow">YOUR SCREENPLAYS</span>
                    <h2>继续剧本项目</h2>
                  </div>
                  <span>{projectsLoading ? '正在读取…' : `${projects.length} 个项目`}</span>
                </div>
                {!projectsLoading && (
                  <div className="screenplay-project-strip">
                    {projects.map((project) => {
                      const sourceBook = books.find((book) => book.id === project.source_book_id)
                      return (
                        <button
                          type="button"
                          key={project.id}
                          className="screenplay-project-option"
                          onClick={() => void openProject(project)}
                        >
                          <span className="screenplay-project-option__icon">
                            <VideoCameraOutlined />
                          </span>
                          <span className="screenplay-project-option__copy">
                            <strong>{project.title}</strong>
                            <span>
                              {project.source_kind === 'original'
                                ? '原创故事'
                                : sourceBook
                                  ? `改编自《${sourceBook.title}》`
                                  : '来源作品已移除'}
                              {' · '}
                              {project.format}
                            </span>
                          </span>
                          <span className="screenplay-project-option__stage">
                            {project.status === 'archived'
                              ? `已归档 · ${STAGE_LABELS[project.active_stage]}`
                              : STAGE_LABELS[project.active_stage]}
                          </span>
                          <ArrowRightOutlined />
                        </button>
                      )
                    })}
                  </div>
                )}
              </section>
            )}

            <section className="screenplay-workflow" aria-label="剧本 Agent 工作流程">
              <div className="screenplay-workflow__intro">
                <span className="screenplay-agent-icon"><RobotOutlined /></span>
                <div>
                  <strong>不是一次生成整份剧本</strong>
                  <span>每一步都可回看、调整与确认</span>
                </div>
              </div>
              {[
                ['01', '建立简报'],
                ['02', '拆解结构'],
                ['03', '铺设场景'],
                ['04', '逐场成稿'],
              ].map(([index, label]) => (
                <div className="screenplay-workflow__step" key={index}>
                  <span>{index}</span>
                  <strong>{label}</strong>
                </div>
              ))}
            </section>
          </div>
        )}

        {stage === 'brief' && (
          <div className="screenplay-brief">
            <button type="button" className="screenplay-inline-back" onClick={resetToSource}>
              <ArrowLeftOutlined /> 重新选择起点
            </button>
            <div className="screenplay-brief__heading">
              <span className="screenplay-agent-kicker">CREATIVE BRIEF</span>
              <h1>先给 Agent 一个方向</h1>
              <p>不用现在想清所有细节；留空的部分，会成为 Agent 接下来与你讨论的问题。</p>
            </div>

            <div className="screenplay-brief__layout">
              <aside className="screenplay-brief-source">
                <span className="screenplay-brief-source__label">本次创作来源</span>
                {sourceKind === 'book' && selectedBook ? (
                  <>
                    <span
                      className="screenplay-brief-source__cover"
                      style={{ '--screenplay-book-color': selectedBook.cover_color || '#c94361' } as React.CSSProperties}
                    >
                      <BookOutlined />
                    </span>
                    <h2>《{selectedBook.title}》</h2>
                    <p>Agent 只会在你选定的改编范围内读取正文，并且不会改动原作。</p>
                  </>
                ) : (
                  <>
                    <span className="screenplay-brief-source__cover screenplay-brief-source__cover--original">
                      <PlusOutlined />
                    </span>
                    <h2>原创故事</h2>
                    <p>先保存为独立的剧本项目，不会自动在书架中创建书籍。</p>
                  </>
                )}
                <div className="screenplay-source-boundary">
                  <CheckCircleOutlined />
                  <span>{sourceKind === 'book' ? '原作只读，剧本独立保存' : '先探索，再确认故事设定'}</span>
                </div>
              </aside>

              <section className="screenplay-brief-form">
                <label className="screenplay-field">
                  <span>项目名称</span>
                  <input
                    value={projectTitle}
                    onChange={(event) => setProjectTitle(event.target.value)}
                    placeholder="给这次创作起个名字"
                  />
                </label>

                {sourceKind === 'book' && (
                  <fieldset className="screenplay-field screenplay-scope-field">
                    <legend>原作改编范围</legend>
                    <div className="screenplay-option-row">
                      {SOURCE_SCOPE_OPTIONS
                        .filter((option) => (
                          !option.requiresVolumes
                          || sourceStructure.volumes.length > 0
                        ))
                        .map((option) => (
                          <button
                            type="button"
                            key={option.value}
                            className={sourceScopeMode === option.value
                              ? 'is-selected'
                              : ''}
                            onClick={() => chooseSourceScopeMode(option.value)}
                          >
                            {option.label}
                          </button>
                        ))}
                    </div>

                    {sourceChaptersLoading ? (
                      <div className="screenplay-scope-loading">
                        <LoadingOutlined /> 正在读取章节目录…
                      </div>
                    ) : sourceChaptersError ? (
                      <div className="screenplay-scope-warning">
                        {sourceChaptersError}
                      </div>
                    ) : (
                      <>
                        {(sourceScopeMode === 'first_chapters'
                          || sourceScopeMode === 'first_volumes') && (
                          <div className="screenplay-scope-count">
                            <span>
                              {sourceScopeMode === 'first_chapters'
                                ? '从开头连续选择'
                                : '从第一卷连续选择'}
                            </span>
                            <InputNumber
                              value={sourceScopeCount}
                              min={1}
                              max={sourceScopeMode === 'first_chapters'
                                ? Math.max(1, sourceStructure.chapters.length)
                                : Math.max(1, sourceStructure.volumes.length)}
                              onChange={(value) => setSourceScopeCount(value ?? 1)}
                            />
                            <span>
                              {sourceScopeMode === 'first_chapters' ? '章' : '卷'}
                            </span>
                          </div>
                        )}

                        {sourceScopeMode === 'selected_chapters' && (
                          <MultiSelect
                            value={selectedSourceChapterIds}
                            onChange={setSelectedSourceChapterIds}
                            options={sourceStructure.chapters.map((chapter) => ({
                              value: chapter.id,
                              label: `${chapter.index}. ${
                                chapter.volumeTitle
                                  ? `${chapter.volumeTitle} / `
                                  : ''
                              }${chapter.title}`,
                            }))}
                            placeholder="选择要改编的章节"
                            allowClear
                            className="screenplay-scope-select"
                          />
                        )}

                        {sourceScopeMode === 'selected_volumes' && (
                          <MultiSelect
                            value={selectedSourceVolumeIds}
                            onChange={setSelectedSourceVolumeIds}
                            options={sourceStructure.volumes.map((volume) => ({
                              value: volume.id,
                              label: `${volume.title} · ${volume.chapterIds.length} 章`,
                            }))}
                            placeholder="选择要改编的卷"
                            allowClear
                            className="screenplay-scope-select"
                          />
                        )}

                        <div className={`screenplay-scope-summary ${
                          sourceScopeSelection.error ? 'is-warning' : ''
                        }`}>
                          <CheckCircleOutlined />
                          <span>{sourceScopeSelection.summary}</span>
                        </div>
                        {sourceStructure.volumes.length === 0
                          && sourceStructure.chapters.length > 0 && (
                            <small>当前作品没有分卷结构，可按整本、章节数量或指定章节改编。</small>
                          )}
                      </>
                    )}
                  </fieldset>
                )}

                <fieldset className="screenplay-field">
                  <legend>剧本形态</legend>
                  <div className="screenplay-option-row">
                    {FORMAT_OPTIONS.map((option) => (
                      <button
                        type="button"
                        key={option}
                        className={format === option ? 'is-selected' : ''}
                        onClick={() => setFormat(option)}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                </fieldset>

                <fieldset className="screenplay-field">
                  <legend>{sourceKind === 'book' ? '改编方式' : '探索起点'}</legend>
                  <div className="screenplay-option-row">
                    {(sourceKind === 'book' ? ADAPTATION_OPTIONS : ORIGINAL_OPTIONS).map((option) => (
                      <button
                        type="button"
                        key={option}
                        className={approach === option ? 'is-selected' : ''}
                        onClick={() => setApproach(option)}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                </fieldset>

                <label className="screenplay-field">
                  <span>{sourceKind === 'book' ? '这次最想怎样改编？' : '你现在有怎样的故事念头？'}</span>
                  <textarea
                    value={premise}
                    onChange={(event) => setPremise(event.target.value)}
                    rows={5}
                    placeholder={sourceKind === 'book'
                      ? '例如：保留主角关系，把故事压缩成一部悬疑电影；结局希望更有余味……'
                      : '例如：一个替人保管记忆的人，发现自己最重要的回忆也属于别人……'}
                  />
                  <small>可选。暂时没有答案时，Agent 会先从澄清问题开始。</small>
                </label>

                <div className="screenplay-brief-action">
                  <div>
                    <strong>下一步：建立 Agent 任务</strong>
                    <span>先形成创作简报，不会直接生成完整剧本。</span>
                  </div>
                  <Button
                    type="primary"
                    size="large"
                    icon={<ArrowRightOutlined />}
                    iconPosition="end"
                    onClick={buildHandoff}
                  >
                    预览 Agent 交接
                  </Button>
                </div>
              </section>
            </div>
          </div>
        )}

        {stage === 'handoff' && launchDraft && (
          <div className="screenplay-handoff">
            <button type="button" className="screenplay-inline-back" onClick={() => setStage('brief')}>
              <ArrowLeftOutlined /> 返回修改简报
            </button>
            <section className="screenplay-handoff-card">
              <div className="screenplay-handoff-card__status">
                <span><CheckCircleOutlined /></span>
                <div>
                  <span className="screenplay-agent-kicker">AGENT HANDOFF</span>
                  <h1>创作任务已准备好</h1>
                  <p>这是入口将提交给剧本 Agent 的结构化任务预览。</p>
                </div>
              </div>

              <div className="screenplay-handoff-summary">
                <div><span>项目</span><strong>{launchDraft.projectTitle}</strong></div>
                <div><span>来源</span><strong>{launchDraft.sourceBookTitle ? `《${launchDraft.sourceBookTitle}》` : '原创故事'}</strong></div>
                <div><span>改编范围</span><strong>{launchDraft.sourceScopeSummary}</strong></div>
                <div><span>形态</span><strong>{launchDraft.format}</strong></div>
                <div><span>{launchDraft.sourceKind === 'book' ? '改编方式' : '探索起点'}</span><strong>{launchDraft.approach}</strong></div>
              </div>

              <div className="screenplay-agent-first-turn">
                <div className="screenplay-agent-first-turn__title">
                  <RobotOutlined />
                  <strong>Agent 的第一轮任务</strong>
                </div>
                <p>
                  {launchDraft.sourceKind === 'book'
                    ? `只在“${launchDraft.sourceScopeSummary}”范围内理解《${launchDraft.sourceBookTitle}》的素材，形成一份${launchDraft.format}改编简报；指出需要用户决定的取舍，并为引用的原作信息保留来源。`
                    : `围绕现有灵感，通过少量关键问题确认主角、核心冲突与主题方向，再形成一份${launchDraft.format}创作简报。`}
                </p>
                {launchDraft.premise && <blockquote>{launchDraft.premise}</blockquote>}
              </div>

              <div className="screenplay-handoff-boundaries">
                <div><BookOutlined /><span><strong>素材边界</strong>{launchDraft.sourceKind === 'book' ? `只读取${launchDraft.sourceScopeSummary}` : '不读取书架作品'}</span></div>
                <div><TeamOutlined /><span><strong>协作边界</strong>关键创作取舍交给用户确认</span></div>
                <div><EditOutlined /><span><strong>写入边界</strong>所有产物进入独立剧本项目</span></div>
              </div>

              <div className="screenplay-handoff-card__footer">
                <span>创建后会保存项目与首个创作简报版本，之后可以从本页继续。</span>
                <Button
                  type="primary"
                  icon={<VideoCameraOutlined />}
                  loading={creatingProject}
                  onClick={() => void createProject()}
                >
                  创建剧本项目
                </Button>
              </div>
            </section>
          </div>
        )}

        {stage === 'project' && openedProject && (
          <div className="screenplay-project">
            <button type="button" className="screenplay-inline-back" onClick={resetToSource}>
              <ArrowLeftOutlined /> 返回剧本项目列表
            </button>
            <section className="screenplay-project-workspace">
              <header className="screenplay-project-workspace__header">
                <span className="screenplay-project-workspace__icon">
                  <VideoCameraOutlined />
                </span>
                <div>
                  <span className="screenplay-agent-kicker">SCREENPLAY PROJECT</span>
                  <h1>{openedProject.title}</h1>
                  <p>
                    {openedProject.source_kind === 'original'
                      ? '原创故事'
                      : openedProject.source_book_id
                        ? `改编自《${books.find((book) => book.id === openedProject.source_book_id)?.title || '书架作品'}》`
                        : '来源作品已移除，已有剧本文档仍然保留'}
                  </p>
                </div>
                <div className="screenplay-project-workspace__actions">
                  <span className="screenplay-project-workspace__stage">
                    {openedProject.status === 'archived'
                      ? '只读归档'
                      : STAGE_LABELS[openedProject.active_stage]}
                  </span>
                  <Button
                    size="small"
                    icon={openedProject.status === 'archived'
                      ? <UndoOutlined />
                      : <HistoryOutlined />}
                    loading={updatingProjectStatus}
                    onClick={() => void toggleProjectArchived()}
                  >
                    {openedProject.status === 'archived' ? '恢复项目' : '归档项目'}
                  </Button>
                </div>
              </header>

              <div className="screenplay-project-workspace__meta">
                <div><span>剧本形态</span><strong>{openedProject.format}</strong></div>
                <div><span>{openedProject.source_kind === 'book' ? '改编方式' : '探索起点'}</span><strong>{openedProject.approach}</strong></div>
                <div><span>改编范围</span><strong>{openedProject.source_kind === 'book' ? describePersistedSourceScope(openedProject.source_scope) : '原创故事'}</strong></div>
                <div>
                  <span>项目状态</span>
                  <strong>
                    {openedProject.status === 'archived'
                      ? '已归档'
                      : openedProject.active_stage === 'completed'
                        ? '已完成并固化交付'
                        : '创作中'}
                  </strong>
                </div>
              </div>

              <div className="screenplay-project-workspace__body">
                <section className="screenplay-project-documents">
                  <div className="screenplay-project-section-title">
                    <div>
                      <span className="screenplay-source-eyebrow">DOCUMENT VERSIONS</span>
                      <h2>项目文档</h2>
                    </div>
                    <span>{projectLoading ? '读取中…' : `${projectDocuments.length} 个版本`}</span>
                  </div>
                  {!projectLoading && projectDocuments.length === 0 ? (
                    <div className="screenplay-project-empty">还没有项目文档</div>
                  ) : (
                    <div className="screenplay-document-list">
                      {projectDocuments.map((document) => {
                        const previousVersion = previousDocumentVersion(
                          document,
                          projectDocuments,
                        )
                        return (
                        <article className="screenplay-document-item" key={document.id}>
                          <span className="screenplay-document-item__icon"><FileTextOutlined /></span>
                          <div>
                            <strong>{document.title}</strong>
                            <span>
                              {DOCUMENT_KIND_LABELS[document.kind]} · v{document.version}
                              {' · '}
                              {document.status === 'draft'
                                ? '草稿'
                                : document.status === 'accepted'
                                  ? '已接受'
                                  : '已替代'}
                              {projectSourceRefs.some((ref) => ref.document_id === document.id)
                                ? ` · ${projectSourceRefs.filter((ref) => ref.document_id === document.id).length} 条来源`
                                : ''}
                            </span>
                          </div>
                          <span className="screenplay-document-item__actions">
                            <span className={`screenplay-document-status is-${document.status}`}>
                              {document.status === 'draft' ? '可编辑' : document.status === 'accepted' ? '当前版本' : '历史版本'}
                            </span>
                            <Button
                              size="small"
                              type="text"
                              icon={<EyeOutlined />}
                              onClick={() => openDocument(document)}
                            >
                              查看
                            </Button>
                            {previousVersion && (
                              <Button
                                size="small"
                                type="text"
                                icon={<HistoryOutlined />}
                                onClick={() => openDocument(document, true)}
                              >
                                对比
                              </Button>
                            )}
                          </span>
                        </article>
                        )
                      })}
                    </div>
                  )}
                </section>

                <aside className="screenplay-project-next">
                  <span className="screenplay-agent-icon"><RobotOutlined /></span>
                  <span className="screenplay-source-eyebrow">NEXT MILESTONE</span>
                  <h2>{milestone?.title}</h2>
                  <p>剧本 Agent 已绑定当前项目。{milestone?.description}</p>
                  {openedProject.premise && <blockquote>{openedProject.premise}</blockquote>}
                  <Button
                    type="primary"
                    block
                    loading={agentRunning}
                    disabled={openedProject.status === 'archived'}
                    onClick={runAgent}
                  >
                    {openedProject.status === 'archived'
                      ? '恢复项目后继续创作'
                      : agentRunning
                        ? 'Agent 正在策划'
                        : '启动 Agent 策划'}
                  </Button>
                  {acceptedScreenplayDraft && (
                    <div className="screenplay-project-export">
                      <span>
                        {openedProject.delivery_manifest
                          ? `最终交付已固化 · ${
                            openedProject.delivery_manifest.documents.length
                          } 项文档`
                          : '导出已接受整稿'}
                      </span>
                      <div>
                        <select
                          value={exportFormat}
                          onChange={(event) => setExportFormat(
                            event.target.value as typeof exportFormat,
                          )}
                          disabled={exportingScreenplay}
                        >
                          <option value="fountain">Fountain</option>
                          <option value="pdf">标准 PDF</option>
                          <option value="markdown">Markdown</option>
                          <option value="txt">纯文本</option>
                          {openedProject.delivery_manifest && (
                            <option value="json">交付清单 JSON</option>
                          )}
                        </select>
                        <Button
                          icon={<ExportOutlined />}
                          loading={exportingScreenplay}
                          onClick={() => void exportScreenplay()}
                        >
                          导出
                        </Button>
                      </div>
                    </div>
                  )}
                </aside>
              </div>

              <section className={`screenplay-agent-studio ${openedProject.status === 'archived' ? 'is-readonly' : ''}`}>
                <header className="screenplay-agent-studio__header">
                  <div>
                    <span className="screenplay-source-eyebrow">SCREENPLAY AGENT</span>
                    <h2>创作协作台</h2>
                    <p>
                      {openedProject.status === 'archived'
                        ? '项目已归档：可以查看、比较和导出版本，恢复项目后才能继续创作。'
                        : '当前输出先作为候选内容展示，只有你点击保存后才会进入项目版本。'}
                    </p>
                  </div>
                  {modelConfigs.length > 0 ? (
                    <label className="screenplay-agent-model">
                      <span>使用模型</span>
                      <select
                        value={selectedModelId}
                        onChange={(event) => setSelectedModelId(event.target.value)}
                        disabled={agentRunning || openedProject.status === 'archived'}
                      >
                        {modelConfigs.map((model) => (
                          <option value={model.id} key={model.id}>
                            {model.nickname?.trim() || model.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <Button
                      disabled={openedProject.status === 'archived'}
                      onClick={onOpenSettings}
                    >
                      配置模型
                    </Button>
                  )}
                </header>

                <div className="screenplay-agent-studio__compose">
                  <textarea
                    value={agentPrompt}
                    onChange={(event) => setAgentPrompt(event.target.value)}
                    rows={3}
                    disabled={agentRunning || openedProject.status === 'archived'}
                    placeholder="例如：先帮我找出这份简报中最需要确认的三个创作取舍"
                  />
                  <div className="screenplay-agent-studio__actions">
                    <span>
                      {openedProject.source_kind === 'book'
                        ? '原作只读；命中的素材会绑定到本轮 Run，保存后随文档留档。'
                        : '本轮只使用当前项目与已有版本。'}
                    </span>
                    {agentRunning ? (
                      <Button onClick={stopAgent}>停止</Button>
                    ) : (
                      <Button
                        type="primary"
                        icon={<RobotOutlined />}
                        disabled={openedProject.status === 'archived'}
                        onClick={runAgent}
                      >
                        发送给剧本 Agent
                      </Button>
                    )}
                  </div>
                </div>

                {(agentRunning || agentThinking || agentResponse || agentProposal) && (
                  <div className="screenplay-agent-result">
                    <div className="screenplay-agent-result__title">
                      <span>
                        {agentRunning
                          ? <><LoadingOutlined spin /> {agentActivity || 'Agent 正在形成提案'}</>
                          : <><CheckCircleOutlined /> 本轮候选结果</>}
                      </span>
                      {!agentRunning && agentRunId && projectSourceRefs.some(
                        (ref) => ref.agent_run_id === agentRunId,
                      ) && (
                        <span className="screenplay-agent-result__sources">
                          {projectSourceRefs.filter(
                            (ref) => ref.agent_run_id === agentRunId,
                          ).length} 条可追溯来源
                        </span>
                      )}
                      {!agentRunning && agentResponse.trim() && !agentProposal && (
                        <Button
                          icon={<SaveOutlined />}
                          loading={savingAgentDraft}
                          disabled={
                            savedAgentDocumentId != null
                            || openedProject.status === 'archived'
                          }
                          onClick={() => void saveAgentDraft()}
                        >
                          {savedAgentDocumentId ? '已保存到项目' : '保存为文档草稿'}
                        </Button>
                      )}
                    </div>
                    {agentThinking && !agentResponse && (
                      <div className="screenplay-agent-result__thinking">
                        正在分析项目上下文…
                      </div>
                    )}
                    {agentResponse && (
                      <div className="screenplay-agent-result__content">
                        {agentResponse}
                      </div>
                    )}
                    {agentProposal && (
                      <article className="screenplay-document-proposal">
                        <header>
                          <div>
                            <span className="screenplay-source-eyebrow">FORMAL PROPOSAL</span>
                            <h3>{agentProposal.title}</h3>
                            <span>
                              {DOCUMENT_KIND_LABELS[agentProposal.kind]}
                              {' · '}
                              {acceptedAgentDocumentId
                                ? '已接受'
                                : savedAgentDocumentId
                                  ? '已保存草稿，等待接受'
                                  : '尚未写入项目'}
                            </span>
                          </div>
                          <span className={`screenplay-document-status ${acceptedAgentDocumentId ? 'is-accepted' : ''}`}>
                            {acceptedAgentDocumentId ? '当前版本' : '待审阅'}
                          </span>
                        </header>
                        <div className="screenplay-document-proposal__content">
                          {agentProposal.contentText}
                        </div>
                        {proposalSourceAnalysis && (
                          <div className="screenplay-review-proposal-summary">
                            <span>
                              <strong>{sourceAnalysisCharacterCount}</strong>
                              个人物
                            </span>
                            <span>
                              <strong>{sourceAnalysisEventCount}</strong>
                              个关键事件
                            </span>
                            <span>
                              <strong>{sourceAnalysisEvidenceCount}</strong>
                              条事实证据
                            </span>
                            <span>
                              精读
                              <strong>{sourceAnalysisReadChapterCount}</strong>
                              章 · 抽样
                              <strong>{sourceAnalysisSampledChapterCount}</strong>
                              章 · 共 {sourceAnalysisSelectedChapterCount} 章
                            </span>
                          </div>
                        )}
                        {proposalCreativeBrief && proposalFormatPlan && (
                          <div className="screenplay-review-proposal-summary">
                            <span>
                              目标
                              <strong>
                                {String(proposalFormatPlan.targetFormat || '待定')}
                              </strong>
                              · {proposalFormatScale}
                            </span>
                            <span>
                              <strong>{proposalAdaptationDecisions.length}</strong>
                              项改编决策
                            </span>
                            <span>
                              <strong>{anchoredAdaptationDecisionCount}</strong>
                              项锚定原作证据
                            </span>
                            {acknowledgedSourceLimitationCount > 0 && (
                              <span>
                                已承接
                                <strong>{acknowledgedSourceLimitationCount}</strong>
                                项原作分析局限
                              </span>
                            )}
                          </div>
                        )}
                        {proposalStructureUnits.length > 0 && (
                          <div className="screenplay-review-proposal-summary">
                            <span>
                              <strong>{proposalStructureUnits.length}</strong>
                              {agentProposal.kind === 'episode_outline'
                                ? ' 集'
                                : ' 个节拍'}
                            </span>
                            <span>
                              已覆盖
                              <strong>{proposalDecisionCoverage.length}</strong>
                              项改编决策
                            </span>
                            {omittedDecisionCoverageCount > 0 && (
                              <span>
                                其中
                                <strong>{omittedDecisionCoverageCount}</strong>
                                项明确删减
                              </span>
                            )}
                          </div>
                        )}
                        {proposalScenes.length > 0 && (
                          <div className="screenplay-review-proposal-summary">
                            <span>
                              <strong>{proposalScenes.length}</strong>
                              个场景
                            </span>
                            <span>
                              已承接
                              <strong>{proposalCoveredStructureUnitCount}</strong>
                              个结构单元
                            </span>
                            {proposalSceneEpisodeCount > 0 && (
                              <span>
                                覆盖
                                <strong>{proposalSceneEpisodeCount}</strong>
                                集
                              </span>
                            )}
                          </div>
                        )}
                        {proposalSceneExecutions.length > 0 && (
                          <div className="screenplay-review-proposal-summary">
                            <span>
                              <strong>{proposalSceneExecutions.length}</strong>
                              场已完成执行检查
                            </span>
                            <span>
                              <strong>{proposalUnresolvedSceneNoteCount}</strong>
                              项未解决连续性事项
                            </span>
                            <span>
                              {agentProposal.contentJson.isComplete === true
                                ? '完整整稿'
                                : '滚动整稿'}
                            </span>
                          </div>
                        )}
                        {agentProposal.kind === 'review' && (
                          <div className="screenplay-review-proposal-summary">
                            <span><strong>{proposalReviewIssues.length}</strong> 个审阅问题</span>
                            <span><strong>{criticalReviewIssueCount}</strong> 个关键问题</span>
                            <span>
                              影响
                              <strong>{proposalReviewAffectedSceneCount}</strong>
                              个场景
                            </span>
                            <span>
                              覆盖
                              <strong>{proposalReviewExecutionFieldCount}</strong>
                              类执行检查
                            </span>
                            <span>
                              结论：
                              <strong>{String(agentProposal.contentJson.verdict || '待定')}</strong>
                            </span>
                            {proposalVerificationResults.length > 0 && (
                              <>
                                <span>
                                  已核验
                                  <strong>{proposalVerificationResults.length}</strong>
                                  个历史问题
                                </span>
                                <span>
                                  通过
                                  <strong>{verifiedPriorIssueCount}</strong>
                                  个 · 未通过
                                  <strong>{failedVerificationCount}</strong>
                                  个
                                </span>
                              </>
                            )}
                          </div>
                        )}
                        {proposalIssueResolutions.length > 0 && (
                          <div className="screenplay-review-proposal-summary">
                            <span>
                              已逐项回应
                              <strong>{proposalIssueResolutions.length}</strong>
                              个审阅问题
                            </span>
                            <span>
                              完全解决
                              <strong>{resolvedReviewIssueCount}</strong>
                              个
                            </span>
                            {partiallyResolvedReviewIssueCount > 0 && (
                              <span>
                                部分解决
                                <strong>{partiallyResolvedReviewIssueCount}</strong>
                                个
                              </span>
                            )}
                            <span>
                              已重评
                              <strong>{reassessedSceneCount}</strong>
                              个场景
                            </span>
                          </div>
                        )}
                        <footer>
                          <span>
                            {proposalWillAdvance
                              ? '保存只创建草稿；接受后锁定当前版本并推进到下一阶段。'
                              : agentProposal.kind === 'scene_draft'
                                && agentProposal.contentJson.isComplete !== true
                                ? '保存只创建草稿；接受后更新当前滚动整稿，继续创作下一场。'
                                : '保存只创建草稿；接受后更新当前审阅或完整剧本版本。'}
                          </span>
                          <div>
                            <Button
                              icon={<SaveOutlined />}
                              loading={savingAgentDraft && !acceptingAgentDraft}
                              disabled={
                                savedAgentDocumentId != null
                                || acceptingAgentDraft
                                || openedProject.status === 'archived'
                              }
                              onClick={() => {
                                void saveAgentProposal().then((documentId) => {
                                  if (documentId) message.success('提案已保存为项目草稿')
                                })
                              }}
                            >
                              {savedAgentDocumentId ? '草稿已保存' : '保存草稿'}
                            </Button>
                            <Button
                              type="primary"
                              icon={<CheckCircleOutlined />}
                              loading={acceptingAgentDraft}
                              disabled={
                                acceptedAgentDocumentId != null
                                || agentRunning
                                || savingAgentDraft
                                || openedProject.status === 'archived'
                              }
                              onClick={() => void acceptAgentProposal()}
                            >
                              {acceptedAgentDocumentId
                                ? proposalWillAdvance
                                  ? '已接受并推进'
                                  : '已接受为当前整稿'
                                : proposalWillAdvance
                                  ? '接受并推进'
                                  : agentProposal.kind === 'scene_draft'
                                    && agentProposal.contentJson.isComplete !== true
                                    ? '接受本场'
                                    : '接受当前版本'}
                            </Button>
                          </div>
                        </footer>
                      </article>
                    )}
                  </div>
                )}
              </section>
            </section>
          </div>
        )}

        <Modal
          open={selectedDocument != null}
          title={selectedDocument
            ? `${DOCUMENT_KIND_LABELS[selectedDocument.kind]} · v${selectedDocument.version}`
            : '剧本文档'}
          width="min(920px, calc(100vw - 40px))"
          destroyOnHidden
          onCancel={() => {
            setSelectedDocument(null)
            setComparisonDocument(null)
          }}
          footer={selectedDocument ? (
            <div className="screenplay-version-modal__footer">
              <div>
                {selectedPreviousDocument && (
                  <Button
                    icon={<HistoryOutlined />}
                    onClick={() => setComparisonDocument((current) => (
                      current ? null : selectedPreviousDocument
                    ))}
                  >
                    {comparisonDocument ? '返回文档' : `与 v${selectedPreviousDocument.version} 对比`}
                  </Button>
                )}
                {selectedDocument.status !== 'draft' && (
                  <Button
                    icon={<UndoOutlined />}
                    loading={restoringDocumentId === selectedDocument.id}
                    disabled={openedProject?.status === 'archived'}
                    onClick={() => void restoreDocumentVersion(selectedDocument)}
                  >
                    恢复为新草稿
                  </Button>
                )}
              </div>
              <div>
                <Button
                  onClick={() => {
                    setSelectedDocument(null)
                    setComparisonDocument(null)
                  }}
                >
                  关闭
                </Button>
                {selectedDocument.status === 'draft' && !comparisonDocument && (
                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={savingDocument}
                    disabled={openedProject?.status === 'archived'}
                    onClick={() => void saveSelectedDocument()}
                  >
                    保存草稿
                  </Button>
                )}
              </div>
            </div>
          ) : null}
          className="screenplay-version-modal"
        >
          {selectedDocument && (
            <div className="screenplay-version-viewer">
              <div className="screenplay-version-viewer__meta">
                <span className={`screenplay-document-status is-${selectedDocument.status}`}>
                  {selectedDocument.status === 'draft'
                    ? '草稿'
                    : selectedDocument.status === 'accepted'
                      ? '当前已接受版本'
                      : '已替代历史版本'}
                </span>
                <span>{selectedDocumentRefs.length} 条来源</span>
                <span>{selectedDocument.derived_from_ids.length} 个上游版本</span>
                {selectedDocument.update_time && (
                  <span>更新于 {new Date(selectedDocument.update_time).toLocaleString()}</span>
                )}
              </div>

              {comparisonDocument ? (
                <>
                  <div className="screenplay-version-compare__heading">
                    <span>旧版 · v{comparisonDocument.version}</span>
                    <strong>{comparisonDocument.title}</strong>
                    <span>当前 · v{selectedDocument.version}</span>
                    <strong>{selectedDocument.title}</strong>
                  </div>
                  <div className="screenplay-version-diff" role="table" aria-label="版本逐行差异">
                    {selectedDocumentDiff.map((line, index) => (
                      <div
                        className={`screenplay-version-diff__line is-${line.type}`}
                        role="row"
                        key={`${line.type}-${line.oldLine}-${line.newLine}-${index}`}
                      >
                        <span>{line.oldLine ?? ''}</span>
                        <span>{line.newLine ?? ''}</span>
                        <span aria-hidden>
                          {line.type === 'added' ? '+' : line.type === 'removed' ? '−' : ' '}
                        </span>
                        <code>{line.text || ' '}</code>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="screenplay-version-editor">
                  <label>
                    <span>文档标题</span>
                    <Input
                      value={documentTitleDraft}
                      disabled={
                        selectedDocument.status !== 'draft'
                        || openedProject?.status === 'archived'
                      }
                      onChange={(event) => setDocumentTitleDraft(event.target.value)}
                    />
                  </label>
                  <label>
                    <span>正文内容</span>
                    <Input.TextArea
                      value={documentTextDraft}
                      rows={18}
                      disabled={
                        selectedDocument.status !== 'draft'
                        || openedProject?.status === 'archived'
                      }
                      onChange={(event) => setDocumentTextDraft(event.target.value)}
                    />
                  </label>
                  {selectedDocument.derived_from_ids.length > 0 && (
                    <div className="screenplay-version-lineage">
                      <strong>版本来源</strong>
                      <span>
                        {selectedDocument.derived_from_ids.map((parentId) => {
                          const parent = projectDocuments.find((item) => item.id === parentId)
                          return parent
                            ? `${DOCUMENT_KIND_LABELS[parent.kind]} v${parent.version}`
                            : parentId
                        }).join('、')}
                      </span>
                    </div>
                  )}
                  {selectedDocumentRefs.length > 0 && (
                    <div className="screenplay-version-sources">
                      <strong>素材引用</strong>
                      {selectedDocumentRefs.map((ref) => (
                        <div key={ref.id}>
                          <span>{ref.source_type} · {ref.source_id}</span>
                          <p>{ref.excerpt || '已记录来源版本摘要'}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </Modal>
      </main>
    </div>
  )
}

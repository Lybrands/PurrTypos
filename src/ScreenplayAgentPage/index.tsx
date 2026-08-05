import { services } from '@/services'
import React from 'react'
import { createPortal } from 'react-dom'
import AppHeader from '../components/AppHeader'
import AgentConversation from '../components/AgentConversation'
import AgentComposer from '../components/AgentComposer'
import AgentConversationIndex from '../components/AgentConversationIndex'
import AgentTaskProgress from '../components/AgentTaskProgress'
import ContextUsageIndicator from '../Workspace/AiPanel/components/ContextUsageIndicator'
import Markdown from '../Workspace/AiPanel/components/Markdown'
import ModelPicker, {
  type ModelRuntimeConfigPatch,
} from '../Workspace/AiPanel/components/ModelPicker'
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ArrowUpIcon,
  BookIcon,
  PurrButton,
  PurrChoiceCard,
  CheckCircleIcon,
  ChevronDownIcon,
  DeleteIcon,
  PurrDropdown,
  EditIcon,
  MoreIcon,
  EyeIcon,
  ExportIcon,
  FileTextIcon,
  HistoryIcon,
  HomeIcon,
  InboxIcon,
  PurrInput,
  PurrInputNumber,
  PurrMultiSelect,
  LoadingIcon,
  MessageIcon,
  PurrModal,
  PlusIcon,
  RefreshIcon,
  RobotIcon,
  SaveIcon,
  SearchIcon,
  StopCircleIcon,
  PurrSegmented,
  PurrSelect,
  PurrSteps,
  type PurrStepItem,
  TeamIcon,
  PurrTooltip,
  UndoIcon,
  VideoCameraIcon,
} from '@/purr-components'
import { useAppFeedback } from '../hooks/useAppFeedback'
import type {
  AiAgentRunSnapshot,
  AiModelConfig,
  AiLongTask,
  AiSession,
  Book,
  Chapter,
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
import { getActiveTaskPlan } from '../Workspace/AiPanel/taskPlanSelection'
import {
  getChatRuntimeVersion,
  getChatSessionActivities,
  getChatSessionRuntime,
  replaceChatRuntimeMessages,
  setChatRuntimeActivity,
  setChatRuntimeLoading,
  setChatRuntimeStreamId,
  subscribeChatRuntime,
  updateChatRuntimeMessages,
} from '../Workspace/AiPanel/hooks/chatRuntimeStore'
import {
  buildDraftBatchActions,
  inferDraftSceneCount,
  inferDraftScope,
  type DraftBatchAction,
  type ScreenplayDraftScope,
} from './draftBatchIntent'
import {
  buildHistoryConverter,
  createCommitScheduler,
  dispatchChunk,
  getAgentConversationCapabilities,
  parseConversationsFromApi,
  type AccState,
  type AiStreamChunk,
  type ChatMessage,
  type ChunkCtx,
} from '../agent-runtime'
import { createAiStreamId } from '../utils/aiStream'
import { recordAiDebugRunContinuation } from '../components/AiDevInspector/store'
import { normalizeApiProvider } from '../modelCatalog'
import { buildScreenplayLineDiff } from './versionDiff'
import {
  buildSourceStructure,
  describePersistedSourceScope,
  resolveSourceScopeSelection,
} from './sourceScope'
import { selectScreenplayAgentSession } from './sessionRestore'
import {
  applyLongTaskProgress,
  buildLongTaskTaskPlan,
  createLongTaskConversationAdapter,
  extractLongTaskProposal,
  isHostDispatchReceipt,
  longTaskBelongsToSession,
  resetLongTaskAssistantMessage,
  resolveProposalSourceRunId,
} from './longTaskConversationAdapter'
import { findPersistedProposalDocument } from './proposalPersistence'
import './index.scss'

type EntryStage = 'source' | 'brief' | 'handoff' | 'project'
type EntrySourceView = 'choices' | 'books'
type SourceKind = ScreenplaySourceKind
type BriefStepKey = 'basics' | 'scope' | 'direction'
type SourceScopeFamily = 'whole' | 'custom'
type SourceScopePartialMethod = 'leading' | 'selected'
type SourceScopeUnit = 'chapters' | 'volumes'

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
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: ModelRuntimeConfigPatch) => void
  onOpenBookshelf: () => void
  onOpenSettings: () => void
  onBack: () => void
}

interface QueuedScreenplaySubmission {
  sessionId: number
  prompt: string
  taskIntent: 'chat' | 'stage_deliverable'
  modelId: string
  draftSceneCount: number
  draftScope: ScreenplayDraftScope
}

const FORMAT_OPTIONS: ScreenplayFormat[] = ['短片', '电影', '单集剧', '连续剧', '竖屏短剧']
const ADAPTATION_OPTIONS = ['忠实改编', '结构重组', '自由改编']
const ORIGINAL_OPTIONS = ['先找人物', '先建世界', '先推情节']
const STAGE_LABELS: Record<ScreenplayProject['active_stage'], string> = {
  orientation: '素材梳理',
  brief: '创作简报',
  structure: '结构设计',
  scenes: '场景规划',
  draft: '剧本正文',
  review: '审阅修订',
  completed: '创作完成',
}
const SCREENPLAY_STAGE_ORDER: ScreenplayProject['active_stage'][] = [
  'orientation',
  'brief',
  'structure',
  'scenes',
  'draft',
  'review',
  'completed',
]
const DOCUMENT_KIND_LABELS: Record<ScreenplayDocument['kind'], string> = {
  source_analysis: '原作范围分析',
  creative_brief: '创作简报',
  beat_sheet: '节拍表',
  episode_outline: '分集结构',
  scene_list: '场景表',
  scene_draft: '场景正文',
  review: '审阅报告',
}
type ScreenplayDocumentStage = Exclude<ScreenplayProject['active_stage'], 'completed'>
const DOCUMENT_STAGE_GROUPS: Array<{
  stage: ScreenplayDocumentStage
  kinds: ScreenplayDocument['kind'][]
}> = [
  { stage: 'orientation', kinds: ['source_analysis'] },
  { stage: 'brief', kinds: ['creative_brief'] },
  { stage: 'structure', kinds: ['beat_sheet', 'episode_outline'] },
  { stage: 'scenes', kinds: ['scene_list'] },
  { stage: 'draft', kinds: ['scene_draft'] },
  { stage: 'review', kinds: ['review'] },
]

function documentStageForKind(
  kind: ScreenplayDocument['kind'],
): ScreenplayDocumentStage | null {
  return DOCUMENT_STAGE_GROUPS.find((group) => group.kinds.includes(kind))?.stage ?? null
}
const SERIES_FORMATS = new Set<ScreenplayFormat>(['连续剧', '竖屏短剧'])
const BOOK_BRIEF_STEPS: PurrStepItem<BriefStepKey>[] = [
  { key: 'basics', title: '基础设置' },
  { key: 'scope', title: '原作范围' },
  { key: 'direction', title: '改编方向' },
]
const ORIGINAL_BRIEF_STEPS: PurrStepItem<BriefStepKey>[] = [
  { key: 'basics', title: '基础设置' },
  { key: 'direction', title: '故事方向' },
]
const LAST_OPENED_SCREENPLAY_PROJECT_STORAGE_KEY = 'purr-typos:last-opened-screenplay-project-id'
const SCREENPLAY_AGENT_MODEL_STORAGE_KEY = 'purr-typos:screenplay-agent-model-id'
const SCREENPLAY_ACTIVE_SESSION_STORAGE_PREFIX = 'purr-typos:screenplay-active-session:'

function getStoredLastOpenedScreenplayProjectId(): EntityId | null {
  try {
    return localStorage.getItem(LAST_OPENED_SCREENPLAY_PROJECT_STORAGE_KEY)
  } catch {
    return null
  }
}

function storeLastOpenedScreenplayProjectId(projectId: EntityId | null) {
  try {
    if (projectId == null) {
      localStorage.removeItem(LAST_OPENED_SCREENPLAY_PROJECT_STORAGE_KEY)
    } else {
      localStorage.setItem(LAST_OPENED_SCREENPLAY_PROJECT_STORAGE_KEY, projectId)
    }
  } catch {
    // 本地存储不可用时不影响剧本项目的打开与删除。
  }
}

function getStoredScreenplayAgentModelId(): string {
  try {
    return localStorage.getItem(SCREENPLAY_AGENT_MODEL_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function storeScreenplayAgentModelId(modelId: string) {
  try {
    localStorage.setItem(SCREENPLAY_AGENT_MODEL_STORAGE_KEY, modelId)
  } catch {
    // 本地存储不可用时仍允许本次会话切换模型。
  }
}

function getStoredScreenplayAgentSessionId(projectId: EntityId): number | null {
  try {
    const value = localStorage.getItem(`${SCREENPLAY_ACTIVE_SESSION_STORAGE_PREFIX}${projectId}`)
    if (!value) return null
    const sessionId = Number(value)
    return Number.isInteger(sessionId) && sessionId > 0 ? sessionId : null
  } catch {
    return null
  }
}

function storeScreenplayAgentSessionId(projectId: EntityId, sessionId: number | null) {
  try {
    const key = `${SCREENPLAY_ACTIVE_SESSION_STORAGE_PREFIX}${projectId}`
    if (sessionId == null) {
      localStorage.removeItem(key)
    } else {
      localStorage.setItem(key, String(sessionId))
    }
  } catch {
    // 本地存储不可用时仍允许本次打开与切换对话。
  }
}

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

function stageAgentStarter(
  project: ScreenplayProject,
  documents: ScreenplayDocument[] = [],
  draftSceneCount = 1,
  draftScope: ScreenplayDraftScope = 'planner',
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
    return '分析当前选定的原作范围，梳理主要人物、关键事件、核心冲突、可改编内容、连续性风险和仍需确认的问题；完成后生成可应用的正式原作范围分析提案。'
  }
  if (project.active_stage === 'brief' && project.source_kind === 'book') {
    return '基于已经确认的原作分析，形成一份可执行的改编方案，明确剧本规模、叙事终点和主要改编取舍；完成后生成可应用的正式创作简报提案。'
  }
  if (project.active_stage === 'structure') {
    return SERIES_FORMATS.has(project.format)
      ? '基于已经确认的创作方案，设计完整的分集结构，并生成可应用的正式分集结构提案。'
      : '基于已经确认的创作方案，设计完整的故事节拍，并生成可应用的正式节拍表提案。'
  }
  if (project.active_stage === 'scenes') {
    return SERIES_FORMATS.has(project.format)
      ? '把已经确认的分集结构拆成完整场景表，明确每场的目标、冲突和转折，并生成可应用的正式场景表提案。'
      : '把已经确认的故事节拍拆成完整场景表，明确每场的目标、冲突和转折，并生成可应用的正式场景表提案。'
  }
  if (project.active_stage === 'draft') {
    const suffix = '保持与当前已接受的场景表和正文版本连贯，并生成可应用的正文提案。'
    if (draftScope === 'all_remaining') return `继续创作全部剩余正文，${suffix}`
    if (draftScope === 'next_3_episodes') return `继续创作接下来 3 集，${suffix}`
    if (draftScope === 'next_5_episodes') return `继续创作接下来 5 集，${suffix}`
    if (draftScope === 'next_episode') return `继续创作下一集，${suffix}`
    if (draftScope === 'next_scene') return `继续创作下一场，${suffix}`
    if (draftScope === 'count' && draftSceneCount > 1) {
      return `继续创作接下来 ${draftSceneCount} 场，${suffix}`
    }
    return SERIES_FORMATS.has(project.format)
      ? `继续创作下一集，${suffix}`
      : `继续创作下一场，${suffix}`
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
      return `根据已经确认的审阅报告修订完整剧本，逐项解决其中 ${issues.length} 个问题，并生成可应用的完整修订稿。`
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
      return `复审当前修订稿，逐项核验上一轮提出的 ${previousIssues.length} 个问题是否真正解决，检查是否出现新的问题，并生成可应用的复审报告。`
    }
    return '审阅当前完整稿，重点检查连贯性、人物弧光、结构节奏、对白和剧本格式，并生成可应用的正式审阅报告。'
  }
  if (project.premise) {
    return '基于当前项目资料完善创作方案，处理可以安全推断的创作取舍，并生成可应用的正式创作简报提案。'
  }
  return '根据当前项目设定确定原创剧本的核心方向，并生成可应用的正式创作简报提案；只有缺少无法安全推断的关键决定时再向我确认。'
}

function screenplayDraftBatchScope(
  project: ScreenplayProject,
  documents: ScreenplayDocument[],
): {
  pendingSceneCount: number
  pendingEpisodeCount: number
  hasEpisodeNumbers: boolean
} {
  if (project.active_stage !== 'draft') {
    return {
      pendingSceneCount: 0,
      pendingEpisodeCount: 0,
      hasEpisodeNumbers: false,
    }
  }
  const sceneList = [...documents].reverse().find(
    (document) => document.kind === 'scene_list' && document.status === 'accepted',
  )
  const latestDraft = [...documents].reverse().find(
    (document) => document.kind === 'scene_draft' && document.status === 'accepted',
  )
  const scenes = Array.isArray(sceneList?.content_json?.scenes)
    ? sceneList.content_json.scenes.filter(
      (scene): scene is Record<string, unknown> => Boolean(scene && typeof scene === 'object'),
    )
    : []
  const completed = new Set(
    Array.isArray(latestDraft?.content_json?.completedSceneIds)
      ? latestDraft.content_json.completedSceneIds.map(String)
      : [],
  )
  const pending = scenes.filter((scene) => !completed.has(String(scene.id || '')))
  const hasEpisodeNumbers = pending.length > 0 && pending.every((scene) => (
    scene.episodeNumber != null && String(scene.episodeNumber).trim() !== ''
  ))
  return {
    pendingSceneCount: pending.length,
    pendingEpisodeCount: hasEpisodeNumbers
      ? new Set(pending.map((scene) => String(scene.episodeNumber))).size
      : 0,
    hasEpisodeNumbers,
  }
}

function stagePrimaryActionLabel(
  project: ScreenplayProject,
  documents: ScreenplayDocument[],
): string {
  if (project.active_stage === 'completed') return '创作已完成'
  if (project.active_stage === 'orientation') {
    return project.source_kind === 'book' ? '开始分析' : '生成创作简报'
  }
  if (project.active_stage === 'brief') return '生成创作简报'
  if (project.active_stage === 'structure') {
    return SERIES_FORMATS.has(project.format) ? '设计分集结构' : '设计故事节拍'
  }
  if (project.active_stage === 'scenes') return '生成场景表'
  if (project.active_stage === 'draft') {
    const sceneList = [...documents].reverse().find(
      (document) => document.kind === 'scene_list' && document.status === 'accepted',
    )
    const latestDraft = [...documents].reverse().find(
      (document) => document.kind === 'scene_draft' && document.status === 'accepted',
    )
    const sceneCount = Array.isArray(sceneList?.content_json?.scenes)
      ? sceneList.content_json.scenes.length
      : 0
    const completedCount = Array.isArray(latestDraft?.content_json?.completedSceneIds)
      ? latestDraft.content_json.completedSceneIds.length
      : 0
    if (sceneCount > 0 && completedCount >= sceneCount) return '完成剧本正文'
    return screenplayDraftBatchScope(project, documents).hasEpisodeNumbers
      ? '创作下一集'
      : '创作下一场'
  }
  const acceptedDraft = [...documents].reverse().find(
    (document) => document.kind === 'scene_draft' && document.status === 'accepted',
  )
  const acceptedReview = [...documents].reverse().find(
    (document) => document.kind === 'review' && document.status === 'accepted',
  )
  if (
    acceptedReview
    && String(acceptedReview.content_json?.reviewedDraftId || '')
      === String(acceptedDraft?.id || '')
    && acceptedReview.content_json?.verdict !== 'ready'
  ) {
    return '开始修订'
  }
  if (acceptedDraft?.content_json?.reviewId) return '开始复审'
  return '开始审阅'
}

function proposalAdvancesProjectStage(
  project: ScreenplayProject | null,
  proposal: ScreenplayDocumentProposal | null,
): boolean {
  if (!project || !proposal) return false
  if (project.active_stage === 'orientation') {
    return project.source_kind === 'book'
      ? proposal.kind === 'source_analysis'
      : proposal.kind === 'creative_brief'
  }
  if (project.active_stage === 'brief') return proposal.kind === 'creative_brief'
  if (project.active_stage === 'structure') {
    return proposal.kind === 'beat_sheet' || proposal.kind === 'episode_outline'
  }
  if (project.active_stage === 'scenes') return proposal.kind === 'scene_list'
  if (project.active_stage === 'draft') {
    return proposal.kind === 'scene_draft' && proposal.contentJson.isComplete === true
  }
  if (project.active_stage === 'review') {
    return proposal.kind === 'review' && proposal.contentJson.verdict === 'ready'
  }
  return false
}

function nextMilestone(project: ScreenplayProject): {
  title: string
  description: string
} {
  if (project.active_stage === 'completed') {
    return {
      title: '创作已完成',
      description: project.delivery_manifest
        ? '最终版本和交付清单已经就绪。'
        : '完整剧本已经通过审阅。',
    }
  }
  if (project.active_stage === 'orientation' && project.source_kind === 'book') {
    return {
      title: '分析原作范围',
      description: project.source_book_id
        ? '梳理人物、事件、冲突和可改编内容。'
        : '来源作品已移除，暂时无法继续分析。',
    }
  }
  if (project.active_stage === 'brief') {
    return {
      title: '完善创作简报',
      description: project.source_kind === 'book'
        ? '确定剧本规模、叙事终点和主要改编取舍。'
        : '确定故事方向和仍需确认的创作取舍。',
    }
  }
  if (project.active_stage === 'structure') {
    return {
      title: SERIES_FORMATS.has(project.format)
        ? '设计分集结构'
        : '设计故事节拍',
      description: SERIES_FORMATS.has(project.format)
        ? '安排各集的推进、转折和结尾。'
        : '安排故事的主要推进与转折。',
    }
  }
  if (project.active_stage === 'scenes') {
    return {
      title: '拆解场景表',
      description: '明确每场的目标、冲突和转折。',
    }
  }
  if (project.active_stage === 'draft') {
    return {
      title: '编写剧本正文',
      description: '逐场或批量继续创作，并保持前后连贯。',
    }
  }
  if (project.active_stage === 'review') {
    return {
      title: '审阅完整剧本',
      description: '检查故事、人物、节奏和剧本格式。',
    }
  }
  return {
    title: '完善创作简报',
    description: project.source_kind === 'book'
      ? '从原作资料中整理明确的改编方向。'
      : '确定故事方向和仍需确认的创作取舍。',
  }
}

export default function ScreenplayAgentPage({
  books,
  modelConfigs,
  onUpdateModelConfig,
  onOpenBookshelf,
  onOpenSettings,
  onBack,
}: ScreenplayAgentPageProps) {
  const { message } = useAppFeedback()
  const [stage, setStage] = React.useState<EntryStage>('source')
  const [entrySourceView, setEntrySourceView] = React.useState<EntrySourceView>('choices')
  const [briefStepIndex, setBriefStepIndex] = React.useState(0)
  const [furthestBriefStepIndex, setFurthestBriefStepIndex] = React.useState(0)
  const [sourceKind, setSourceKind] = React.useState<SourceKind>('book')
  const [selectedBookId, setSelectedBookId] = React.useState<EntityId | null>(null)
  const [projectTitle, setProjectTitle] = React.useState('')
  const [format, setFormat] = React.useState<ScreenplayFormat | null>(null)
  const [approach, setApproach] = React.useState('')
  const [premise, setPremise] = React.useState('')
  const [sourceScopeMode, setSourceScopeMode] = React.useState<ScreenplaySourceScopeMode>('whole_book')
  const [sourceScopeCount, setSourceScopeCount] = React.useState<number | null>(10)
  const [sourceScopePartialMethod, setSourceScopePartialMethod] = React.useState<SourceScopePartialMethod>('leading')
  const [sourceScopeUnit, setSourceScopeUnit] = React.useState<SourceScopeUnit>('chapters')
  const [selectedSourceChapterIds, setSelectedSourceChapterIds] = React.useState<EntityId[]>([])
  const [selectedSourceVolumeIds, setSelectedSourceVolumeIds] = React.useState<EntityId[]>([])
  const [sourceChapters, setSourceChapters] = React.useState<Chapter[]>([])
  const [sourceChaptersLoading, setSourceChaptersLoading] = React.useState(false)
  const [sourceChaptersError, setSourceChaptersError] = React.useState('')
  const [launchDraft, setLaunchDraft] = React.useState<ScreenplayLaunchDraft | null>(null)
  const [projects, setProjects] = React.useState<ScreenplayProject[]>([])
  const [projectsLoading, setProjectsLoading] = React.useState(true)
  const [projectSearch, setProjectSearch] = React.useState('')
  const [lastOpenedProjectId, setLastOpenedProjectId] = React.useState<EntityId | null>(
    getStoredLastOpenedScreenplayProjectId,
  )
  const [renameProjectTarget, setRenameProjectTarget] = React.useState<ScreenplayProject | null>(null)
  const [renameProjectTitle, setRenameProjectTitle] = React.useState('')
  const [deleteProjectTarget, setDeleteProjectTarget] = React.useState<ScreenplayProject | null>(null)
  const [projectMutationId, setProjectMutationId] = React.useState<EntityId | null>(null)
  const [creatingProject, setCreatingProject] = React.useState(false)
  const [openedProject, setOpenedProject] = React.useState<ScreenplayProject | null>(null)
  const [projectDocuments, setProjectDocuments] = React.useState<ScreenplayDocument[]>([])
  const [projectSourceRefs, setProjectSourceRefs] = React.useState<ScreenplaySourceRef[]>([])
  const [selectedDocumentStages, setSelectedDocumentStages] = React.useState<
    Record<string, ScreenplayDocumentStage>
  >({})
  const [documentLibraryOpen, setDocumentLibraryOpen] = React.useState(false)
  const [projectLoading, setProjectLoading] = React.useState(false)
  const [selectedModelId, setSelectedModelId] = React.useState(
    getStoredScreenplayAgentModelId,
  )
  const [agentPrompt, setAgentPrompt] = React.useState('')
  const [agentResponse, setAgentResponse] = React.useState('')
  const [agentRunId, setAgentRunId] = React.useState('')
  const [agentMessages, setAgentMessages] = React.useState<ChatMessage[]>([])
  const [agentResultHost, setAgentResultHost] = React.useState<HTMLDivElement | null>(null)
  const [agentProposal, setAgentProposal] = React.useState<ScreenplayDocumentProposal | null>(null)
  const [longTasks, setLongTasks] = React.useState<AiLongTask[]>([])
  const [longTaskConversationEpoch, setLongTaskConversationEpoch] = React.useState(0)
  const [agentRunning, setAgentRunning] = React.useState(false)
  const [agentQueuedSubmissions, setAgentQueuedSubmissions] = React.useState<
    QueuedScreenplaySubmission[]
  >([])
  const [agentSessionId, setAgentSessionId] = React.useState<number | null>(null)
  const [agentSessions, setAgentSessions] = React.useState<AiSession[]>([])
  const [agentSessionLoading, setAgentSessionLoading] = React.useState(false)
  const [agentConversationIndexOpen, setAgentConversationIndexOpen] = React.useState(true)
  const [editingAgentSessionId, setEditingAgentSessionId] = React.useState<number | null>(null)
  const [editingAgentSessionTitle, setEditingAgentSessionTitle] = React.useState('')
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
  const [documentEditing, setDocumentEditing] = React.useState(false)
  const [savingDocument, setSavingDocument] = React.useState(false)
  const [deletingDocumentId, setDeletingDocumentId] = React.useState<EntityId | null>(null)
  const [deleteDocumentTarget, setDeleteDocumentTarget] = React.useState<ScreenplayDocument | null>(null)
  const [restoringDocumentId, setRestoringDocumentId] = React.useState<EntityId | null>(null)
  const [updatingProjectStatus, setUpdatingProjectStatus] = React.useState(false)
  const persistedAgentDocument = React.useMemo(
    () => findPersistedProposalDocument(projectDocuments, agentProposal),
    [agentProposal, projectDocuments],
  )
  const savedAgentDocumentId = persistedAgentDocument?.id ?? null
  const acceptedAgentDocumentId = persistedAgentDocument?.status === 'accepted'
    ? persistedAgentDocument.id
    : null
  const agentStreamIdRef = React.useRef<string | null>(null)
  const agentUnsubscribeRef = React.useRef<(() => void) | null>(null)
  const agentRecoveryTokenRef = React.useRef(0)
  const agentStopPendingSessionIdsRef = React.useRef(new Set<number>())
  const agentQueuedSubmissionsRef = React.useRef<QueuedScreenplaySubmission[]>([])
  const activeAgentSessionRef = React.useRef<number | null>(null)
  const agentRuntimeSubscriptionsRef = React.useRef(new Map<
    number,
    { streamId: string; unsubscribe: () => void }
  >())
  const agentRuntimeVersion = React.useSyncExternalStore(
    subscribeChatRuntime,
    getChatRuntimeVersion,
    getChatRuntimeVersion,
  )

  React.useEffect(() => {
    agentQueuedSubmissionsRef.current = agentQueuedSubmissions
  }, [agentQueuedSubmissions])

  React.useEffect(() => {
    activeAgentSessionRef.current = agentSessionId
  }, [agentSessionId])

  React.useEffect(() => {
    const runtime = getChatSessionRuntime(agentSessionId)
    if (!runtime) return
    setAgentMessages(runtime.messages)
    setAgentRunning(runtime.loading)
    const latestAssistant = [...runtime.messages].reverse().find(
      (item) => item.role === 'assistant',
    )
    setAgentResponse(latestAssistant?.content || '')
    setAgentProposal(latestAssistant?.screenplayProposal ?? null)
    setAgentRunId(latestAssistant?.agentRunId || '')
  }, [agentRuntimeVersion, agentSessionId])

  React.useEffect(() => {
    if (modelConfigs.length === 0) return
    if (
      selectedModelId
      && modelConfigs.some((config) => config.id === selectedModelId)
    ) {
      return
    }
    const storedModelId = getStoredScreenplayAgentModelId()
    setSelectedModelId(
      modelConfigs.some((config) => config.id === storedModelId)
        ? storedModelId
        : modelConfigs[0]?.id ?? '',
    )
  }, [modelConfigs, selectedModelId])

  React.useEffect(() => {
    if (
      selectedModelId
      && modelConfigs.some((config) => config.id === selectedModelId)
    ) {
      storeScreenplayAgentModelId(selectedModelId)
    }
  }, [modelConfigs, selectedModelId])

  React.useEffect(() => () => {
    agentRecoveryTokenRef.current += 1
    agentRuntimeSubscriptionsRef.current.forEach(({ streamId, unsubscribe }) => {
      unsubscribe()
      services.ai.abortAiStream(streamId)
    })
    agentRuntimeSubscriptionsRef.current.clear()
    agentStopPendingSessionIdsRef.current.clear()
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

  React.useEffect(() => {
    if (
      projectsLoading
      || lastOpenedProjectId == null
      || projects.some((project) => project.id === lastOpenedProjectId)
    ) {
      return
    }
    setLastOpenedProjectId(null)
    storeLastOpenedScreenplayProjectId(null)
  }, [lastOpenedProjectId, projects, projectsLoading])

  const applyProjectUpdate = React.useCallback((updatedProject: ScreenplayProject) => {
    setProjects((current) => [
      updatedProject,
      ...current.filter((project) => project.id !== updatedProject.id),
    ])
    setOpenedProject((current) => (
      current?.id === updatedProject.id ? updatedProject : current
    ))
  }, [])

  const openRenameProject = React.useCallback((project: ScreenplayProject) => {
    setRenameProjectTarget(project)
    setRenameProjectTitle(project.title)
  }, [])

  const renameProject = React.useCallback(async () => {
    if (!renameProjectTarget || projectMutationId != null) return
    const title = renameProjectTitle.trim()
    if (!title) {
      message.warning('项目名称不能为空')
      return
    }
    if (title === renameProjectTarget.title) {
      setRenameProjectTarget(null)
      setRenameProjectTitle('')
      return
    }
    setProjectMutationId(renameProjectTarget.id)
    try {
      const result = await services.screenplay.updateScreenplayProject({
        projectId: renameProjectTarget.id,
        patch: { title },
      })
      if (!result.success || !result.data) {
        message.error(result.error || '重命名项目失败')
        return
      }
      applyProjectUpdate(result.data)
      setRenameProjectTarget(null)
      setRenameProjectTitle('')
      message.success('项目名称已更新')
    } finally {
      setProjectMutationId(null)
    }
  }, [
    applyProjectUpdate,
    message,
    projectMutationId,
    renameProjectTarget,
    renameProjectTitle,
  ])

  const toggleListedProjectArchived = React.useCallback(async (
    project: ScreenplayProject,
  ) => {
    if (projectMutationId != null) return
    const nextStatus = project.status === 'archived' ? 'active' : 'archived'
    setProjectMutationId(project.id)
    try {
      const result = await services.screenplay.updateScreenplayProject({
        projectId: project.id,
        patch: { status: nextStatus },
      })
      if (!result.success || !result.data) {
        message.error(result.error || '更新项目状态失败')
        return
      }
      applyProjectUpdate(result.data)
      message.success(nextStatus === 'archived' ? '项目已归档' : '项目已恢复')
    } finally {
      setProjectMutationId(null)
    }
  }, [applyProjectUpdate, message, projectMutationId])

  const deleteProject = React.useCallback(async () => {
    if (!deleteProjectTarget || projectMutationId != null) return
    const projectId = deleteProjectTarget.id
    setProjectMutationId(projectId)
    try {
      const result = await services.screenplay.deleteScreenplayProject({
        projectId,
      })
      if (!result.success) {
        message.error(result.error || '删除项目失败')
        return
      }
      setProjects((current) => current.filter((project) => project.id !== projectId))
      storeScreenplayAgentSessionId(projectId, null)
      if (projectId === lastOpenedProjectId) {
        setLastOpenedProjectId(null)
        storeLastOpenedScreenplayProjectId(null)
      }
      if (openedProject?.id === projectId) {
        if (agentStreamIdRef.current) {
          services.ai.abortAiStream(agentStreamIdRef.current)
        }
        agentUnsubscribeRef.current?.()
        agentUnsubscribeRef.current = null
        agentStreamIdRef.current = null
        setOpenedProject(null)
        setProjectDocuments([])
        setProjectSourceRefs([])
        setAgentSessions([])
        setAgentSessionId(null)
        setAgentMessages([])
        setAgentPrompt('')
        setAgentResponse('')
        setAgentProposal(null)
        setAgentRunning(false)
        setStage('source')
      }
      setDeleteProjectTarget(null)
      message.success('剧本项目已删除')
    } finally {
      setProjectMutationId(null)
    }
  }, [
    deleteProjectTarget,
    lastOpenedProjectId,
    message,
    openedProject?.id,
    projectMutationId,
  ])

  const orderedProjects = React.useMemo(() => {
    if (lastOpenedProjectId == null) return projects
    return [...projects].sort((left, right) => {
      if (left.id === lastOpenedProjectId) return -1
      if (right.id === lastOpenedProjectId) return 1
      return 0
    })
  }, [lastOpenedProjectId, projects])

  const filteredProjects = React.useMemo(() => {
    const query = projectSearch.trim().toLocaleLowerCase()
    if (!query) return orderedProjects
    return orderedProjects.filter((project) => {
      const sourceBook = books.find((book) => book.id === project.source_book_id)
      const searchableText = [
        project.title,
        project.format,
        project.approach,
        STAGE_LABELS[project.active_stage],
        project.source_kind === 'original' ? '原创故事' : '小说改编',
        project.status === 'archived' ? '已归档' : '进行中',
        sourceBook?.title,
      ].filter(Boolean).join(' ').toLocaleLowerCase()
      return searchableText.includes(query)
    })
  }, [books, orderedProjects, projectSearch])

  const recentAdaptationBookIds = React.useMemo(() => {
    const knownBookIds = new Set(books.map((book) => book.id))
    const seen = new Set<EntityId>()
    const orderedBookIds: EntityId[] = []
    orderedProjects.forEach((project) => {
      const bookId = project.source_kind === 'book' ? project.source_book_id : null
      if (bookId && knownBookIds.has(bookId) && !seen.has(bookId)) {
        seen.add(bookId)
        orderedBookIds.push(bookId)
      }
    })
    return orderedBookIds
  }, [books, orderedProjects])

  const recentAdaptationBookId = recentAdaptationBookIds[0] ?? null
  const sortedBooks = React.useMemo(() => {
    if (recentAdaptationBookIds.length === 0) return books
    const adaptationOrder = new Map(
      recentAdaptationBookIds.map((bookId, index) => [bookId, index]),
    )
    return [...books].sort((left, right) => {
      const leftIndex = adaptationOrder.get(left.id) ?? Number.MAX_SAFE_INTEGER
      const rightIndex = adaptationOrder.get(right.id) ?? Number.MAX_SAFE_INTEGER
      return leftIndex - rightIndex
    })
  }, [books, recentAdaptationBookIds])

  const selectedBook = React.useMemo(
    () => books.find((book) => book.id === selectedBookId) ?? null,
    [books, selectedBookId],
  )
  const briefSteps = sourceKind === 'book'
    ? BOOK_BRIEF_STEPS
    : ORIGINAL_BRIEF_STEPS
  const currentBriefStep = briefSteps[
    Math.min(briefStepIndex, briefSteps.length - 1)
  ]

  const sourceStructure = React.useMemo(
    () => buildSourceStructure(sourceChapters),
    [sourceChapters],
  )

  const sourceScopeSelection = React.useMemo(
    () => resolveSourceScopeSelection({
      structure: sourceStructure,
      mode: sourceScopeMode,
      count: sourceScopeCount ?? 0,
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
  const briefSummaryRows = [
    {
      key: 'project',
      label: '项目名称',
      value: projectTitle.trim(),
    },
    ...(sourceKind === 'book' ? [{
      key: 'scope',
      label: '改编范围',
      value: sourceChaptersLoading
        ? '正在读取目录…'
        : sourceScopeSelection.summary,
    }] : []),
    {
      key: 'format',
      label: '剧本形态',
      value: format ?? '',
    },
    {
      key: 'approach',
      label: sourceKind === 'book' ? '改编方式' : '探索起点',
      value: approach,
    },
    {
      key: 'premise',
      label: sourceKind === 'book' ? '改编想法' : '故事想法',
      value: premise.trim() ? '已填写' : '',
    },
  ]
  const sourceScopeFamily: SourceScopeFamily = sourceScopeMode === 'whole_book'
    ? 'whole'
    : 'custom'
  const sourceScopeOptions = React.useMemo(() => {
    if (sourceScopeUnit === 'volumes') {
      return sourceStructure.volumes.map((volume) => ({
        value: volume.id,
        label: `${volume.title} · ${volume.chapterIds.length} 章`,
        searchText: volume.title,
      }))
    }
    return sourceStructure.chapters.map((chapter) => ({
      value: chapter.id,
      label: `${chapter.index}. ${chapter.title}${
        chapter.volumeTitle ? ` · ${chapter.volumeTitle}` : ''
      }`,
      searchText: [
        String(chapter.index),
        chapter.title,
        chapter.volumeTitle,
      ].filter(Boolean).join(' '),
    }))
  }, [sourceScopeUnit, sourceStructure])
  const selectedSourceScopeIds = sourceScopeUnit === 'volumes'
    ? selectedSourceVolumeIds
    : selectedSourceChapterIds

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

  const chooseSourceScopeFamily = React.useCallback((family: SourceScopeFamily) => {
    if (family === 'whole') {
      setSourceScopeMode('whole_book')
      return
    }
    setSourceScopeMode(sourceScopePartialMethod === 'leading'
      ? (sourceScopeUnit === 'volumes' ? 'first_volumes' : 'first_chapters')
      : (sourceScopeUnit === 'volumes' ? 'selected_volumes' : 'selected_chapters'))
  }, [sourceScopePartialMethod, sourceScopeUnit])

  const chooseSourceScopePartialMethod = React.useCallback((
    method: SourceScopePartialMethod,
  ) => {
    setSourceScopePartialMethod(method)
    setSourceScopeMode(method === 'leading'
      ? (sourceScopeUnit === 'volumes' ? 'first_volumes' : 'first_chapters')
      : (sourceScopeUnit === 'volumes' ? 'selected_volumes' : 'selected_chapters'))
  }, [sourceScopeUnit])

  const chooseSourceScopeUnit = React.useCallback((unit: SourceScopeUnit) => {
    setSourceScopeUnit(unit)
    setSourceScopeMode(sourceScopePartialMethod === 'leading'
      ? (unit === 'volumes' ? 'first_volumes' : 'first_chapters')
      : (unit === 'volumes' ? 'selected_volumes' : 'selected_chapters'))
    setSourceScopeCount(unit === 'volumes' ? 1 : Math.min(
      10,
      Math.max(1, sourceStructure.chapters.length),
    ))
  }, [sourceScopePartialMethod, sourceStructure.chapters.length])

  const changeSelectedSourceScopeIds = React.useCallback((ids: EntityId[]) => {
    if (sourceScopeUnit === 'volumes') {
      setSelectedSourceVolumeIds(ids)
      setSourceScopeMode('selected_volumes')
    } else {
      setSelectedSourceChapterIds(ids)
      setSourceScopeMode('selected_chapters')
    }
  }, [sourceScopeUnit])

  const startFromBook = React.useCallback((book: Book) => {
    setBriefStepIndex(0)
    setFurthestBriefStepIndex(0)
    setSourceKind('book')
    setSelectedBookId(book.id)
    setProjectTitle(`《${book.title}》剧本改编`)
    setFormat(null)
    setApproach('')
    setPremise('')
    setSourceScopeMode('whole_book')
    setSourceScopeCount(10)
    setSourceScopePartialMethod('leading')
    setSourceScopeUnit('chapters')
    setSelectedSourceChapterIds([])
    setSelectedSourceVolumeIds([])
    setStage('brief')
  }, [])

  const startOriginal = React.useCallback(() => {
    setBriefStepIndex(0)
    setFurthestBriefStepIndex(0)
    setSourceKind('original')
    setSelectedBookId(null)
    setProjectTitle('未命名原创剧本')
    setFormat(null)
    setApproach('')
    setPremise('')
    setSourceScopeMode('whole_book')
    setSourceScopePartialMethod('leading')
    setSourceScopeUnit('chapters')
    setSelectedSourceChapterIds([])
    setSelectedSourceVolumeIds([])
    setStage('brief')
  }, [])

  const buildHandoff = React.useCallback(() => {
    if (!format || !approach) {
      message.warning('请先完成剧本形态和创作方向设置')
      return
    }
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

  const selectBriefStep = React.useCallback((index: number) => {
    if (index > furthestBriefStepIndex) return
    setBriefStepIndex(index)
  }, [furthestBriefStepIndex])

  const continueBrief = React.useCallback(() => {
    if (currentBriefStep.key === 'basics') {
      if (!projectTitle.trim()) {
        message.warning('请先填写项目名称')
        return
      }
      if (!format) {
        message.warning('请选择剧本形态')
        return
      }
    }
    if (currentBriefStep.key === 'scope') {
      if (sourceChaptersLoading) {
        message.warning('正在读取来源作品章节，请稍候')
        return
      }
      if (sourceChaptersError) {
        message.warning(sourceChaptersError)
        return
      }
      if (sourceScopeSelection.error) {
        message.warning(sourceScopeSelection.error)
        return
      }
    }
    if (currentBriefStep.key === 'direction' && !approach) {
      message.warning(sourceKind === 'book' ? '请选择改编方式' : '请选择探索起点')
      return
    }
    const nextIndex = briefStepIndex + 1
    if (nextIndex >= briefSteps.length) {
      buildHandoff()
      return
    }
    setBriefStepIndex(nextIndex)
    setFurthestBriefStepIndex((current) => Math.max(current, nextIndex))
  }, [
    briefStepIndex,
    briefSteps.length,
    buildHandoff,
    currentBriefStep.key,
    format,
    message,
    approach,
    projectTitle,
    sourceChaptersError,
    sourceChaptersLoading,
    sourceScopeSelection.error,
    sourceKind,
  ])

  const returnToPreviousBriefStep = React.useCallback(() => {
    setBriefStepIndex((current) => Math.max(0, current - 1))
  }, [])

  const resetToSource = React.useCallback(() => {
    setBriefStepIndex(0)
    setFurthestBriefStepIndex(0)
    setLaunchDraft(null)
    setOpenedProject(null)
    setProjectDocuments([])
    setProjectSourceRefs([])
    setAgentProposal(null)
    setAgentSessionId(null)
    setAgentSessions([])
    setAgentMessages([])
    setAgentPrompt('')
    setAgentResponse('')
    setSelectedDocument(null)
    setComparisonDocument(null)
    setSourceChapters([])
    setSourceChaptersError('')
    setEntrySourceView('choices')
    setStage('source')
  }, [])

  const handleHeaderBack = React.useCallback(() => {
    if (stage === 'source') {
      onBack()
      return
    }
    if (stage === 'handoff') {
      setStage('brief')
      return
    }
    resetToSource()
  }, [onBack, resetToSource, stage])

  const headerBackLabel = stage === 'source'
    ? '返回首页'
    : stage === 'brief'
      ? '重新选择起点'
      : stage === 'handoff'
        ? '返回修改简报'
        : '返回剧本项目列表'

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

  const loadProjectLongTasks = React.useCallback(async (projectId: EntityId) => {
    const result = await services.ai.listScreenplayLongTasks({ projectId })
    if (!result.success || !Array.isArray(result.data)) return null
    setLongTasks(result.data)
    return result.data
  }, [])

  const monitorDetachedAgentRun = React.useCallback(async (
    runId: string,
    sessionId: number,
    project: ScreenplayProject,
    documents: ScreenplayDocument[],
    prompt: string,
  ) => {
    const recoveryModel = modelConfigs.find(
      (item) => item.id === selectedModelId,
    ) ?? modelConfigs[0]
    if (!recoveryModel) {
      setAgentRunning(false)
      message.error('恢复 Agent 任务失败：当前没有可用模型配置')
      return
    }
    const recoveryToken = agentRecoveryTokenRef.current + 1
    agentRecoveryTokenRef.current = recoveryToken
    let terminalWithoutConversationPolls = 0
    let afterCursor = 0
    const existingAssistant = [
      ...(getChatSessionRuntime(sessionId)?.messages ?? []),
    ].reverse().find((item) => item.role === 'assistant')
    const turnStartedAt = existingAssistant?.turnStartedAt ?? performance.now()
    const acc: AccState = {
      response: existingAssistant?.content || '',
      thinking: '',
      bookId: project.source_book_id,
      sessionId,
      chapterId: null,
      needsTitle: false,
      userText: prompt,
      model: existingAssistant?.model || recoveryModel.name,
      turnStartedAt,
      toolCallSegments: undefined,
      thinkingBlocks: existingAssistant?.thinkingBlocks ?? [],
      thinkingDurationsMs: existingAssistant?.thinkingDurationsMs ?? [],
      contentAfterToolCalls: '',
      agentRunId: runId,
      taskPlan: undefined,
      delegations: undefined,
      contextCompaction: undefined,
      contextBudget: undefined,
      longTaskId: undefined,
    }
    updateChatRuntimeMessages(sessionId, (current) => {
      const next = [...current]
      let assistantIndex = next.findIndex(
        (item) => item.role === 'assistant' && item.agentRunId === runId,
      )
      if (
        assistantIndex < 0
        && next.at(-1)?.role === 'assistant'
        && next.at(-2)?.role === 'user'
        && next.at(-2)?.content === prompt
      ) {
        assistantIndex = next.length - 1
      }
      if (assistantIndex < 0) {
        return [
          ...next,
          { role: 'user', content: prompt },
          {
            role: 'assistant',
            content: '',
            model: recoveryModel.name,
            agentRunId: runId,
            turnStartedAt,
          },
        ]
      }
      next[assistantIndex] = {
        ...next[assistantIndex],
        agentRunId: runId,
        taskPlan: undefined,
        delegations: undefined,
        subAgentActivities: undefined,
        toolApprovals: undefined,
        toolCallSegments: undefined,
        contentAfterToolCalls: undefined,
        toolCalling: false,
      }
      return next
    })
    const runtimeSetConversations: React.Dispatch<
      React.SetStateAction<ChatMessage[]>
    > = (next) => {
      if (typeof next === 'function') {
        updateChatRuntimeMessages(sessionId, next)
      } else {
        replaceChatRuntimeMessages(sessionId, next)
      }
    }
    const { scheduleCommit, flushCommits } = createCommitScheduler(
      runtimeSetConversations,
    )
    const ctx: ChunkCtx = {
      acc,
      sessionId,
      cfg: recoveryModel,
      apiModelName: recoveryModel.name,
      writingChapters: sourceChapters.map((chapter) => ({
        id: chapter.id,
        title: chapter.title,
      })),
      availableOutlines: [],
      setConversations: runtimeSetConversations,
      scheduleCommit,
      flushCommits,
      setLoading: (next) => setChatRuntimeLoading(sessionId, next),
      setSessions: setAgentSessions,
      setScreenplayProposal: (proposal) => {
        if (activeAgentSessionRef.current === sessionId) {
          setAgentProposal(proposal)
        }
      },
      appMessage: message,
      isVisibleSession: () => true,
      persistConversation: true,
      cleanup: () => undefined,
    }
    setChatRuntimeLoading(sessionId, true)
    setChatRuntimeActivity(sessionId, { state: 'running', queuedCount: 0 })

    while (agentRecoveryTokenRef.current === recoveryToken) {
      const snapshotResult = await services.ai.getAgentRunSnapshot({
        runId,
        after: afterCursor,
        limit: 500,
      })
      if (!snapshotResult.success || !snapshotResult.data) {
        setAgentRunning(false)
        message.error(snapshotResult.error || '恢复 Agent 任务失败')
        return
      }
      const snapshot: AiAgentRunSnapshot = snapshotResult.data
      snapshot.events.forEach((event) => {
        if (!event.chunk) return
        dispatchChunk(event.chunk as AiStreamChunk, ctx)
      })
      flushCommits()
      afterCursor = snapshot.nextCursor
      setChatRuntimeLoading(sessionId, snapshot.run.status === 'running')
      setChatRuntimeActivity(sessionId, {
        state: snapshot.run.status === 'running'
          ? 'running'
          : snapshot.run.status === 'done'
            ? 'completed'
            : snapshot.run.status === 'canceled'
              ? 'canceled'
              : 'failed',
        queuedCount: agentQueuedSubmissionsRef.current.filter(
          (submission) => submission.sessionId === sessionId,
        ).length,
      })
      if (snapshot.run.status === 'running') {
        if (snapshot.hasMore) continue
        await new Promise((resolve) => window.setTimeout(resolve, 800))
        continue
      }
      if (
        snapshot.run.conversationId == null
        && terminalWithoutConversationPolls < 8
      ) {
        terminalWithoutConversationPolls += 1
        await new Promise((resolve) => window.setTimeout(resolve, 150))
        continue
      }

      const historyResult = await services.conversations.getConversations({ sessionId })
      if (
        agentRecoveryTokenRef.current !== recoveryToken
        || !historyResult.success
        || !Array.isArray(historyResult.data)
      ) {
        if (agentRecoveryTokenRef.current === recoveryToken) {
          setAgentRunning(false)
        }
        return
      }
      const restoredMessages = parseConversationsFromApi(historyResult.data)
      const projectedAssistant = [
        ...(getChatSessionRuntime(sessionId)?.messages ?? []),
      ].reverse().find(
        (item) => item.role === 'assistant' && item.agentRunId === runId,
      )
      let restoredAssistantIndex = -1
      for (let index = restoredMessages.length - 1; index >= 0; index -= 1) {
        if (restoredMessages[index].role !== 'assistant') continue
        restoredAssistantIndex = index
        break
      }
      if (projectedAssistant && restoredAssistantIndex >= 0) {
        restoredMessages[restoredAssistantIndex] = {
          ...restoredMessages[restoredAssistantIndex],
          agentRunId: runId,
          taskPlan: projectedAssistant.taskPlan,
          delegations: projectedAssistant.delegations,
          subAgentActivities: projectedAssistant.subAgentActivities,
          toolApprovals: projectedAssistant.toolApprovals,
        }
      }
      const latestAssistantMessage = [...restoredMessages].reverse().find(
        (item) => item.role === 'assistant',
      )
      const restoredProposal = latestAssistantMessage?.screenplayProposal ?? null
      replaceChatRuntimeMessages(sessionId, restoredMessages)
      setChatRuntimeLoading(sessionId, false)
      if (activeAgentSessionRef.current === sessionId) {
        setAgentResponse(latestAssistantMessage?.content || snapshot.run.finalResponse || '')
        setAgentProposal(restoredProposal)
        setAgentPrompt(restoredMessages.length > 0 ? '' : stageAgentStarter(project, documents))
        setAgentRunning(false)
        agentStreamIdRef.current = null
        agentUnsubscribeRef.current = null
      }
      void loadProjectSourceRefs(project.id)
      return
    }
  }, [
    loadProjectSourceRefs,
    message,
    modelConfigs,
    selectedModelId,
    sourceChapters,
  ])

  const loadAgentSession = React.useCallback(async (
    sessionId: number,
    project: ScreenplayProject,
    documents: ScreenplayDocument[],
  ) => {
    setAgentSessionLoading(true)
    activeAgentSessionRef.current = sessionId
    setAgentSessionId(sessionId)
    storeScreenplayAgentSessionId(project.id, sessionId)
    setAgentMessages([])
    setAgentResponse('')
    setAgentRunId('')
    setAgentProposal(null)
    try {
      const activeRuntime = getChatSessionRuntime(sessionId)
      if (activeRuntime) {
        const latestAssistantMessage = [...activeRuntime.messages].reverse().find(
          (item) => item.role === 'assistant',
        )
        setAgentMessages(activeRuntime.messages)
        setAgentRunning(activeRuntime.loading)
        setAgentResponse(latestAssistantMessage?.content || '')
        const runtimeProposal = latestAssistantMessage?.screenplayProposal ?? null
        setAgentProposal(runtimeProposal)
        setAgentRunId(latestAssistantMessage?.agentRunId || '')
        setAgentPrompt(activeRuntime.messages.length > 0
          ? ''
          : stageAgentStarter(project, documents))
        if (!activeRuntime.loading || activeRuntime.streamId) return
      }
      if (!activeRuntime) replaceChatRuntimeMessages(sessionId, [])
      const historyResult = await services.conversations.getConversations({ sessionId })
      if (!historyResult.success || !Array.isArray(historyResult.data)) {
        message.error(historyResult.error || '读取剧本 Agent 对话失败')
        return
      }
      const restoredMessages = parseConversationsFromApi(historyResult.data)
      const latestAssistantMessage = [...restoredMessages].reverse().find(
        (item) => item.role === 'assistant',
      )
      const restoredProposal = latestAssistantMessage?.screenplayProposal ?? null
      replaceChatRuntimeMessages(sessionId, restoredMessages)
      setChatRuntimeLoading(sessionId, false)
      if (restoredMessages.length > 0) {
        setChatRuntimeActivity(sessionId, {
          state: 'completed',
          queuedCount: 0,
        })
      }
      setAgentMessages(restoredMessages)
      setAgentPrompt(restoredMessages.length > 0 ? '' : stageAgentStarter(project, documents))
      setAgentResponse(latestAssistantMessage?.content || '')
      setAgentProposal(restoredProposal)
      const latestRunResult = await services.ai.getLatestSessionAgentRun({ sessionId })
      const latestRun = latestRunResult.success ? latestRunResult.data : null
      const needsAgentProcessHydration = Boolean(
        latestAssistantMessage?.longTaskId
        && !latestAssistantMessage.subAgentActivities?.length,
      )
      if (
        latestRun
        && (
          latestRun.snapshot.run.status === 'running'
          || (
            latestRun.snapshot.run.status === 'done'
            && (
              latestRun.snapshot.run.conversationId == null
              || needsAgentProcessHydration
            )
          )
        )
      ) {
        void monitorDetachedAgentRun(
          latestRun.snapshot.run.runId,
          sessionId,
          project,
          documents,
          latestRun.prompt,
        )
      }
    } finally {
      setAgentSessionLoading(false)
    }
  }, [message, monitorDetachedAgentRun])

  const openProject = React.useCallback(async (project: ScreenplayProject) => {
    agentRecoveryTokenRef.current += 1
    if (agentStreamIdRef.current) {
      services.ai.abortAiStream(agentStreamIdRef.current)
    }
    agentUnsubscribeRef.current?.()
    agentUnsubscribeRef.current = null
    agentStreamIdRef.current = null
    setLastOpenedProjectId(project.id)
    storeLastOpenedScreenplayProjectId(project.id)
    setOpenedProject(project)
    setProjectDocuments([])
    setProjectSourceRefs([])
    setLongTasks([])
    setAgentSessionId(null)
    setAgentSessions([])
    setAgentMessages([])
    setAgentPrompt('')
    setAgentResponse('')
    setAgentRunId('')
    setAgentProposal(null)
    setAgentQueuedSubmissions([])
    setAgentRunning(false)
    setProjectLoading(true)
    setStage('project')
    try {
      const [documents, , , sessionResult] = await Promise.all([
        loadProjectDocuments(project.id),
        loadProjectSourceRefs(project.id),
        loadProjectLongTasks(project.id),
        services.screenplay.getOrCreateScreenplaySession({
          projectId: project.id,
        }),
      ])
      if (sessionResult.success && sessionResult.data) {
        const sessionsResult = await services.screenplay.listScreenplaySessions({
          projectId: project.id,
        })
        const sessions = sessionsResult.success && Array.isArray(sessionsResult.data)
          ? sessionsResult.data
          : [sessionResult.data]
        const targetSession = selectScreenplayAgentSession(
          sessions,
          getStoredScreenplayAgentSessionId(project.id),
          sessionResult.data.id,
        )
        setAgentSessions(sessions)
        if (targetSession) {
          await loadAgentSession(targetSession.id, project, documents || [])
        }
      } else {
        message.error(sessionResult.error || '初始化剧本 Agent 会话失败')
      }
      if (documents && !sessionResult.success) {
        setAgentPrompt(stageAgentStarter(project, documents))
      }
    } finally {
      setProjectLoading(false)
    }
  }, [
    loadAgentSession,
    loadProjectDocuments,
    loadProjectLongTasks,
    loadProjectSourceRefs,
    message,
  ])

  React.useEffect(() => {
    if (longTasks.length === 0) return
    const tasksById = new Map(longTasks.map((task) => [task.id, task]))
    agentSessions.forEach((session) => {
      const runtime = getChatSessionRuntime(session.id)
      const task = [...(runtime?.messages ?? [])].reverse()
        .map((item) => item.longTaskId ? tasksById.get(item.longTaskId) : undefined)
        .find((item): item is AiLongTask => item != null)
      if (!task) return
      const state = task.status === 'pending' || task.status === 'running'
        ? 'running'
        : task.status === 'paused'
          ? 'paused'
          : task.status === 'completed'
            ? 'completed'
            : task.status === 'failed'
              ? 'failed'
              : 'canceled'
      setChatRuntimeActivity(session.id, { state, queuedCount: 0 })
    })
  }, [agentRuntimeVersion, agentSessions, longTasks])

  const conversationLongTaskAttachment = React.useMemo(() => (
    [...agentMessages].reverse().find((item) => (
      item.role === 'assistant' && item.longTaskId
    )) ?? null
  ), [agentMessages])
  const conversationLongTaskId = conversationLongTaskAttachment?.longTaskId ?? null
  const conversationLongTaskStreamKey = conversationLongTaskAttachment
    ? `${conversationLongTaskId}:${
        conversationLongTaskAttachment.conversationId
        || agentMessages.length
      }`
    : null
  const conversationLongTask = React.useMemo(() => (
    conversationLongTaskId == null
      ? null
      : longTasks.find((task) => (
          task.id === conversationLongTaskId
          && longTaskBelongsToSession(task, agentSessionId)
        )) ?? null
  ), [agentSessionId, conversationLongTaskId, longTasks])

  const presentLongTaskProposal = React.useCallback((
    taskId: string,
    proposal: ScreenplayDocumentProposal,
  ) => {
    if (agentSessionId == null) return
    updateChatRuntimeMessages(agentSessionId, (current) => current.map((item) => (
      item.role === 'assistant' && item.longTaskId === taskId
        ? { ...item, screenplayProposal: proposal }
        : item
    )))
    setAgentProposal(proposal)
  }, [agentSessionId])

  React.useEffect(() => {
    if (
      !conversationLongTask
      || conversationLongTask.status !== 'completed'
      || conversationLongTaskAttachment?.screenplayProposal
    ) {
      return undefined
    }
    let disposed = false
    void services.ai.getLongTask({ taskId: conversationLongTask.id }).then((result) => {
      if (disposed || !result.success || !result.data) return
      const proposal = extractLongTaskProposal(result.data)
      if (proposal) presentLongTaskProposal(conversationLongTask.id, proposal)
    })
    return () => {
      disposed = true
    }
  }, [
    conversationLongTask,
    conversationLongTaskAttachment?.screenplayProposal,
    presentLongTaskProposal,
  ])

  React.useEffect(() => {
    if (!conversationLongTask || agentSessionId == null) return
    const taskPlan = buildLongTaskTaskPlan(conversationLongTask)
    updateChatRuntimeMessages(agentSessionId, (current) => {
      let changed = false
      const next = current.map((item) => {
        if (item.role !== 'assistant' || item.longTaskId !== conversationLongTask.id) {
          return item
        }
        const currentPlan = item.taskPlan
        const unchanged = currentPlan?.title === taskPlan.title
          && currentPlan.status === taskPlan.status
          && currentPlan.steps.length === taskPlan.steps.length
          && currentPlan.steps.every((step, index) => {
            const nextStep = taskPlan.steps[index]
            return step.id === nextStep?.id
              && step.title === nextStep.title
              && step.status === nextStep.status
              && step.error === nextStep.error
          })
        if (unchanged) return item
        changed = true
        return { ...item, taskPlan }
      })
      return changed ? next : current
    })
  }, [agentSessionId, conversationLongTask])

  React.useEffect(() => {
    if (!conversationLongTask || agentSessionId == null || !openedProject) {
      return undefined
    }
    const taskId = conversationLongTask.id
    const model = modelConfigs.find((item) => item.id === selectedModelId)
      ?? modelConfigs[0]
    if (!model) return undefined

    const runtime = getChatSessionRuntime(agentSessionId)
    if (!runtime) return undefined
    // A new durable task stays inside its ordinary root Run stream.  The
    // legacy polling bridge is recovery-only and must never subscribe beside
    // an active standard stream, otherwise every child event is rendered
    // twice through two transports.
    if (
      runtime.loading
      && !String(runtime.streamId || '').startsWith('long-task:')
    ) {
      return undefined
    }
    const runtimeMessages = runtime.messages
    let assistantIndex = -1
    for (let index = runtimeMessages.length - 1; index >= 0; index -= 1) {
      const item = runtimeMessages[index]
      if (item.role === 'assistant' && item.longTaskId === taskId) {
        assistantIndex = index
        break
      }
    }
    if (assistantIndex < 0) return undefined
    const persistedMessage = runtimeMessages[assistantIndex]
    if (
      conversationLongTask.status === 'completed'
      && persistedMessage.content.trim()
      && !isHostDispatchReceipt(persistedMessage.content)
    ) {
      return undefined
    }

    const resetMessage = resetLongTaskAssistantMessage(
      persistedMessage,
      conversationLongTask,
    )
    const resetMessages = [...runtimeMessages]
    resetMessages[assistantIndex] = resetMessage
    replaceChatRuntimeMessages(agentSessionId, resetMessages)
    setChatRuntimeLoading(
      agentSessionId,
      conversationLongTask.status === 'pending'
        || conversationLongTask.status === 'running',
    )
    setChatRuntimeActivity(agentSessionId, {
      state: conversationLongTask.status === 'paused'
        ? 'paused'
        : conversationLongTask.status === 'completed'
          ? 'completed'
          : conversationLongTask.status === 'failed'
            ? 'failed'
            : conversationLongTask.status === 'canceled'
              ? 'canceled'
              : 'running',
      queuedCount: 0,
    })

    let userText = ''
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (runtimeMessages[index].role === 'user') {
        userText = runtimeMessages[index].content
        break
      }
    }
    const elapsedMs = resetMessage.durationMs
      ?? (resetMessage.turnStartedAt != null
        ? Math.max(0, performance.now() - resetMessage.turnStartedAt)
        : 0)
    const acc: AccState = {
      response: resetMessage.content,
      thinking: '',
      bookId: openedProject.source_book_id,
      sessionId: agentSessionId,
      chapterId: null,
      needsTitle: false,
      userText,
      model: resetMessage.model || model.name,
      turnStartedAt: performance.now() - elapsedMs,
      toolCallSegments: undefined,
      thinkingBlocks: [],
      thinkingDurationsMs: [],
      contentAfterToolCalls: '',
      agentRunId: resetMessage.agentRunId,
      conversationRunId: resetMessage.agentRunId,
      longTaskId: taskId,
      taskPlan: resetMessage.taskPlan,
      delegations: resetMessage.delegations,
      contextCompaction: resetMessage.contextCompaction,
      contextBudget: resetMessage.contextBudget,
    }
    const runtimeSetConversations: React.Dispatch<
      React.SetStateAction<ChatMessage[]>
    > = (next) => {
      if (typeof next === 'function') {
        updateChatRuntimeMessages(agentSessionId, (current) => {
          let targetIndex = -1
          for (let index = current.length - 1; index >= 0; index -= 1) {
            const item = current[index]
            if (
              item.role === 'assistant'
              && item.longTaskId === taskId
              && (
                persistedMessage.conversationId == null
                || item.conversationId === persistedMessage.conversationId
              )
            ) {
              targetIndex = index
              break
            }
          }
          if (targetIndex < 0) return current
          const scoped = next([current[targetIndex]])
          const replacement = scoped.at(-1)
          if (!replacement || replacement === current[targetIndex]) return current
          const updated = [...current]
          updated[targetIndex] = replacement
          return updated
        })
      } else {
        const replacement = next.at(-1)
        if (!replacement) return
        updateChatRuntimeMessages(agentSessionId, (current) => {
          const targetIndex = current.findIndex((item) => (
            item.role === 'assistant'
            && item.longTaskId === taskId
            && (
              persistedMessage.conversationId == null
              || item.conversationId === persistedMessage.conversationId
            )
          ))
          if (targetIndex < 0) return current
          const updated = [...current]
          updated[targetIndex] = replacement
          return updated
        })
      }
    }
    const { scheduleCommit, flushCommits } = createCommitScheduler(
      runtimeSetConversations,
    )
    const adapter = createLongTaskConversationAdapter(
      resetMessage.content,
      conversationLongTask,
    )
    let stop = (): void => {}
    let reconnectTimer: number | undefined
    let terminalStatus: AiLongTask['status'] | undefined
    const ctx: ChunkCtx = {
      acc,
      sessionId: agentSessionId,
      cfg: model,
      apiModelName: model.name,
      writingChapters: sourceChapters.map((chapter) => ({
        id: chapter.id,
        title: chapter.title,
      })),
      availableOutlines: [],
      setConversations: runtimeSetConversations,
      scheduleCommit,
      flushCommits,
      setLoading: (next) => setChatRuntimeLoading(agentSessionId, next),
      setSessions: setAgentSessions,
      setScreenplayProposal: setAgentProposal,
      appMessage: message,
      isVisibleSession: () => activeAgentSessionRef.current === agentSessionId,
      persistConversation: Boolean(acc.conversationRunId),
      cleanup: (outcome) => {
        flushCommits()
        stop()
        setChatRuntimeStreamId(agentSessionId, undefined)
        setChatRuntimeLoading(agentSessionId, false)
        setChatRuntimeActivity(agentSessionId, {
          state: terminalStatus === 'completed'
            ? 'completed'
            : terminalStatus === 'canceled'
              ? 'canceled'
              : terminalStatus === 'failed'
                ? 'failed'
                : outcome,
          queuedCount: 0,
        })
        if (activeAgentSessionRef.current === agentSessionId) {
          setAgentResponse(acc.response)
          setAgentRunId(acc.agentRunId || '')
        }
      },
    }

    setChatRuntimeStreamId(agentSessionId, `long-task:${taskId}`)
    stop = services.ai.streamLongTaskConversation(
      { taskId, sessionId: agentSessionId },
      (event) => {
        if (event.type === 'stream.error') {
          message.error(event.error)
          reconnectTimer = window.setTimeout(() => {
            setLongTaskConversationEpoch((current) => current + 1)
          }, 1_000)
          return
        }
        if (event.type === 'task.progress') {
          setLongTasks((current) => current.map((task) => (
            task.id === event.taskId
              ? applyLongTaskProgress(task, event)
              : task
          )))
        }
        if (event.type === 'task.terminal') terminalStatus = event.status
        adapter.toChunks(event).forEach((chunk) => {
          recordAiDebugRunContinuation(conversationLongTask.parentRunId, chunk)
          dispatchChunk(chunk, ctx)
        })
        if (activeAgentSessionRef.current === agentSessionId) {
          setAgentResponse(acc.response)
          if (acc.agentRunId) setAgentRunId(acc.agentRunId)
        }
        if (event.type === 'task.terminal') {
          flushCommits()
          if (event.status === 'paused') {
            const durationMs = Math.max(
              0,
              Math.round(performance.now() - acc.turnStartedAt),
            )
            if (acc.taskPlan) {
              acc.taskPlan = { ...acc.taskPlan, status: 'paused' }
            }
            runtimeSetConversations((current) => {
              const next = [...current]
              const last = next.at(-1)
              if (!last || last.role !== 'assistant') return current
              next[next.length - 1] = {
                ...last,
                durationMs,
                turnStartedAt: undefined,
                taskPlan: last.taskPlan
                  ? { ...last.taskPlan, status: 'paused' }
                  : last.taskPlan,
                toolCalling: false,
              }
              return next
            })
            flushCommits()
            stop()
            setChatRuntimeStreamId(agentSessionId, undefined)
            setChatRuntimeLoading(agentSessionId, false)
            setChatRuntimeActivity(agentSessionId, {
              state: 'paused',
              queuedCount: 0,
            })
          }
          void loadProjectLongTasks(openedProject.id)
          void loadProjectDocuments(openedProject.id)
          void loadProjectSourceRefs(openedProject.id)
          // New tasks deliver the proposal through the ordinary chat chunk
          // before `done`. Fetching is only a compatibility path for tasks
          // created before terminal proposal events existed.
          if (!acc.screenplayProposal) {
            void services.ai.getLongTask({ taskId }).then((result) => {
              if (!result.success || !result.data) return
              const proposal = extractLongTaskProposal(result.data)
              if (proposal) presentLongTaskProposal(taskId, proposal)
            })
          }
        }
      },
    )
    return () => {
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
      flushCommits()
      stop()
    }
  }, [
    agentSessionId,
    conversationLongTask?.id,
    conversationLongTaskStreamKey,
    longTaskConversationEpoch,
    loadProjectDocuments,
    loadProjectLongTasks,
    loadProjectSourceRefs,
    message,
    modelConfigs,
    openedProject,
    presentLongTaskProposal,
    selectedModelId,
    sourceChapters,
  ])
  const activeConversationLongTask = React.useMemo(() => {
    const taskIds = new Set(
      agentMessages
        .map((item) => item.longTaskId)
        .filter((item): item is string => Boolean(item)),
    )
    return longTasks.find((task) => (
      taskIds.has(task.id)
      && longTaskBelongsToSession(task, agentSessionId)
      && (task.status === 'pending' || task.status === 'running')
    )) ?? null
  }, [agentMessages, agentSessionId, longTasks])
  const agentConversationLoading = agentRunning || activeConversationLongTask != null

  const detachActiveAgentStream = React.useCallback(() => {
    agentRecoveryTokenRef.current += 1
    // The Run and its SSE listener remain alive in the shared per-session
    // runtime. Switching conversations detaches only the visible view.
    agentUnsubscribeRef.current = null
    agentStreamIdRef.current = null
    setAgentRunning(false)
  }, [])

  const switchAgentSession = React.useCallback((sessionId: number) => {
    if (!openedProject || agentSessionLoading || sessionId === agentSessionId) return
    detachActiveAgentStream()
    void loadAgentSession(sessionId, openedProject, projectDocuments)
  }, [
    agentSessionId,
    agentSessionLoading,
    detachActiveAgentStream,
    loadAgentSession,
    openedProject,
    projectDocuments,
  ])

  const createAgentSession = React.useCallback(async () => {
    if (!openedProject || agentSessionLoading) return
    if (agentSessions.length > 0 && agentMessages.length === 0) return
    detachActiveAgentStream()
    setAgentSessionLoading(true)
    try {
      const result = await services.screenplay.createScreenplaySession({
        projectId: openedProject.id,
      })
      if (!result.success || !result.data) {
        message.error(result.error || '新建对话失败')
        return
      }
      setAgentSessions((current) => [...current, result.data!])
      await loadAgentSession(result.data.id, openedProject, projectDocuments)
    } finally {
      setAgentSessionLoading(false)
    }
  }, [
    agentMessages.length,
    agentSessionLoading,
    agentSessions.length,
    detachActiveAgentStream,
    loadAgentSession,
    message,
    openedProject,
    projectDocuments,
  ])

  const closeAgentSession = React.useCallback(async (session: AiSession) => {
    if (!openedProject || agentSessionLoading) return
    setAgentSessionLoading(true)
    try {
      const result = await services.sessions.setSessionClosed({ sessionId: session.id })
      if (!result.success) {
        message.error(result.error || '关闭对话失败')
        return
      }
      if (session.id === agentSessionId) detachActiveAgentStream()
      setAgentQueuedSubmissions((current) => current.filter(
        (submission) => submission.sessionId !== session.id,
      ))
      const remaining = agentSessions.filter((item) => item.id !== session.id)
      setAgentSessions(remaining)
      if (session.id !== agentSessionId) return
      const nextSession = remaining[remaining.length - 1]
      if (nextSession) {
        await loadAgentSession(nextSession.id, openedProject, projectDocuments)
        return
      }
      const created = await services.screenplay.createScreenplaySession({
        projectId: openedProject.id,
      })
      if (created.success && created.data) {
        setAgentSessions([created.data])
        await loadAgentSession(created.data.id, openedProject, projectDocuments)
      }
    } finally {
      setAgentSessionLoading(false)
    }
  }, [
    agentSessionId,
    agentSessionLoading,
    agentSessions,
    detachActiveAgentStream,
    loadAgentSession,
    message,
    openedProject,
    projectDocuments,
  ])

  const saveAgentSessionTitle = React.useCallback(async () => {
    if (editingAgentSessionId == null) return
    const title = editingAgentSessionTitle.trim()
    if (!title) {
      message.warning('对话名称不能为空')
      setEditingAgentSessionId(null)
      return
    }
    const result = await services.sessions.updateSessionTitle({
      sessionId: editingAgentSessionId,
      title,
    })
    if (result.success) {
      setAgentSessions((current) => current.map((session) => (
        session.id === editingAgentSessionId ? { ...session, title } : session
      )))
    } else {
      message.error(result.error || '更新对话名称失败')
    }
    setEditingAgentSessionId(null)
  }, [editingAgentSessionId, editingAgentSessionTitle, message])

  const cancelScreenplayLongTask = React.useCallback(async (task: AiLongTask) => {
    const result = await services.ai.cancelLongTask({ taskId: task.id })
    if (!result.success) {
      message.error(result.error || '终止长任务失败')
      return
    }
    if (agentSessionId != null) {
      setChatRuntimeLoading(agentSessionId, false)
      setChatRuntimeActivity(agentSessionId, {
        state: 'canceled',
        queuedCount: 0,
      })
    }
    if (openedProject) await loadProjectLongTasks(openedProject.id)
  }, [agentSessionId, loadProjectLongTasks, message, openedProject])

  const stopAgent = React.useCallback(() => {
    if (activeConversationLongTask) {
      void cancelScreenplayLongTask(activeConversationLongTask)
      return
    }
    if (!agentRunId && agentSessionId != null) {
      agentStopPendingSessionIdsRef.current.add(agentSessionId)
      return
    }
    if (agentRunId) {
      void services.ai.cancelAgentRun({ runId: agentRunId }).then((result) => {
        if (!result.success && result.error !== 'Agent Run 已结束') {
          message.error(result.error || '终止 Agent 任务失败')
        }
      })
    }
    const activeStreamId = getChatSessionRuntime(agentSessionId)?.streamId
      || agentStreamIdRef.current
    if (activeStreamId) {
      services.ai.abortAiStream(activeStreamId)
    }
  }, [
    activeConversationLongTask,
    agentRunId,
    agentSessionId,
    cancelScreenplayLongTask,
    message,
  ])

  const runAgent = React.useCallback(async (
    promptOverride?: string,
    taskIntent: 'chat' | 'stage_deliverable' = 'chat',
    editMessageIndex?: number,
    modelOverrideId?: string,
    draftSceneCount?: number,
    draftScope?: ScreenplayDraftScope,
  ) => {
    if (!openedProject) return
    if (openedProject.status === 'archived') {
      message.warning('项目已归档，请先恢复项目')
      return
    }
    if (agentSessionId == null) {
      message.warning('剧本 Agent 会话尚未就绪，请重新打开项目')
      return
    }
    const prompt = (promptOverride ?? agentPrompt).trim()
    if (!prompt) {
      message.warning('先告诉 Agent 这轮要解决什么')
      return
    }
    const resolvedDraftScope = openedProject.active_stage === 'draft'
      ? draftScope ?? inferDraftScope(prompt)
      : 'planner'
    const resolvedDraftSceneCount = openedProject.active_stage === 'draft'
      && ['planner', 'count', 'next_scene'].includes(resolvedDraftScope)
      ? draftSceneCount ?? inferDraftSceneCount(
        prompt,
        screenplayDraftBatchScope(openedProject, projectDocuments),
      )
      : 1
    const requestedModelId = modelOverrideId || selectedModelId
    const model = modelConfigs.find((item) => item.id === requestedModelId)
    if (!model?.apiKey?.trim()) {
      message.warning('请先在设置中添加可用模型')
      onOpenSettings()
      return
    }
    if (agentConversationLoading) {
      if (typeof editMessageIndex === 'number') return
      const nextQueue = [
        ...agentQueuedSubmissionsRef.current,
        {
          sessionId: agentSessionId,
          prompt,
          taskIntent,
          modelId: requestedModelId,
          draftSceneCount: resolvedDraftSceneCount,
          draftScope: resolvedDraftScope,
        },
      ]
      agentQueuedSubmissionsRef.current = nextQueue
      setAgentQueuedSubmissions(nextQueue)
      setAgentPrompt('')
      const queuedCount = nextQueue.filter(
        (submission) => submission.sessionId === agentSessionId,
      ).length
      setChatRuntimeActivity(agentSessionId, {
        state: 'running',
        queuedCount,
      })
      message.info(`已加入发送队列 · ${queuedCount} 条等待中`)
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
    const { options, apiModelName } = buildStreamOptions({
      cfg: model,
      selectedModel: model.id,
    })
    const streamId = createAiStreamId(`screenplay-${openedProject.id}`)
    const toHistoryMessage = buildHistoryConverter()
    const sessionMessages = getChatSessionRuntime(agentSessionId)?.messages
      ?? agentMessages
    const baseMessages = typeof editMessageIndex === 'number'
      ? sessionMessages.slice(0, editMessageIndex)
      : sessionMessages
    if (typeof editMessageIndex === 'number') {
      const keepTurnCount = Math.floor(editMessageIndex / 2)
      const deleteResult = await services.conversations.deleteConversationsAfterTurn({
        sessionId: agentSessionId,
        keepTurnCount,
      })
      if (!deleteResult.success) {
        message.error(deleteResult.error || '更新历史对话失败')
        return
      }
    }
    const historyMessages = baseMessages
      .map(toHistoryMessage)
      .filter((item): item is { role: string; content: string } => item != null)
    const turnStartedAt = performance.now()
    const userSentAt = new Date().toISOString()
    agentStopPendingSessionIdsRef.current.delete(agentSessionId)
    setAgentResponse('')
    setAgentRunId('')
    setAgentProposal(null)
    setAgentRunning(true)
    setAgentPrompt('')
    const nextMessages: ChatMessage[] = [
      ...baseMessages,
      { role: 'user', content: prompt, sentAt: userSentAt },
      {
        role: 'assistant',
        content: '',
        model: model.name,
        turnStartedAt,
      },
    ]
    replaceChatRuntimeMessages(agentSessionId, nextMessages)
    setChatRuntimeLoading(agentSessionId, true)
    setChatRuntimeActivity(agentSessionId, {
      state: 'running',
      queuedCount: agentQueuedSubmissionsRef.current.filter(
        (submission) => submission.sessionId === agentSessionId,
      ).length,
    })
    setAgentMessages(nextMessages)

    const acc: AccState = {
      response: '',
      thinking: '',
      bookId: openedProject.source_book_id,
      sessionId: agentSessionId,
      chapterId: null,
      needsTitle: baseMessages.length === 0 && (
        agentSessions.find((session) => session.id === agentSessionId)?.title === '新对话'
      ),
      userText: prompt,
      model: model.name,
      turnStartedAt,
      toolCallSegments: undefined,
      thinkingBlocks: [],
      thinkingDurationsMs: [],
      contentAfterToolCalls: '',
      agentRunId: undefined,
      taskPlan: undefined,
      delegations: undefined,
      contextCompaction: undefined,
      contextBudget: undefined,
      longTaskId: undefined,
    }
    agentStreamIdRef.current = streamId
    setChatRuntimeStreamId(agentSessionId, streamId)
    agentUnsubscribeRef.current?.()
    let unsubscribe = (): void => {}
    const runtimeSetConversations: React.Dispatch<
      React.SetStateAction<ChatMessage[]>
    > = (next) => {
      if (typeof next === 'function') {
        updateChatRuntimeMessages(agentSessionId, next)
      } else {
        replaceChatRuntimeMessages(agentSessionId, next)
      }
    }
    const { scheduleCommit, flushCommits } = createCommitScheduler(
      runtimeSetConversations,
    )
    const ctx: ChunkCtx = {
      acc,
      sessionId: agentSessionId,
      cfg: model,
      apiModelName,
      writingChapters: sourceChapters.map((chapter) => ({
        id: chapter.id,
        title: chapter.title,
      })),
      availableOutlines: [],
      setConversations: runtimeSetConversations,
      scheduleCommit,
      flushCommits,
      setLoading: (next) => setChatRuntimeLoading(agentSessionId, next),
      setSessions: setAgentSessions,
      setScreenplayProposal: (proposal) => {
        if (activeAgentSessionRef.current === agentSessionId) {
          setAgentProposal(proposal)
        }
      },
      appMessage: message,
      isVisibleSession: () => true,
      cleanup: (outcome) => {
        agentStopPendingSessionIdsRef.current.delete(agentSessionId)
        flushCommits()
        unsubscribe()
        agentRuntimeSubscriptionsRef.current.delete(agentSessionId)
        setChatRuntimeStreamId(agentSessionId, undefined)
        setChatRuntimeLoading(agentSessionId, false)
        const queuedCount = agentQueuedSubmissionsRef.current.filter(
          (submission) => submission.sessionId === agentSessionId,
        ).length
        setChatRuntimeActivity(agentSessionId, {
          state: queuedCount > 0
            ? 'queued'
            : acc.longTaskId
              ? 'running'
              : outcome,
          queuedCount,
        })
        if (activeAgentSessionRef.current === agentSessionId) {
          setAgentRunning(false)
          setAgentResponse(acc.response)
          setAgentRunId(acc.agentRunId || '')
        }
        if (acc.agentRunId) {
          void loadProjectSourceRefs(openedProject.id)
        }
        if (activeAgentSessionRef.current === agentSessionId) {
          agentStreamIdRef.current = null
          agentUnsubscribeRef.current = null
        }
      },
    }
    unsubscribe = services.ai.onAiChunk((chunk) => {
      if (chunk.error && acc.agentRunId) {
        flushCommits()
        unsubscribe()
        agentRuntimeSubscriptionsRef.current.delete(agentSessionId)
        setChatRuntimeStreamId(agentSessionId, undefined)
        if (activeAgentSessionRef.current === agentSessionId) {
          agentStreamIdRef.current = null
          agentUnsubscribeRef.current = null
          void monitorDetachedAgentRun(
            acc.agentRunId,
            agentSessionId,
            openedProject,
            projectDocuments,
            prompt,
          )
        }
        return
      }
      if (chunk.longTaskDispatched?.taskId) {
        void loadProjectLongTasks(openedProject.id)
      }
      if (chunk.longTaskProgress) {
        const progress = chunk.longTaskProgress
        setLongTasks((current) => current.map((task) => (
          task.id === progress.taskId
            ? applyLongTaskProgress(task, {
                type: 'task.progress',
                taskId: progress.taskId,
                status: progress.status,
                revision: progress.revision,
                totalUnits: progress.totalUnits,
                completedUnits: progress.completedUnits,
                failedUnits: progress.failedUnits,
                updateTime: progress.updateTime,
                units: progress.units,
              })
            : task
        )))
      }
      dispatchChunk(chunk, ctx)
      if (activeAgentSessionRef.current === agentSessionId) {
        setAgentResponse(acc.response)
        if (acc.agentRunId) setAgentRunId(acc.agentRunId)
      }
      if (
        agentStopPendingSessionIdsRef.current.has(agentSessionId)
        && acc.agentRunId
      ) {
        agentStopPendingSessionIdsRef.current.delete(agentSessionId)
        void services.ai.cancelAgentRun({ runId: acc.agentRunId })
        services.ai.abortAiStream(streamId)
      }
    }, streamId)
    agentRuntimeSubscriptionsRef.current.set(agentSessionId, {
      streamId,
      unsubscribe,
    })
    agentUnsubscribeRef.current = unsubscribe

    services.ai.aiChatStream({
      streamId,
      apiKey: model.apiKey,
      baseURL: model.baseUrl || undefined,
      apiProvider: normalizeApiProvider(model.apiProvider),
      locale: document.documentElement.lang || 'zh-CN',
      sessionId: agentSessionId,
      messages: [
        ...historyMessages,
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
      screenplayTaskIntent: taskIntent,
      ...(['planner', 'count', 'next_scene'].includes(resolvedDraftScope)
        ? { screenplayDraftSceneCount: resolvedDraftSceneCount }
        : {}),
      screenplayDraftScope: resolvedDraftScope,
    })
  }, [
    agentMessages,
    agentPrompt,
    agentConversationLoading,
    agentSessionId,
    agentSessions,
    message,
    loadProjectSourceRefs,
    loadProjectLongTasks,
    modelConfigs,
    monitorDetachedAgentRun,
    onOpenSettings,
    openedProject,
    projectDocuments,
    selectedModelId,
    sourceChapters,
  ])

  const runAgentRef = React.useRef(runAgent)
  runAgentRef.current = runAgent

  React.useEffect(() => {
    if (agentConversationLoading || agentSessionLoading || agentSessionId == null) return
    const nextIndex = agentQueuedSubmissions.findIndex(
      (submission) => submission.sessionId === agentSessionId,
    )
    if (nextIndex < 0) return
    const nextSubmission = agentQueuedSubmissions[nextIndex]
    const nextQueue = agentQueuedSubmissions.filter(
      (_submission, index) => index !== nextIndex,
    )
    agentQueuedSubmissionsRef.current = nextQueue
    setAgentQueuedSubmissions(nextQueue)
    queueMicrotask(() => {
      void runAgentRef.current(
        nextSubmission.prompt,
        nextSubmission.taskIntent,
        undefined,
        nextSubmission.modelId,
        nextSubmission.draftSceneCount,
        nextSubmission.draftScope,
      )
    })
  }, [
    agentQueuedSubmissions,
    agentConversationLoading,
    agentSessionId,
    agentSessionLoading,
  ])

  const saveAgentProposal = React.useCallback(async (
    forceNewVersion = false,
  ): Promise<EntityId | null> => {
    if (!openedProject || !agentProposal) return null
    if (savedAgentDocumentId && !forceNewVersion) return savedAgentDocumentId
    const persistedDocument = findPersistedProposalDocument(
      projectDocuments,
      agentProposal,
    )
    if (persistedDocument && !forceNewVersion) {
      const persistedStage = documentStageForKind(persistedDocument.kind)
      if (persistedStage) {
        setSelectedDocumentStages((current) => ({
          ...current,
          [openedProject.id]: persistedStage,
        }))
      }
      return persistedDocument.id
    }
    setSavingAgentDraft(true)
    try {
      const result = await services.screenplay.createScreenplayDocument({
        projectId: openedProject.id,
        kind: agentProposal.kind,
        title: agentProposal.title,
        contentJson: agentProposal.contentJson,
        contentText: agentProposal.contentText,
        derivedFromIds: agentProposal.derivedFromIds,
        // Durable screenplay proposals are finalized onto the dispatch/root
        // Run. Child Run ids only identify the currently visible batch and
        // cannot satisfy the proposal provenance check.
        sourceRunId: resolveProposalSourceRunId(conversationLongTask, agentRunId),
      })
      if (!result.success || !result.data) {
        // A request can finish on the server while its response or optimistic
        // UI update is lost. Reconcile once before surfacing an error so a
        // durable save is never presented as an unsaved proposal.
        const refreshedDocuments = await loadProjectDocuments(openedProject.id)
        const recoveredDocument = findPersistedProposalDocument(
          refreshedDocuments || [],
          agentProposal,
        )
        if (recoveredDocument && !forceNewVersion) {
          const recoveredStage = documentStageForKind(recoveredDocument.kind)
          if (recoveredStage) {
            setSelectedDocumentStages((current) => ({
              ...current,
              [openedProject.id]: recoveredStage,
            }))
          }
          return recoveredDocument.id
        }
        message.error(result.error || '保存剧本文档提案失败')
        return null
      }
      const savedStage = documentStageForKind(result.data.kind)
      if (savedStage) {
        setSelectedDocumentStages((current) => ({
          ...current,
          [openedProject.id]: savedStage,
        }))
      }
      setProjectDocuments((current) => (
        current.some((document) => document.id === result.data?.id)
          ? current
          : [...current, result.data as ScreenplayDocument]
      ))
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
    conversationLongTask?.parentRunId,
    loadProjectDocuments,
    loadProjectSourceRefs,
    message,
    openedProject,
    projectDocuments,
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
      if (!accepted.success || !accepted.data) {
        message.error(accepted.error || '接受剧本文档提案失败')
        return
      }
      const acceptedDocument = accepted.data
      setProjectDocuments((current) => {
        let found = false
        const updated = current.map((document) => {
          if (document.id === acceptedDocument.id) {
            found = true
            return acceptedDocument
          }
          if (
            document.kind === acceptedDocument.kind
            && document.status === 'accepted'
          ) {
            return { ...document, status: 'superseded' as const }
          }
          return document
        })
        return found ? updated : [...updated, acceptedDocument]
      })
      const refreshed = await services.screenplay.getScreenplayProject({
        projectId: openedProject.id,
      })
      await Promise.all([
        loadProjectDocuments(openedProject.id),
        loadProjectSourceRefs(openedProject.id),
      ])
      if (refreshed.success && refreshed.data) {
        setOpenedProject(refreshed.data)
        setProjects((current) => current.map((project) => (
          project.id === refreshed.data?.id ? refreshed.data : project
        )))
      }
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
    setDocumentEditing(false)
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
      setDocumentEditing(false)
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

  const deleteDocument = React.useCallback(async (
    document: ScreenplayDocument,
  ) => {
    if (
      !openedProject
      || openedProject.status === 'archived'
      || document.status !== 'draft'
      || deletingDocumentId != null
    ) return
    setDeletingDocumentId(document.id)
    try {
      const result = await services.screenplay.deleteScreenplayDocument({
        documentId: document.id,
      })
      if (!result.success) {
        message.error(result.error || '删除文档失败')
        return
      }
      setProjectDocuments((current) => current.filter((item) => item.id !== document.id))
      setProjectSourceRefs((current) => current.filter((ref) => ref.document_id !== document.id))
      if (selectedDocument?.id === document.id) {
        setSelectedDocument(null)
        setComparisonDocument(null)
        setDocumentEditing(false)
      }
      setDeleteDocumentTarget(null)
      message.success('文档草稿已删除')
    } finally {
      setDeletingDocumentId(null)
    }
  }, [
    deletingDocumentId,
    message,
    openedProject,
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
  const briefStepPrompt: Record<BriefStepKey, string> = {
    basics: '确定项目身份，以及最终要呈现的剧本形态',
    scope: '确定 Agent 本次可以读取的原作范围',
    direction: sourceKind === 'book'
      ? '确定新剧本与原作之间的距离'
      : '确定故事最先从哪个方向开始探索',
  }
  const milestone = openedProject ? nextMilestone(openedProject) : null
  const activeAgentTaskPlan = React.useMemo(
    () => getActiveTaskPlan(agentMessages, agentConversationLoading),
    [agentConversationLoading, agentMessages],
  )
  const activeAgentQueuedSubmissions = React.useMemo(
    () => agentQueuedSubmissions.filter(
      (submission) => submission.sessionId === agentSessionId,
    ),
    [agentQueuedSubmissions, agentSessionId],
  )
  const selectedAgentModelConfig = React.useMemo(
    () => modelConfigs.find((model) => model.id === selectedModelId) ?? null,
    [modelConfigs, selectedModelId],
  )
  const agentConversationCapabilities = getAgentConversationCapabilities({
    running: agentRunning,
    readOnly: openedProject?.status === 'archived',
    sessionLoading: agentSessionLoading,
  })
  const agentSessionActivities = React.useMemo(() => {
    const allActivities = getChatSessionActivities()
    return Object.fromEntries(
      agentSessions.flatMap((session) => (
        allActivities[session.id]
          ? [[session.id, allActivities[session.id]]]
          : []
      )),
    )
  }, [agentRuntimeVersion, agentSessions])
  const openedProjectStageIndex = openedProject
    ? Math.max(0, SCREENPLAY_STAGE_ORDER.indexOf(openedProject.active_stage))
    : 0
  const projectDocumentGroups = React.useMemo(() => (
    DOCUMENT_STAGE_GROUPS.map((group, index) => ({
      ...group,
      index,
      documents: projectDocuments.filter((document) => (
        group.kinds.includes(document.kind)
      )),
    }))
  ), [projectDocuments])
  const nonEmptyProjectDocumentGroups = projectDocumentGroups.filter(
    (group) => group.documents.length > 0,
  )
  const preferredProjectDocumentStage = openedProject
    ? selectedDocumentStages[openedProject.id]
    : undefined
  const selectedProjectDocumentGroup = (
    projectDocumentGroups.find((group) => (
      group.stage === preferredProjectDocumentStage
      && group.documents.length > 0
    ))
    ?? projectDocumentGroups.find((group) => (
      group.stage === openedProject?.active_stage
      && group.documents.length > 0
    ))
    ?? nonEmptyProjectDocumentGroups.at(-1)
    ?? null
  )
  const proposalWillAdvance = proposalAdvancesProjectStage(openedProject, agentProposal)
  const proposalNewSceneCount = agentProposal?.kind === 'scene_draft'
    && Array.isArray(agentProposal.contentJson.newSceneIds)
    ? agentProposal.contentJson.newSceneIds.length
    : agentProposal?.kind === 'scene_draft'
      ? 1
      : 0
  const hasPendingAgentProposal = agentProposal != null && acceptedAgentDocumentId == null
  const hasActiveLongTask = longTasks.some(
    (task) => ['pending', 'running', 'paused'].includes(task.status),
  )
  // The CURRENT TASK panel always keeps the stage's primary shortcut visible.
  // Runtime/proposal state may temporarily disable it, but must not remove the
  // entry point and make the panel appear to have lost its core action.
  const showStageStartAction = openedProject?.active_stage !== 'completed'
  const stageStartActionDisabled = openedProject?.status === 'archived'
    || agentSessionLoading
    || agentRunning
    || hasActiveLongTask
    || hasPendingAgentProposal
    || (
      openedProject?.active_stage === 'orientation'
      && openedProject.source_kind === 'book'
      && !openedProject.source_book_id
    )
  const draftBatchScope = openedProject
    ? screenplayDraftBatchScope(openedProject, projectDocuments)
    : null
  const draftBatchActions = draftBatchScope
    ? buildDraftBatchActions(draftBatchScope)
    : []
  const handleStageStartAction = React.useCallback(() => {
    if (
      !openedProject
      || openedProject.active_stage === 'completed'
      || agentRunning
      || hasActiveLongTask
      || hasPendingAgentProposal
    ) {
      return
    }
    const defaultDraftScope: ScreenplayDraftScope = openedProject.active_stage === 'draft'
      && draftBatchScope?.hasEpisodeNumbers
      ? 'next_episode'
      : 'next_scene'
    runAgent(
      stageAgentStarter(
        openedProject,
        projectDocuments,
        1,
        defaultDraftScope,
      ),
      ['orientation', 'brief', 'structure', 'scenes'].includes(openedProject.active_stage)
        ? 'stage_deliverable'
        : 'chat',
      undefined,
      undefined,
      undefined,
      defaultDraftScope,
    )
  }, [
    agentRunning,
    hasActiveLongTask,
    hasPendingAgentProposal,
    draftBatchScope?.hasEpisodeNumbers,
    openedProject,
    projectDocuments,
    runAgent,
  ])
  const handleDraftBatchAction = React.useCallback((action: DraftBatchAction) => {
    if (
      !openedProject
      || openedProject.active_stage !== 'draft'
      || agentRunning
      || hasActiveLongTask
      || hasPendingAgentProposal
    ) {
      return
    }
    runAgent(
      stageAgentStarter(
        openedProject,
        projectDocuments,
        1,
        action.key,
      ),
      'chat',
      undefined,
      undefined,
      undefined,
      action.key,
    )
  }, [
    agentRunning,
    hasActiveLongTask,
    hasPendingAgentProposal,
    openedProject,
    projectDocuments,
    runAgent,
  ])
  const editAgentMessage = React.useCallback((messageIndex: number, content: string) => {
    if (!openedProject) return
    const originalPrompt = agentMessages[messageIndex]?.content?.trim() || ''
    const followingProposal = agentMessages[messageIndex + 1]?.screenplayProposal
    const taskIntent = followingProposal
      || originalPrompt === stageAgentStarter(openedProject, projectDocuments).trim()
      ? 'stage_deliverable'
      : 'chat'
    runAgent(content, taskIntent, messageIndex)
  }, [agentMessages, openedProject, projectDocuments, runAgent])
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
        title={(
          <span className="screenplay-agent-header-title">
            <span className="app-title">剧本 Agent</span>
            {stage !== 'source' && (
              <span className="screenplay-agent-stage">{stageLabel}</span>
            )}
          </span>
        )}
        left={(
          <>
            {stage === 'source' ? (
              <PurrTooltip title="返回首页">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<ArrowLeftIcon style={{ fontSize: 14 }} />}
                  onClick={onBack}
                  aria-label="返回首页"
                />
              </PurrTooltip>
            ) : (
              <>
                <PurrTooltip title="返回首页">
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<HomeIcon style={{ fontSize: 14 }} />}
                    onClick={onBack}
                    aria-label="返回首页"
                  />
                </PurrTooltip>
                <PurrTooltip title={headerBackLabel}>
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<ArrowLeftIcon style={{ fontSize: 14 }} />}
                    onClick={handleHeaderBack}
                    aria-label={headerBackLabel}
                  />
                </PurrTooltip>
              </>
            )}
          </>
        )}
        showActions
        onOpenSettings={onOpenSettings}
      />

      <main className={[
        'screenplay-agent-main',
        stage === 'brief' ? 'screenplay-agent-main--brief' : '',
        stage === 'project' ? 'screenplay-agent-main--project' : '',
      ].filter(Boolean).join(' ')}>
        {stage === 'source' && (
          <div className="screenplay-entry">
            <header className="screenplay-entry-header">
              <h1>开始创作</h1>
              <p>选择一个起点，或继续已有项目。</p>
            </header>

            <section className="screenplay-entry-section" aria-labelledby="screenplay-create-title">
              <div className="screenplay-entry-section__header">
                <h2 id="screenplay-create-title">新建剧本</h2>
                {entrySourceView === 'books' && <span>{sortedBooks.length} 本作品</span>}
              </div>
              <div className={`screenplay-source-picker screenplay-source-picker--${entrySourceView}`}>
                {entrySourceView === 'choices' ? (
                  <div className="screenplay-entry-choice-grid">
                    <button
                      type="button"
                      className="screenplay-entry-choice purr-entry-surface"
                      onClick={startOriginal}
                    >
                      <span className="screenplay-entry-choice__icon"><PlusIcon /></span>
                      <span className="screenplay-entry-choice__copy">
                        <strong>新建原创剧本</strong>
                        <span>从人物、世界或一个故事想法开始</span>
                      </span>
                      <span className="screenplay-entry-choice__footer">
                        开始创作 <ArrowRightIcon />
                      </span>
                    </button>

                    <button
                      type="button"
                      className="screenplay-entry-choice purr-entry-surface"
                      onClick={() => setEntrySourceView('books')}
                    >
                      <span className="screenplay-entry-choice__icon"><BookIcon /></span>
                      <span className="screenplay-entry-choice__copy">
                        <strong>从小说开始改编</strong>
                        <span>选择书架作品，再确定章节范围与改编方式</span>
                      </span>
                      <span className="screenplay-entry-choice__footer">
                        {sortedBooks.length > 0 ? `${sortedBooks.length} 本作品` : '查看书架'}
                        <ArrowRightIcon />
                      </span>
                    </button>
                  </div>
                ) : (
                  <div className="screenplay-book-selector">
                    <header className="screenplay-book-selector__header">
                      <div>
                        <h3>选择书架作品</h3>
                        <p>选择后可以继续设置改编章节范围。</p>
                      </div>
                      <button
                        type="button"
                        className="screenplay-book-selector__back"
                        onClick={() => setEntrySourceView('choices')}
                        aria-label="返回新建剧本选择"
                      >
                        <ArrowLeftIcon /> 返回选择
                      </button>
                    </header>

                    {sortedBooks.length > 0 ? (
                      <div className="screenplay-book-selector__grid">
                        {sortedBooks.map((book) => {
                          const isRecentAdaptation = book.id === recentAdaptationBookId
                          return (
                            <button
                              key={book.id}
                              type="button"
                              className="screenplay-book-choice purr-data-entry-surface"
                              onClick={() => startFromBook(book)}
                              aria-label={`引用《${book.title}》创作剧本`}
                            >
                              <span
                                className="screenplay-book-choice__cover"
                                style={{ '--screenplay-book-color': book.cover_color || '#c94361' } as React.CSSProperties}
                              >
                                <BookIcon />
                              </span>
                              <span className="screenplay-book-choice__copy">
                                <strong>{book.title}</strong>
                                <span>{isRecentAdaptation ? '最近用于改编' : '书架作品'}</span>
                              </span>
                              <ArrowRightIcon />
                            </button>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="screenplay-book-selector__empty">
                        <span><FileTextIcon /></span>
                        <div>
                          <strong>书架暂无作品</strong>
                          <p>先创建一本小说，再回来选择改编范围。</p>
                        </div>
                        <PurrButton onClick={onOpenBookshelf}>前往书架</PurrButton>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>

            {(projectsLoading || projects.length > 0) && (
              <section className="screenplay-recent-projects" aria-labelledby="screenplay-recent-title">
                <div className="screenplay-recent-projects__header">
                  <h2 id="screenplay-recent-title">我的项目</h2>
                  <div className="screenplay-project-list-tools">
                    {!projectsLoading && projects.length > 0 && (
                      <PurrInput
                        size="small"
                        className="screenplay-project-search"
                        prefix={<SearchIcon />}
                        allowClear
                        value={projectSearch}
                        placeholder="搜索项目"
                        aria-label="搜索我的项目"
                        onChange={(event) => setProjectSearch(event.target.value)}
                      />
                    )}
                  </div>
                </div>
                {!projectsLoading && (
                  <div className="screenplay-project-strip">
                    {filteredProjects.map((project) => {
                      const sourceBook = books.find((book) => book.id === project.source_book_id)
                      const projectBusy = projectMutationId === project.id
                      const isLastOpenedProject = project.id === lastOpenedProjectId
                      return (
                        <article
                          key={project.id}
                          className={[
                            'screenplay-project-option',
                            'purr-data-entry-surface',
                            isLastOpenedProject ? 'is-last-opened' : '',
                          ].filter(Boolean).join(' ')}
                        >
                          <button
                            type="button"
                            className="screenplay-project-option__open"
                            onClick={() => void openProject(project)}
                          >
                            <span className="screenplay-project-option__icon">
                              <VideoCameraIcon />
                            </span>
                            <span className="screenplay-project-option__copy">
                              <span className="screenplay-project-option__title-row">
                                <strong>{project.title}</strong>
                                {isLastOpenedProject && (
                                  <small className="screenplay-project-option__last-opened">
                                    上次打开
                                  </small>
                                )}
                              </span>
                              <span className="screenplay-project-option__meta">
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
                          </button>
                          <PurrDropdown
                            placement="bottomRight"
                            trigger={['click']}
                            menu={{
                              items: [
                                {
                                  key: 'rename',
                                  label: '重命名',
                                  icon: <EditIcon />,
                                  disabled: projectBusy,
                                  onClick: () => openRenameProject(project),
                                },
                                {
                                  key: 'archive',
                                  label: project.status === 'archived' ? '恢复项目' : '归档项目',
                                  icon: project.status === 'archived'
                                    ? <RefreshIcon />
                                    : <InboxIcon />,
                                  disabled: projectBusy,
                                  onClick: () => void toggleListedProjectArchived(project),
                                },
                                {
                                  key: 'delete',
                                  label: '删除项目',
                                  icon: <DeleteIcon />,
                                  disabled: projectBusy,
                                  onClick: () => setDeleteProjectTarget(project),
                                },
                              ],
                            }}
                          >
                            <PurrTooltip title="项目操作">
                              <PurrButton
                                type="text"
                                size="small"
                                icon={projectBusy
                                  ? <LoadingIcon spin />
                                  : <MoreIcon />}
                                className="screenplay-project-option__menu"
                                disabled={projectBusy}
                                aria-label={`管理项目《${project.title}》`}
                              />
                            </PurrTooltip>
                          </PurrDropdown>
                        </article>
                      )
                    })}
                    {filteredProjects.length === 0 && (
                      <div className="screenplay-project-search-empty">
                        没有找到匹配的项目
                      </div>
                    )}
                  </div>
                )}
              </section>
            )}
          </div>
        )}

        {stage === 'brief' && (
          <div className="screenplay-brief">
            <div className="screenplay-brief__heading">
              <span className="screenplay-agent-kicker">STORY SETUP</span>
              <h1>设置创作方向</h1>
              <p>先确定剧本形态和创作边界，其余细节可以交给 Agent 和你一起完善。</p>
            </div>

            <div className="screenplay-brief-wizard">
              <section className="screenplay-brief-wizard__main">
                <PurrSteps
                  items={briefSteps}
                  current={briefStepIndex}
                  availableUntil={furthestBriefStepIndex}
                  completedUntil={furthestBriefStepIndex - 1}
                  ariaLabel="创作设置步骤"
                  className="screenplay-brief-steps"
                  onChange={selectBriefStep}
                />

                <div className="screenplay-brief-panel">
                  <h2 className="screenplay-sr-only">
                    {currentBriefStep.title}
                  </h2>

                  <div className={[
                    'screenplay-brief-panel__body',
                    currentBriefStep.key === 'scope' ? 'is-dense' : 'is-composed',
                  ].join(' ')}>
                    <div className="screenplay-brief-panel__content">
                      <div className="screenplay-step-prompt">
                        <span>当前任务</span>
                        <strong>{briefStepPrompt[currentBriefStep.key]}</strong>
                      </div>

                    {currentBriefStep.key === 'basics' && (
                      <div className="screenplay-brief-composition screenplay-brief-composition--basics">
                        <section className="screenplay-config-block">
                          <header className="screenplay-config-block__header">
                            <span>01</span>
                            <div>
                              <h3>项目名称</h3>
                            </div>
                          </header>
                          <PurrInput
                            size="large"
                            value={projectTitle}
                            aria-label="项目名称"
                            onChange={(event) => setProjectTitle(event.target.value)}
                            placeholder="给这次创作起个名字"
                            autoFocus
                          />
                        </section>

                        <section className="screenplay-config-block">
                          <header className="screenplay-config-block__header">
                            <span>02</span>
                            <div>
                              <h3>剧本形态</h3>
                            </div>
                          </header>
                          <PurrChoiceCard.Group<ScreenplayFormat>
                            value={format ?? undefined}
                            size="small"
                            columns={3}
                            ariaLabel="剧本形态"
                            onChange={(event) => setFormat(event.target.value)}
                          >
                            {FORMAT_OPTIONS.map((option) => (
                              <PurrChoiceCard
                                key={option}
                                value={option}
                                title={option}
                              />
                            ))}
                          </PurrChoiceCard.Group>
                        </section>
                      </div>
                    )}

                    {currentBriefStep.key === 'scope' && (
                      <div className="screenplay-field screenplay-scope-field screenplay-wizard-scope">
                        <PurrChoiceCard.Group<SourceScopeFamily>
                          value={sourceScopeFamily}
                          size="large"
                          columns={2}
                          ariaLabel="原作范围"
                          onChange={(event) => chooseSourceScopeFamily(event.target.value)}
                        >
                          {([
                            {
                              key: 'whole' as const,
                              title: '整本作品',
                              description: `${sourceStructure.chapters.length} 章全部纳入`,
                            },
                            {
                              key: 'custom' as const,
                              title: '选择部分内容',
                              description: '连续选择或自由选择章节',
                            },
                          ]).map((option) => (
                            <PurrChoiceCard
                              key={option.key}
                              value={option.key}
                              title={option.title}
                              description={option.description}
                            />
                          ))}
                        </PurrChoiceCard.Group>

                        {sourceChaptersLoading ? (
                          <div className="screenplay-scope-loading">
                            <LoadingIcon spin /> 正在读取章节目录…
                          </div>
                        ) : sourceChaptersError ? (
                          <div className="screenplay-scope-warning">
                            {sourceChaptersError}
                          </div>
                        ) : (
                          <>
                            {sourceScopeFamily !== 'whole' && (
                              <div className="screenplay-scope-detail">
                                <div className="screenplay-scope-detail__header">
                                  <div>
                                    <strong>选择方式</strong>
                                    <span>两种方式相互独立，不会覆盖彼此的配置</span>
                                  </div>
                                  {sourceStructure.volumes.length > 0 && (
                                    <PurrSegmented<SourceScopeUnit>
                                      size="small"
                                      value={sourceScopeUnit}
                                      options={[
                                        { label: '按章节', value: 'chapters' },
                                        { label: '按分卷', value: 'volumes' },
                                      ]}
                                      onChange={chooseSourceScopeUnit}
                                    />
                                  )}
                                </div>

                                <PurrChoiceCard.Group<SourceScopePartialMethod>
                                  value={sourceScopePartialMethod}
                                  size="middle"
                                  columns={2}
                                  ariaLabel="章节选择方式"
                                  onChange={(event) => chooseSourceScopePartialMethod(
                                    event.target.value,
                                  )}
                                >
                                  {([
                                    {
                                      key: 'leading' as const,
                                      title: '连续选择',
                                      description: `从作品开头选取前 N ${
                                        sourceScopeUnit === 'chapters' ? '章' : '卷'
                                      }`,
                                    },
                                    {
                                      key: 'selected' as const,
                                      title: '自由选择',
                                      description: `搜索并勾选任意${
                                        sourceScopeUnit === 'chapters' ? '章节' : '分卷'
                                      }`,
                                    },
                                  ]).map((option) => (
                                    <PurrChoiceCard
                                      key={option.key}
                                      value={option.key}
                                      title={option.title}
                                      description={option.description}
                                    />
                                  ))}
                                </PurrChoiceCard.Group>

                                {sourceScopePartialMethod === 'leading' ? (
                                  <div className="screenplay-scope-leading">
                                    <div>
                                      <strong>
                                        从开头连续选择
                                      </strong>
                                      <span>
                                        Agent 将按原作顺序读取连续内容
                                      </span>
                                    </div>
                                    <div className="screenplay-scope-count">
                                      <span>前</span>
                                      <PurrInputNumber
                                        value={sourceScopeCount}
                                        min={1}
                                        max={sourceScopeUnit === 'chapters'
                                          ? Math.max(1, sourceStructure.chapters.length)
                                          : Math.max(1, sourceStructure.volumes.length)}
                                        placeholder="数量"
                                        onChange={setSourceScopeCount}
                                      />
                                      <span>
                                        {sourceScopeUnit === 'chapters' ? '章' : '卷'}
                                      </span>
                                      <small>
                                        最多 {sourceScopeUnit === 'chapters'
                                          ? sourceStructure.chapters.length
                                          : sourceStructure.volumes.length}
                                        {sourceScopeUnit === 'chapters' ? ' 章' : ' 卷'}
                                      </small>
                                    </div>
                                  </div>
                                ) : (
                                  <div className="screenplay-scope-select-field">
                                    <span>
                                      {sourceScopeUnit === 'chapters'
                                        ? '具体章节'
                                        : '具体分卷'}
                                    </span>
                                    <PurrMultiSelect<EntityId>
                                      value={selectedSourceScopeIds}
                                      options={sourceScopeOptions}
                                      size="large"
                                      searchable
                                      allowClear
                                      maxVisibleValues={2}
                                      className="screenplay-scope-select"
                                      placeholder={sourceScopeUnit === 'chapters'
                                        ? '搜索并选择章节'
                                        : '搜索并选择分卷'}
                                      searchPlaceholder={sourceScopeUnit === 'chapters'
                                        ? '搜索章节标题、序号或分卷'
                                        : '搜索分卷名称'}
                                      onChange={changeSelectedSourceScopeIds}
                                    />
                                  </div>
                                )}
                              </div>
                            )}

                            <div className={`screenplay-scope-summary ${
                              sourceScopeSelection.error ? 'is-warning' : ''
                            }`}>
                              <CheckCircleIcon />
                              <span>{sourceScopeSelection.summary}</span>
                            </div>
                            {sourceStructure.volumes.length === 0
                              && sourceStructure.chapters.length > 0 && (
                                <small>当前作品没有分卷结构，因此只提供按章节选择。</small>
                              )}
                          </>
                        )}
                      </div>
                    )}

                    {currentBriefStep.key === 'direction' && (
                      <div className="screenplay-brief-composition screenplay-brief-composition--direction">
                        <section className="screenplay-config-block">
                          <header className="screenplay-config-block__header">
                            <span>01</span>
                            <div>
                              <h3>
                                {sourceKind === 'book' ? '改编方式' : '探索起点'}
                              </h3>
                            </div>
                          </header>
                          <PurrChoiceCard.Group<string>
                            value={approach || undefined}
                            size="small"
                            columns={1}
                            ariaLabel={sourceKind === 'book' ? '改编方式' : '探索起点'}
                            onChange={(event) => setApproach(event.target.value)}
                          >
                            {(sourceKind === 'book'
                              ? ADAPTATION_OPTIONS
                              : ORIGINAL_OPTIONS).map((option) => (
                                <PurrChoiceCard
                                  key={option}
                                  value={option}
                                  title={option}
                                />
                              ))}
                          </PurrChoiceCard.Group>
                        </section>

                        <section className="screenplay-config-block">
                          <header className="screenplay-config-block__header">
                            <span>02</span>
                            <div>
                              <div className="screenplay-config-block__title-line">
                                <h3>{sourceKind === 'book' ? '改编想法' : '故事想法'}</h3>
                                <span>可选</span>
                              </div>
                            </div>
                          </header>
                          <PurrInput.TextArea
                            size="large"
                            value={premise}
                            aria-label={sourceKind === 'book' ? '改编想法' : '故事想法'}
                            className="screenplay-direction-premise"
                            onChange={(event) => setPremise(event.target.value)}
                            rows={7}
                            placeholder={sourceKind === 'book'
                              ? '例如：保留主角关系，把故事压缩成一部悬疑电影；结局希望更有余味……'
                              : '例如：一个替人保管记忆的人，发现自己最重要的回忆也属于别人……'}
                          />
                        </section>
                      </div>
                    )}
                    </div>
                  </div>

                  <footer className="screenplay-brief-panel__footer">
                    <PurrButton
                      type="text"
                      icon={<ArrowLeftIcon />}
                      disabled={briefStepIndex === 0}
                      onClick={returnToPreviousBriefStep}
                    >
                      上一步
                    </PurrButton>
                    <PurrButton
                      type="primary"
                      size="middle"
                      icon={<ArrowRightIcon />}
                      iconPosition="end"
                      onClick={continueBrief}
                    >
                      {briefStepIndex === briefSteps.length - 1 ? '确认并预览' : '继续'}
                    </PurrButton>
                  </footer>
                </div>
              </section>

              <aside className="screenplay-brief-summary">
                <div className="screenplay-brief-summary__eyebrow">当前创作</div>
                <div className="screenplay-brief-summary__source">
                  <span
                    className={[
                      'screenplay-brief-source__cover',
                      sourceKind === 'original'
                        ? 'screenplay-brief-source__cover--original'
                        : '',
                    ].filter(Boolean).join(' ')}
                    style={sourceKind === 'book'
                      ? {
                          '--screenplay-book-color': selectedBook?.cover_color || '#c94361',
                        } as React.CSSProperties
                      : undefined}
                  >
                    {sourceKind === 'book' ? <BookIcon /> : <PlusIcon />}
                  </span>
                  <div>
                    <span>{sourceKind === 'book' ? '小说改编' : '原创剧本'}</span>
                    <strong>
                      {sourceKind === 'book' && selectedBook
                        ? `《${selectedBook.title}》`
                        : '从一个新故事开始'}
                    </strong>
                  </div>
                </div>

                <div className="screenplay-brief-summary__list" aria-live="polite">
                  {briefSummaryRows.map((row) => (
                    <div
                      key={row.key}
                      className={row.value ? '' : 'is-empty'}
                    >
                      <span>{row.label}</span>
                      <strong aria-label={row.value ? undefined : '尚未选择'}>
                        {row.value || '— —'}
                      </strong>
                    </div>
                  ))}
                </div>

                <div className="screenplay-source-boundary">
                  <CheckCircleIcon />
                  <span>
                    {sourceKind === 'book'
                      ? '原作只读 · 剧本独立保存'
                      : '创建独立剧本项目'}
                  </span>
                </div>
              </aside>
            </div>
          </div>
        )}

        {stage === 'handoff' && launchDraft && (
          <div className="screenplay-handoff">
            <section className="screenplay-handoff-card">
              <header className="screenplay-handoff-card__status">
                <div>
                  <span className="screenplay-agent-kicker">READY TO CREATE</span>
                  <h1>确认创作任务</h1>
                  <p>确认关键信息后，Agent 将开始第一轮整理。</p>
                </div>
                <span className="screenplay-handoff-ready">
                  <CheckCircleIcon />
                  配置完成
                </span>
              </header>

              <div className="screenplay-handoff-card__body">
                <div className="screenplay-handoff-task">
                  <header className="screenplay-handoff-task__header">
                    <span><RobotIcon /></span>
                    <div>
                      <small>AGENT 第一轮</small>
                      <h2>
                        生成一份{launchDraft.format}
                        {launchDraft.sourceKind === 'book' ? '改编简报' : '创作简报'}
                      </h2>
                    </div>
                  </header>
                  <p>
                    {launchDraft.sourceKind === 'book'
                      ? 'Agent 会先完成三项整理，再把需要你决定的改编内容交回来。'
                      : 'Agent 会先确认故事核心，再把仍需补充的关键问题交回来。'}
                  </p>

                  {launchDraft.premise && (
                    <div className="screenplay-handoff-note">
                      <span>你的补充</span>
                      <p>{launchDraft.premise}</p>
                    </div>
                  )}

                  <div className="screenplay-handoff-task-plan">
                    <span>本轮处理过程</span>
                    <div>
                      {(launchDraft.sourceKind === 'book'
                        ? [
                            ['01', '提炼素材', '人物、设定与关键情节'],
                            ['02', '形成判断', `适合${launchDraft.format}的结构重点`],
                            ['03', '交付简报', '标记需要你确认的取舍'],
                          ]
                        : [
                            ['01', '确认核心', '主角、目标与核心冲突'],
                            ['02', '形成方向', `适合${launchDraft.format}的主题与结构`],
                            ['03', '交付简报', '标记仍需补充的关键问题'],
                          ]).map(([index, title, description]) => (
                            <div key={index}>
                              <span>{index}</span>
                              <strong>{title}</strong>
                              <small>{description}</small>
                            </div>
                          ))}
                    </div>
                  </div>

                  <div className="screenplay-handoff-boundaries">
                    <div>
                      <BookIcon />
                      <span>
                        <strong>
                          {launchDraft.sourceKind === 'book' ? '原作保护' : '素材边界'}
                        </strong>
                        {launchDraft.sourceKind === 'book'
                          ? '原作只读，不回写小说'
                          : '不读取书架作品'}
                      </span>
                    </div>
                    <div>
                      <TeamIcon />
                      <span>
                        <strong>需要确认</strong>
                        关键创作取舍由你决定
                      </span>
                    </div>
                    <div>
                      <EditIcon />
                      <span>
                        <strong>保存位置</strong>
                        独立剧本项目
                      </span>
                    </div>
                  </div>
                </div>

                <aside className="screenplay-handoff-snapshot">
                  <span>本次创建</span>
                  <h2>{launchDraft.projectTitle}</h2>
                  <div className="screenplay-handoff-snapshot__source">
                    {launchDraft.sourceKind === 'book'
                      ? `小说改编 · 《${launchDraft.sourceBookTitle}》`
                      : '原创剧本'}
                  </div>
                  <dl>
                    {launchDraft.sourceKind === 'book' && (
                      <div>
                        <dt>原作范围</dt>
                        <dd>{launchDraft.sourceScopeSummary}</dd>
                      </div>
                    )}
                    <div>
                      <dt>剧本形态</dt>
                      <dd>{launchDraft.format}</dd>
                    </div>
                    <div>
                      <dt>{launchDraft.sourceKind === 'book' ? '改编方式' : '探索起点'}</dt>
                      <dd>{launchDraft.approach}</dd>
                    </div>
                    <div>
                      <dt>{launchDraft.sourceKind === 'book' ? '改编想法' : '故事想法'}</dt>
                      <dd>{launchDraft.premise ? '已加入任务' : '交给 Agent 探索'}</dd>
                    </div>
                  </dl>
                </aside>
              </div>

              <footer className="screenplay-handoff-card__footer">
                <span>
                  <CheckCircleIcon />
                  将创建项目并保存首份创作简报
                </span>
                <PurrButton
                  type="primary"
                  icon={<VideoCameraIcon />}
                  loading={creatingProject}
                  onClick={() => void createProject()}
                >
                  创建并进入项目
                </PurrButton>
              </footer>
            </section>
          </div>
        )}

        {stage === 'project' && openedProject && (
          <div className="screenplay-project">
            <section className="screenplay-project-workspace">
              <header className="screenplay-project-workspace__header">
                <div className="screenplay-project-workspace__identity">
                  <span className="screenplay-agent-kicker">SCREENPLAY PROJECT</span>
                  <h1>{openedProject.title}</h1>
                  <div className="screenplay-project-workspace__source">
                    <span className="screenplay-project-workspace__source-icon">
                      {openedProject.source_kind === 'book' ? <BookIcon /> : <VideoCameraIcon />}
                    </span>
                    <span>
                      {openedProject.source_kind === 'original'
                        ? '原创故事'
                        : openedProject.source_book_id
                          ? `改编自《${books.find((book) => book.id === openedProject.source_book_id)?.title || '书架作品'}》`
                          : '来源作品已移除，已有剧本文档仍然保留'}
                    </span>
                  </div>
                </div>
                <div className="screenplay-project-workspace__actions">
                  <span className="screenplay-project-workspace__stage">
                    {openedProject.status === 'archived'
                      ? '只读归档'
                      : STAGE_LABELS[openedProject.active_stage]}
                  </span>
                  <PurrButton
                    size="small"
                    icon={openedProject.status === 'archived'
                      ? <UndoIcon />
                      : <InboxIcon />}
                    loading={updatingProjectStatus}
                    onClick={() => void toggleProjectArchived()}
                  >
                    {openedProject.status === 'archived' ? '恢复项目' : '归档项目'}
                  </PurrButton>
                  <PurrButton
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteIcon />}
                    disabled={updatingProjectStatus || projectMutationId != null}
                    onClick={() => setDeleteProjectTarget(openedProject)}
                  >
                    删除项目
                  </PurrButton>
                </div>
              </header>

              <div className="screenplay-project-overview">
                <section className="screenplay-project-next">
                  <div className="screenplay-project-next__heading">
                    <span className="screenplay-agent-icon"><RobotIcon /></span>
                    <div>
                      <div className="screenplay-project-next__meta">
                        <span className="screenplay-source-eyebrow">CURRENT TASK</span>
                      </div>
                      <h2>{milestone?.title}</h2>
                    </div>
                    <span className="screenplay-project-next__stage-count">
                      {openedProjectStageIndex + 1}/{SCREENPLAY_STAGE_ORDER.length}
                    </span>
                  </div>
                  <div className="screenplay-project-next__command">
                    <p>{milestone?.description}</p>
                    {showStageStartAction ? (
                      <div className="screenplay-project-next__actions">
                        {openedProject.active_stage === 'draft'
                          && draftBatchActions.length > 0 ? (
                            <PurrDropdown.Button
                              type="primary"
                              size="small"
                              placement="bottomRight"
                              trigger={['click']}
                              disabled={stageStartActionDisabled}
                              icon={<ChevronDownIcon />}
                              dropdownAriaLabel="选择创作范围"
                              onClick={handleStageStartAction}
                              menu={{
                                items: draftBatchActions.map((action) => ({
                                  key: action.key,
                                  label: action.label,
                                  onClick: () => handleDraftBatchAction(action),
                                })),
                              }}
                            >
                              {stagePrimaryActionLabel(openedProject, projectDocuments)}
                            </PurrDropdown.Button>
                          ) : (
                            <PurrButton
                              type="primary"
                              size="small"
                              icon={<ArrowRightIcon />}
                              disabled={stageStartActionDisabled}
                              onClick={handleStageStartAction}
                            >
                              {stagePrimaryActionLabel(openedProject, projectDocuments)}
                            </PurrButton>
                          )}
                      </div>
                    ) : null}
                  </div>
                  <div className="screenplay-project-next__footer">
                    <div className="screenplay-project-progress">
                      <ol className="screenplay-project-stage-labels" aria-label="剧本创作流程">
                        {SCREENPLAY_STAGE_ORDER.map((stageKey, index) => (
                          <li
                            key={stageKey}
                            className={[
                              index < openedProjectStageIndex ? 'is-completed' : '',
                              index === openedProjectStageIndex ? 'is-current' : '',
                            ].filter(Boolean).join(' ')}
                            aria-current={index === openedProjectStageIndex ? 'step' : undefined}
                          >
                            {STAGE_LABELS[stageKey]}
                          </li>
                        ))}
                      </ol>
                    </div>
                  </div>
                </section>

                <aside className="screenplay-project-context">
                  <div className="screenplay-project-section-title">
                    <div>
                      <span className="screenplay-source-eyebrow">PROJECT CONTEXT</span>
                      <h2>项目设定</h2>
                    </div>
                    <span>
                      {openedProject.status === 'archived'
                        ? '已归档'
                        : openedProject.active_stage === 'completed'
                          ? '已完成'
                          : '创作中'}
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>剧本形态</dt>
                      <dd>{openedProject.format}</dd>
                    </div>
                    <div>
                      <dt>{openedProject.source_kind === 'book' ? '改编方式' : '探索起点'}</dt>
                      <dd>{openedProject.approach}</dd>
                    </div>
                    <div>
                      <dt>创作范围</dt>
                      <dd>
                        {openedProject.source_kind === 'book'
                          ? describePersistedSourceScope(openedProject.source_scope)
                          : '原创故事'}
                      </dd>
                    </div>
                  </dl>
                  {openedProject.premise && (
                    <div className="screenplay-project-context__premise">
                      <span>创作设想</span>
                      <p>{openedProject.premise}</p>
                    </div>
                  )}
                  {acceptedScreenplayDraft && (
                    <div className="screenplay-project-export">
                      <span>
                        {openedProject.delivery_manifest
                          ? `最终交付已固化 · ${
                            openedProject.delivery_manifest.documents.length
                          } 项文档`
                          : '已生成可导出的完整剧本'}
                      </span>
                      <div>
                        <PurrSelect
                          size="small"
                          value={exportFormat}
                          options={[
                            { value: 'fountain', label: 'Fountain' },
                            { value: 'pdf', label: '标准 PDF' },
                            { value: 'markdown', label: 'Markdown' },
                            { value: 'txt', label: '纯文本' },
                            ...(openedProject.delivery_manifest
                              ? [{ value: 'json', label: '交付清单 JSON' }]
                              : []),
                          ]}
                          onChange={(value) => setExportFormat(value as typeof exportFormat)}
                          disabled={exportingScreenplay}
                        />
                        <PurrButton
                          icon={<ExportIcon />}
                          loading={exportingScreenplay}
                          onClick={() => void exportScreenplay()}
                        >
                          导出
                        </PurrButton>
                      </div>
                    </div>
                  )}
                </aside>
              </div>

              <section className={`screenplay-agent-studio ${openedProject.status === 'archived' ? 'is-readonly' : ''}`}>
                <header className="screenplay-agent-studio__header">
                  <div>
                    <span className="screenplay-source-eyebrow">SCREENPLAY AGENT</span>
                    <h2>与 Agent 继续创作</h2>
                  </div>
                </header>

                <div className="screenplay-agent-studio__body">
                  {agentConversationIndexOpen ? (
                    <AgentConversationIndex
                      sessions={agentSessions}
                      activeSessionId={agentSessionId}
                      editingSessionId={editingAgentSessionId}
                      editingTitle={editingAgentSessionTitle}
                      isCurrentSessionEmpty={agentMessages.length === 0}
                      disabled={agentConversationCapabilities.sessionNavigationDisabled}
                      sessionActivities={agentSessionActivities}
                      onActiveSessionChange={switchAgentSession}
                      onEditingSessionIdChange={setEditingAgentSessionId}
                      onEditingTitleChange={setEditingAgentSessionTitle}
                      onSaveTitle={() => void saveAgentSessionTitle()}
                      onNewSession={() => void createAgentSession()}
                      onCloseSession={(session) => void closeAgentSession(session)}
                      onCollapse={() => setAgentConversationIndexOpen(false)}
                      context={(
                        <div className="screenplay-conversation-context">
                          <span><VideoCameraIcon /></span>
                          <div>
                            <small>当前项目</small>
                            <strong title={openedProject.title}>{openedProject.title}</strong>
                            <span>{STAGE_LABELS[openedProject.active_stage]}</span>
                          </div>
                        </div>
                      )}
                    />
                  ) : (
                    <PurrTooltip title="展开对话列表" placement="right">
                      <PurrButton
                        type="text"
                        className="screenplay-conversation-index-reopen"
                        icon={<MessageIcon />}
                        onClick={() => setAgentConversationIndexOpen(true)}
                        aria-label="展开对话列表"
                      />
                    </PurrTooltip>
                  )}
                  <div className="screenplay-agent-studio__chat">
                <AgentConversation
                  messages={agentMessages}
                  loading={agentConversationLoading}
                  emptyTitle="从当前任务开始"
                  emptyDescription="发送后会实时展示思考过程、素材读取和执行结果。"
                  afterMessagesHostRef={setAgentResultHost}
                  afterMessagesVersion={agentResultHost
                    ? [
                        agentProposal?.title || agentResponse.length,
                        savedAgentDocumentId || 'unsaved',
                        acceptedAgentDocumentId || 'unapplied',
                      ].join(':')
                    : 'detached'}
                  onEditMessage={editAgentMessage}
                />

                <AgentComposer
                  className="screenplay-agent-studio__compose"
                  value={agentPrompt}
                  onChange={setAgentPrompt}
                  onSubmit={runAgent}
                  autoSize={{ minRows: 1, maxRows: 5 }}
                  disabled={agentConversationCapabilities.inputDisabled}
                  submitDisabled={
                    !agentPrompt.trim()
                    || openedProject.status === 'archived'
                    || !selectedModelId
                  }
                  placeholder="输入希望 Agent 完成的任务"
                  ariaLabel="输入希望剧本 Agent 完成的任务"
                  floatingContent={activeAgentTaskPlan ? (
                    <AgentTaskProgress
                      plan={activeAgentTaskPlan}
                      placement="topLeft"
                    />
                  ) : null}
                  supplementaryContent={activeAgentQueuedSubmissions.length > 0 ? (
                    <div className="screenplay-agent-queued" aria-label="待发送消息">
                      {activeAgentQueuedSubmissions.slice(0, 3).map((submission, index) => (
                        <div
                          className="screenplay-agent-queued__item"
                          key={`${submission.sessionId}-${index}-${submission.prompt}`}
                        >
                          <span>待发送 {index + 1}</span>
                          <span title={submission.prompt}>{submission.prompt}</span>
                        </div>
                      ))}
                      {activeAgentQueuedSubmissions.length > 3 ? (
                        <div className="screenplay-agent-queued__more">
                          另有 {activeAgentQueuedSubmissions.length - 3} 条消息排队
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  footer={(
                    <div className="screenplay-agent-studio__actions">
                      <div className="screenplay-agent-studio__compose-left">
                        {modelConfigs.length > 0 ? (
                          <ModelPicker
                            className="screenplay-agent-model"
                            modelConfigs={modelConfigs}
                            selectedModelId={selectedModelId}
                            onModelChange={setSelectedModelId}
                            onUpdateModelConfig={onUpdateModelConfig}
                            disabled={openedProject.status === 'archived'}
                          />
                        ) : (
                          <PurrButton
                            type="text"
                            size="small"
                            disabled={openedProject.status === 'archived'}
                            onClick={onOpenSettings}
                          >
                            配置模型
                          </PurrButton>
                        )}
                        <span className="screenplay-agent-studio__compose-hint">
                          {openedProject.status === 'archived'
                            ? '当前项目只读'
                            : openedProject.source_kind === 'book'
                              ? '原作只读 · 结果另存为新版本'
                              : '使用当前项目与已有版本'}
                        </span>
                      </div>
                      <div className="screenplay-agent-studio__compose-right">
                        {activeAgentQueuedSubmissions.length > 0 ? (
                          <span className="screenplay-agent-queue-count" role="status">
                            排队 {activeAgentQueuedSubmissions.length}
                          </span>
                        ) : null}
                        <ContextUsageIndicator
                          conversations={agentMessages}
                          selectedModelConfig={selectedAgentModelConfig}
                        />
                        {agentConversationLoading ? (
                          <PurrTooltip title="停止生成">
                            <PurrButton
                              type="text"
                              shape="circle"
                              className="agent-composer__stop"
                              icon={<StopCircleIcon size={18} />}
                              onClick={stopAgent}
                              aria-label="停止生成"
                            />
                          </PurrTooltip>
                        ) : null}
                        <PurrTooltip title={agentConversationCapabilities.submitMode === 'queue'
                          ? '加入发送队列 (Enter)'
                          : '发送 (Enter)'}>
                          <PurrButton
                            type="primary"
                            shape="circle"
                            className="agent-composer__send"
                            icon={<ArrowUpIcon style={{ fontSize: 16 }} />}
                            disabled={
                              !agentPrompt.trim()
                              || openedProject.status === 'archived'
                              || !selectedModelId
                            }
                            onClick={() => runAgent()}
                            aria-label={agentConversationCapabilities.submitMode === 'queue'
                              ? '加入发送队列'
                              : '发送'}
                          />
                        </PurrTooltip>
                      </div>
                    </div>
                  )}
                />

                {agentResultHost && createPortal((
                  agentProposal
                ) && (
                  <div className="screenplay-agent-result">
                    <div className="screenplay-agent-result__title">
                      <span>
                        <CheckCircleIcon />
                        本轮产物
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
                    </div>
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
                                ? '已应用到项目'
                                : savedAgentDocumentId
                                  ? '已保存，等待应用'
                                  : '尚未保存'}
                            </span>
                          </div>
                          <span className={`screenplay-document-status ${
                            acceptedAgentDocumentId
                              ? 'is-accepted'
                              : savedAgentDocumentId
                                ? 'is-saved'
                                : ''
                          }`}>
                            {acceptedAgentDocumentId
                              ? '已应用'
                              : savedAgentDocumentId
                                ? '已保存'
                                : '待处理'}
                          </span>
                        </header>
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
                              ? openedProject.active_stage === 'review'
                                ? '保存只创建草稿；应用后锁定最终审阅并完成项目。'
                                : '保存只创建草稿；应用后锁定当前版本并推进到下一阶段。'
                              : agentProposal.kind === 'scene_draft'
                                && agentProposal.contentJson.isComplete !== true
                                ? '保存只创建草稿；应用后更新当前滚动整稿，继续创作下一批。'
                                : '保存只创建草稿；应用后更新当前审阅或完整剧本版本。'}
                          </span>
                          <div>
                            <PurrButton
                              icon={<SaveIcon />}
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
                            </PurrButton>
                            {savedAgentDocumentId && (
                              <PurrButton
                                icon={<SaveIcon />}
                                loading={savingAgentDraft && !acceptingAgentDraft}
                                disabled={
                                  acceptingAgentDraft
                                  || openedProject.status === 'archived'
                                }
                                onClick={() => {
                                  void saveAgentProposal(true).then((documentId) => {
                                    if (documentId) message.success('已再次保存为新的项目草稿')
                                  })
                                }}
                              >
                                再次保存
                              </PurrButton>
                            )}
                            <PurrButton
                              type="primary"
                              icon={<CheckCircleIcon />}
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
                                  ? openedProject.active_stage === 'review'
                                    ? '已应用并完成'
                                    : '已应用并推进'
                                  : agentProposal.kind === 'review'
                                    ? '已应用审阅结论'
                                    : openedProject.active_stage === 'review'
                                      ? '已应用修订稿'
                                      : '已应用为当前整稿'
                                : proposalWillAdvance
                                  ? openedProject.active_stage === 'review'
                                    ? '应用并完成'
                                    : '应用并推进'
                                  : agentProposal.kind === 'review'
                                    ? '应用审阅结论'
                                  : agentProposal.kind === 'scene_draft'
                                    && agentProposal.contentJson.isComplete !== true
                                    ? proposalNewSceneCount > 1
                                      ? `应用本批 ${proposalNewSceneCount} 场`
                                      : '应用本场'
                                    : '应用当前版本'}
                            </PurrButton>
                          </div>
                        </footer>
                      </article>
                    )}
                  </div>
                ), agentResultHost)}
                  </div>
                </div>
              </section>

              <section className="screenplay-project-documents">
                <div className="screenplay-project-section-title">
                  <div>
                    <span className="screenplay-source-eyebrow">PROJECT FILES</span>
                    <h2>项目文档</h2>
                  </div>
                  <span>
                    {projectLoading
                      ? '读取中…'
                      : `${nonEmptyProjectDocumentGroups.length} 个阶段 · ${projectDocuments.length} 个版本`}
                  </span>
                </div>
                {!projectLoading && projectDocuments.length === 0 ? (
                  <div className="screenplay-project-empty">Agent 的输出保存后，会出现在这里</div>
                ) : (
                  <button
                    type="button"
                    className="screenplay-document-library-launch"
                    onClick={() => setDocumentLibraryOpen(true)}
                  >
                    <span className="screenplay-document-library-launch__icon">
                      <FileTextIcon />
                    </span>
                    <span>
                      <strong>浏览项目文档</strong>
                      <small>
                        {selectedProjectDocumentGroup
                          ? `${STAGE_LABELS[selectedProjectDocumentGroup.stage]} · ${selectedProjectDocumentGroup.documents.length} 个版本`
                          : `${projectDocuments.length} 个版本`}
                      </small>
                    </span>
                    <ArrowRightIcon />
                  </button>
                )}
              </section>
            </section>
          </div>
        )}

        <PurrModal
          title="项目文档"
          open={documentLibraryOpen && openedProject != null}
          width="min(900px, calc(100vw - 40px))"
          destroyOnHidden
          footer={null}
          onCancel={() => setDocumentLibraryOpen(false)}
        >
          {openedProject && selectedProjectDocumentGroup && (
            <div className="screenplay-document-library-modal">
              <nav
                className="screenplay-document-stage-nav screenplay-document-stage-nav--library"
                aria-label="按创作阶段筛选项目文档"
              >
                {projectDocumentGroups.map((group) => {
                  const selected = selectedProjectDocumentGroup.stage === group.stage
                  const current = openedProject.active_stage === group.stage
                  return (
                    <button
                      type="button"
                      className={[
                        'screenplay-document-stage-nav__item',
                        selected ? 'is-selected' : '',
                        current ? 'is-current-stage' : '',
                      ].filter(Boolean).join(' ')}
                      disabled={group.documents.length === 0}
                      aria-pressed={selected}
                      aria-label={`${STAGE_LABELS[group.stage]}，${group.documents.length} 个版本`}
                      onClick={() => {
                        setSelectedDocumentStages((value) => ({
                          ...value,
                          [openedProject.id]: group.stage,
                        }))
                      }}
                      key={group.stage}
                    >
                      <span>{String(group.index + 1).padStart(2, '0')}</span>
                      <strong>{STAGE_LABELS[group.stage]}</strong>
                      <small>{group.documents.length}</small>
                    </button>
                  )
                })}
              </nav>
              <section className="screenplay-document-stage-panel">
                <header className="screenplay-document-stage-panel__header">
                  <span>
                    <strong>{STAGE_LABELS[selectedProjectDocumentGroup.stage]}</strong>
                    <small>
                      {selectedProjectDocumentGroup.kinds
                        .filter((kind) => selectedProjectDocumentGroup.documents.some(
                          (document) => document.kind === kind,
                        ))
                        .map((kind) => DOCUMENT_KIND_LABELS[kind])
                        .join(' / ')}
                    </small>
                  </span>
                  <span>{selectedProjectDocumentGroup.documents.length} 个版本</span>
                </header>
                <div className="screenplay-document-list">
                  {selectedProjectDocumentGroup.documents.map((document) => {
                    const previousVersion = previousDocumentVersion(
                      document,
                      projectDocuments,
                    )
                    const sourceCount = projectSourceRefs.filter(
                      (ref) => ref.document_id === document.id,
                    ).length
                    return (
                      <article className="screenplay-document-item" key={document.id}>
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
                            {sourceCount > 0 ? ` · ${sourceCount} 条来源` : ''}
                          </span>
                        </div>
                        <span className="screenplay-document-item__actions">
                          <span className={`screenplay-document-status is-${document.status}`}>
                            {document.status === 'draft'
                              ? '可编辑'
                              : document.status === 'accepted'
                                ? '当前版本'
                                : '历史版本'}
                          </span>
                          <PurrButton
                            size="small"
                            type="text"
                            icon={<EyeIcon />}
                            onClick={() => {
                              setDocumentLibraryOpen(false)
                              openDocument(document)
                            }}
                            aria-label={`查看${document.title}`}
                            title="查看文档"
                          />
                          {previousVersion && (
                            <PurrButton
                              size="small"
                              type="text"
                              icon={<HistoryIcon />}
                              onClick={() => {
                                setDocumentLibraryOpen(false)
                                openDocument(document, true)
                              }}
                              aria-label={`对比${document.title}的历史版本`}
                              title="对比版本"
                            />
                          )}
                          {document.status === 'draft' && openedProject.status !== 'archived' && (
                            <PurrButton
                              size="small"
                              type="text"
                              icon={deletingDocumentId === document.id
                                ? <LoadingIcon spin />
                                : <DeleteIcon />}
                              disabled={deletingDocumentId != null}
                              onClick={() => setDeleteDocumentTarget(document)}
                              aria-label={`删除${document.title}草稿版本`}
                              title="删除草稿版本"
                            />
                          )}
                        </span>
                      </article>
                    )
                  })}
                </div>
              </section>
            </div>
          )}
        </PurrModal>

        <PurrModal
          title="删除文档草稿"
          open={deleteDocumentTarget != null}
          confirmLoading={deletingDocumentId === deleteDocumentTarget?.id}
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
          onOk={() => {
            if (deleteDocumentTarget) void deleteDocument(deleteDocumentTarget)
          }}
          onCancel={() => {
            if (deletingDocumentId != null) return
            setDeleteDocumentTarget(null)
          }}
          destroyOnHidden
        >
          <p>确认删除“{deleteDocumentTarget?.title}”草稿？删除后无法恢复。</p>
        </PurrModal>

        <PurrModal
          title="重命名剧本项目"
          open={renameProjectTarget != null}
          confirmLoading={projectMutationId === renameProjectTarget?.id}
          okText="保存"
          cancelText="取消"
          onOk={() => void renameProject()}
          onCancel={() => {
            if (projectMutationId === renameProjectTarget?.id) return
            setRenameProjectTarget(null)
            setRenameProjectTitle('')
          }}
          destroyOnHidden
        >
          <PurrInput
            value={renameProjectTitle}
            maxLength={120}
            autoFocus
            placeholder="输入项目名称"
            onChange={(event) => setRenameProjectTitle(event.target.value)}
            onPressEnter={() => void renameProject()}
          />
        </PurrModal>

        <PurrModal
          title="删除剧本项目"
          open={deleteProjectTarget != null}
          confirmLoading={projectMutationId === deleteProjectTarget?.id}
          okText="删除"
          cancelText="取消"
          onOk={() => void deleteProject()}
          onCancel={() => {
            if (projectMutationId === deleteProjectTarget?.id) return
            setDeleteProjectTarget(null)
          }}
          destroyOnHidden
        >
          <div className="screenplay-project-delete-confirm">
            <p>确认删除《{deleteProjectTarget?.title}》？</p>
          </div>
        </PurrModal>

        <PurrModal
          open={selectedDocument != null}
          title={selectedDocument
            ? `${DOCUMENT_KIND_LABELS[selectedDocument.kind]} · v${selectedDocument.version}`
            : '剧本文档'}
          width="min(920px, calc(100vw - 40px))"
          destroyOnHidden
          onCancel={() => {
            setSelectedDocument(null)
            setComparisonDocument(null)
            setDocumentEditing(false)
          }}
          footer={selectedDocument ? (
            <div className="screenplay-version-modal__footer">
              <div>
                {selectedPreviousDocument && (
                  <PurrButton
                    icon={<HistoryIcon />}
                    onClick={() => {
                      setDocumentEditing(false)
                      setComparisonDocument((current) => (
                        current ? null : selectedPreviousDocument
                      ))
                    }}
                  >
                    {comparisonDocument ? '返回文档' : `与 v${selectedPreviousDocument.version} 对比`}
                  </PurrButton>
                )}
                {selectedDocument.status !== 'draft' && (
                  <PurrButton
                    icon={<UndoIcon />}
                    loading={restoringDocumentId === selectedDocument.id}
                    disabled={openedProject?.status === 'archived'}
                    onClick={() => void restoreDocumentVersion(selectedDocument)}
                  >
                    恢复为新草稿
                  </PurrButton>
                )}
              </div>
              <div>
                <PurrButton
                  onClick={() => {
                    setSelectedDocument(null)
                    setComparisonDocument(null)
                    setDocumentEditing(false)
                  }}
                >
                  关闭
                </PurrButton>
                {selectedDocument.status === 'draft' && !comparisonDocument && (
                  <>
                    <PurrButton
                      icon={documentEditing ? <UndoIcon /> : <EditIcon />}
                      disabled={openedProject?.status === 'archived'}
                      onClick={() => {
                        if (documentEditing) {
                          setDocumentTitleDraft(selectedDocument.title)
                          setDocumentTextDraft(selectedDocument.content_text)
                        }
                        setDocumentEditing((current) => !current)
                      }}
                    >
                      {documentEditing ? '取消编辑' : '编辑文档'}
                    </PurrButton>
                    {documentEditing && (
                      <PurrButton
                        type="primary"
                        icon={<SaveIcon />}
                        loading={savingDocument}
                        disabled={openedProject?.status === 'archived'}
                        onClick={() => void saveSelectedDocument()}
                      >
                        保存草稿
                      </PurrButton>
                    )}
                  </>
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
                  {documentEditing ? (
                    <>
                      <label>
                        <span>文档标题</span>
                        <PurrInput
                          value={documentTitleDraft}
                          onChange={(event) => setDocumentTitleDraft(event.target.value)}
                        />
                      </label>
                      <label>
                        <span>正文内容 · Markdown</span>
                        <PurrInput.TextArea
                          value={documentTextDraft}
                          rows={18}
                          onChange={(event) => setDocumentTextDraft(event.target.value)}
                        />
                      </label>
                    </>
                  ) : (
                    <article className="screenplay-version-document">
                      <h2>{selectedDocument.title}</h2>
                      <Markdown className="screenplay-version-document__body">
                        {selectedDocument.content_text || '*暂无正文内容*'}
                      </Markdown>
                    </article>
                  )}
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
        </PurrModal>
      </main>
    </div>
  )
}

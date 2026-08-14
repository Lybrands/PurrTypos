import { services } from '@/services'
import React from 'react'
import AppHeader from '../components/AppHeader'
import { AgentConversationPanel } from '../components/AgentConversation'
import {
  hydrateAiDebugRunSnapshot,
  recordScreenplayAiDebugChunk,
  type AiDebugChunk,
} from '../components/AiDevInspector/store'
import Markdown from '../components/Markdown'
import {
  ArrowLeftIcon,
  ArrowRightIcon,
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
  PurrModal,
  PlusIcon,
  RefreshIcon,
  RobotIcon,
  SearchIcon,
  PurrSegmented,
  PurrSelect,
  PurrSteps,
  type PurrStepItem,
  TeamIcon,
  PurrTooltip,
  UndoIcon,
  usePurrConfirm,
  VideoCameraIcon,
} from '@/purr-components'
import { useAppFeedback } from '../hooks/useAppFeedback'
import type {
  AiModelConfig,
  AiSession,
  Book,
  Chapter,
  EntityId,
  ScreenplayDocument,
  ScreenplayDocumentEpisode,
  ScreenplayAgentTask,
  ScreenplayDraftEpisode,
  ScreenplayFormat,
  ScreenplayProject,
  ScreenplaySourceRef,
  ScreenplaySourceKind,
  ScreenplaySourceScopeMode,
  ScreenplaySourceScopeRequest,
  ScreenplayConversationRuntimeInput,
  ScreenplayConversationTurn,
  ScreenplayOperationProjection,
  ScreenplayStageCommand,
  ScreenplayV2ReviewFindingStatus,
  ScreenplayV2Workspace,
} from '../types'
import {
  AgentChunkReplay,
  type AgentConversationActivity,
  type AiStreamChunk,
  type AgentConversationMessage,
} from '../agent-runtime'
import { buildStreamOptions } from '../agent-runtime/streamOptions'
import { getActiveTaskPlan } from '../agent-runtime/taskPlan'
import {
  buildDraftBatchActions,
  draftScopeForEpisodeCount,
  MAX_SCREENPLAY_DRAFT_BATCH_EPISODES,
  type DraftBatchAction,
  type ScreenplayDraftScope,
} from './draftBatchIntent'
import { normalizeApiProvider } from '../modelCatalog'
import { buildScreenplayLineDiff } from './versionDiff'
import {
  buildSourceStructure,
  describePersistedSourceScope,
  resolveSourceScopeSelection,
} from './sourceScope'
import { selectScreenplayAgentSession } from './sessionRestore'
import {
  createScreenplayCommandId,
  projectFromV2Project,
  projectFromWorkspace,
  revisionLibraryTarget,
  screenplayFormatToV2,
  screenplaySourceToV2,
  type RevisionLibraryTarget,
} from './screenplayProjectModel'
import { ScreenplayConversationClient } from './conversationClient'
import {
  isScreenplayOperationCancellable,
  modelRunIds,
  screenplayTurnArtifacts,
  screenplayTurnReconciliationKey,
  type ScreenplayConversationState,
  type ScreenplayTurnArtifact,
} from './conversationState'
import { createScreenplayOperationCommandLatch } from './screenplayOperationCommandLatch'
import {
  createScreenplayConversationSessionLifecycle,
  type ScreenplayConversationLoadToken,
} from './screenplayConversationSessionLifecycle'
import RevisionLibraryModal from './RevisionLibraryModal'
import ReviewAdjudicationPanel from './ReviewAdjudicationPanel'
import {
  reviewAgentActionAvailable,
  reviewWorkspaceEntry,
} from './reviewAdjudicationModel'
import { stageAgentAction } from './stageAgentAction'
import { structuredContentToMarkdown } from './revisionDocumentView'
import {
  documentEpisodesFromRevision,
  documentFromRevision,
  draftEpisodesFromRevision,
} from './revisionProposal'
import { useScreenplayConversationExtensions } from './ScreenplayConversationExtensions'
import {
  useScreenplayConversationController,
  type ScreenplayConversationBindings,
  type ScreenplayQueuedSubmission,
} from './useScreenplayConversationController'
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
  onUpdateModelConfig?: (
    id: string,
    patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>,
  ) => void
  onOpenBookshelf: () => void
  onOpenSettings: () => void
  onBack: () => void
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
const DELIVERABLE_ROLE_LABELS: Record<ScreenplayTurnArtifact['role'], string> = {
  sourceAnalysis: '原作分析',
  creativeBrief: '创作简报',
  structure: '结构设计',
  sceneList: '场景规划',
  screenplayDraft: '剧本正文',
  review: '审阅修订',
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
function screenplayChunkChangesConversation(chunk: AiStreamChunk): boolean {
  return Object.keys(chunk).some((key) => key !== 'model' && key !== 'streamId')
}

function backendTimestampMs(value?: string | null): number | null {
  const normalized = String(value || '').trim().replace(' ', 'T')
  if (!normalized) return null
  const timestamp = Date.parse(
    /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized) ? normalized : `${normalized}Z`,
  )
  return Number.isFinite(timestamp) ? timestamp : null
}

function screenplayTurnDurationMs(
  turn: ScreenplayConversationTurn,
  operation?: ScreenplayOperationProjection,
  task?: ScreenplayAgentTask,
): number {
  const startedAt = backendTimestampMs(turn.createdAt)
  const finishedAt = backendTimestampMs(
    operation?.updatedAt || task?.updatedAt || turn.updatedAt,
  )
  return startedAt == null || finishedAt == null
    ? 0
    : Math.max(0, finishedAt - startedAt)
}

function replayTurnStartedAt(
  turn: ScreenplayConversationTurn,
  operation?: ScreenplayOperationProjection,
  task?: ScreenplayAgentTask,
): number {
  const terminal = operation
    ? ['paused', 'succeeded', 'failed', 'canceled'].includes(operation.status)
    : ['paused', 'completed', 'failed', 'canceled'].includes(
      task?.status || turn.status,
    )
  const elapsed = terminal
    ? screenplayTurnDurationMs(turn, operation, task)
    : Math.max(0, Date.now() - (backendTimestampMs(turn.createdAt) ?? Date.now()))
  return performance.now() - elapsed
}

function turnTiming(
  turn: ScreenplayConversationTurn,
  operation?: ScreenplayOperationProjection,
  task?: ScreenplayAgentTask,
): Pick<AgentConversationMessage, 'durationMs' | 'turnStartedAt'> {
  const terminal = operation
    ? ['paused', 'succeeded', 'failed', 'canceled'].includes(operation.status)
    : ['paused', 'completed', 'failed', 'canceled'].includes(
      task?.status || turn.status,
    )
  return terminal
    ? { durationMs: screenplayTurnDurationMs(turn, operation, task) }
    : { turnStartedAt: replayTurnStartedAt(turn, operation, task) }
}

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

function screenplayDraftBatchScope(
  project: ScreenplayProject,
  documents: ScreenplayDocument[],
  episodes: ScreenplayDraftEpisode[] = [],
  documentEpisodes: ScreenplayDocumentEpisode[] = [],
): {
  pendingSceneCount: number
  pendingEpisodeCount: number
} {
  if (project.active_stage !== 'draft') {
    return {
      pendingSceneCount: 0,
      pendingEpisodeCount: 0,
    }
  }
  const sceneList = [...documents].reverse().find(
    (document) => document.kind === 'scene_list' && document.status === 'accepted',
  )
  const sceneEpisodes = documentEpisodes.filter((episode) => (
    episode.document_id === sceneList?.id
    && episode.document_kind === 'scene_list'
  ))
  const sceneIds = sceneEpisodes.flatMap((episode) => episode.item_ids.map(String))
  const completed = new Set(
    episodes.flatMap((episode) => episode.scene_ids).map(String),
  )
  const pendingSceneIds = sceneIds.filter((sceneId) => !completed.has(sceneId))
  const pendingEpisodeCount = sceneEpisodes.filter((episode) => (
    episode.item_ids.some((sceneId) => !completed.has(String(sceneId)))
  )).length
  return {
    pendingSceneCount: pendingSceneIds.length,
    pendingEpisodeCount,
  }
}

interface ScreenplayProposalActionPanelProps {
  artifact: ScreenplayTurnArtifact
  sourceCount: number
  showSources: boolean
  running: boolean
  accepting: boolean
  archived: boolean
  onView: () => void
  onApply: () => void
}

function ScreenplayProposalActionPanel({
  artifact,
  sourceCount,
  showSources,
  running,
  accepting,
  archived,
  onView,
  onApply,
}: ScreenplayProposalActionPanelProps) {
  const current = artifact.status === 'current'
  const statusLabel = current
    ? '已应用'
    : artifact.status === 'historical'
      ? '历史版本'
      : '待应用'
  const kindLabel = artifact.kind
    ? DOCUMENT_KIND_LABELS[artifact.kind]
    : DELIVERABLE_ROLE_LABELS[artifact.role]
  return (
    <div className="screenplay-agent-result">
      <div className="screenplay-agent-result__title">
        <span>
          <CheckCircleIcon />
          本轮产物
        </span>
        {showSources && sourceCount > 0 ? (
          <span className="screenplay-agent-result__sources">
            {sourceCount} 条可追溯来源
          </span>
        ) : null}
      </div>
      <article className="screenplay-document-proposal">
        <header>
          <div>
            <span className="screenplay-source-eyebrow">FORMAL PROPOSAL</span>
            <h3>{artifact.title}</h3>
            <span>
              {kindLabel}
              {artifact.revisionNo != null ? ` · v${artifact.revisionNo}` : ''}
              {' · '}
              {current
                ? '已应用到项目'
                : artifact.status === 'historical'
                  ? '曾应用，可重新使用'
                  : '候选已就绪，等待应用'}
            </span>
          </div>
          <span className={`screenplay-document-status ${
            current
              ? 'is-accepted'
              : artifact.status === 'candidate'
                ? 'is-saved'
                : ''
          }`}>
            {statusLabel}
          </span>
        </header>
        <div className="screenplay-document-proposal__handoff">
          <FileTextIcon />
          <div>
            <strong>候选稿内容已写入项目版本库</strong>
            <span>
              对话仅展示执行结论；需要审阅时再打开候选稿，不在消息中展开完整正文。
            </span>
          </div>
        </div>
        <footer>
          <span>
            应用会原子更新项目当前版本；若影响下游内容，系统会先请求确认。
          </span>
          <div>
            <PurrButton
              icon={<EyeIcon />}
              onClick={onView}
            >
              查看候选稿
            </PurrButton>
            <PurrButton
              type="primary"
              icon={<CheckCircleIcon />}
              loading={accepting}
              disabled={
                current
                || running
                || archived
              }
              onClick={onApply}
            >
              {current
                ? '已应用'
                : artifact.status === 'historical'
                  ? '重新应用'
                  : '应用此版本'}
            </PurrButton>
          </div>
        </footer>
      </article>
    </div>
  )
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
  const confirm = usePurrConfirm()
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
  const [projectWorkspace, setProjectWorkspace] = React.useState<ScreenplayV2Workspace | null>(null)
  const [projectDocuments, setProjectDocuments] = React.useState<ScreenplayDocument[]>([])
  const [documentEpisodes, setDocumentEpisodes] = React.useState<ScreenplayDocumentEpisode[]>([])
  const [selectedDocumentEpisode, setSelectedDocumentEpisode] = React.useState<ScreenplayDocumentEpisode | null>(null)
  const [documentEpisodeLoadingId, setDocumentEpisodeLoadingId] = React.useState<EntityId | null>(null)
  const [draftEpisodes, setDraftEpisodes] = React.useState<ScreenplayDraftEpisode[]>([])
  const [selectedDraftEpisode, setSelectedDraftEpisode] = React.useState<ScreenplayDraftEpisode | null>(null)
  const [draftEpisodeLoadingId, setDraftEpisodeLoadingId] = React.useState<EntityId | null>(null)
  const [projectSourceRefs, setProjectSourceRefs] = React.useState<ScreenplaySourceRef[]>([])
  const [selectedDocumentStages, setSelectedDocumentStages] = React.useState<
    Record<string, ScreenplayDocumentStage>
  >({})
  const [documentLibraryOpen, setDocumentLibraryOpen] = React.useState(false)
  const [revisionLibraryOpen, setRevisionLibraryOpen] = React.useState(false)
  const [revisionLibrarySelection, setRevisionLibrarySelection] = React.useState<
    RevisionLibraryTarget | null
  >(null)
  const [draftRangeModalOpen, setDraftRangeModalOpen] = React.useState(false)
  const [customDraftEpisodeCount, setCustomDraftEpisodeCount] = React.useState<number | null>(2)
  const [projectLoading, setProjectLoading] = React.useState(false)
  const [selectedModelId, setSelectedModelId] = React.useState(
    getStoredScreenplayAgentModelId,
  )
  const [agentPrompt, setAgentPrompt] = React.useState('')
  const [agentSubmitting, setAgentSubmitting] = React.useState(false)
  const [agentResumeSubmitting, setAgentResumeSubmitting] = React.useState(false)
  const [agentCancelSubmitting, setAgentCancelSubmitting] = React.useState(false)
  const [agentCancelHeld, setAgentCancelHeld] = React.useState(false)
  const [agentConversationState, setAgentConversationState] = React.useState<
    ScreenplayConversationState | null
  >(null)
  const [agentChunkVersion, setAgentChunkVersion] = React.useState(0)
  const [agentQueuedSubmissions, setAgentQueuedSubmissions] = React.useState<
    ScreenplayQueuedSubmission[]
  >([])
  const [agentQueueDraining, setAgentQueueDraining] = React.useState(false)
  const [agentSessionId, setAgentSessionId] = React.useState<number | null>(null)
  const [agentSessions, setAgentSessions] = React.useState<AiSession[]>([])
  const [agentSessionLoading, setAgentSessionLoading] = React.useState(false)
  const [agentChunkHydrating, setAgentChunkHydrating] = React.useState(false)
  const [agentLoadInitializing, setAgentLoadInitializing] = React.useState(false)
  const [agentConversationIdentity, setAgentConversationIdentity] = React.useState(
    'screenplay-session:none:0',
  )
  const [acceptingAgentRevisionId, setAcceptingAgentRevisionId] = React.useState<
    EntityId | null
  >(null)
  const [reviewMutationPending, setReviewMutationPending] = React.useState(false)
  const [reviewAdjudicationOpen, setReviewAdjudicationOpen] = React.useState(false)
  const [exportFormat, setExportFormat] = React.useState<
    'fountain' | 'markdown' | 'txt' | 'pdf' | 'json'
  >('fountain')
  const [exportingScreenplay, setExportingScreenplay] = React.useState(false)
  const [selectedDocument, setSelectedDocument] = React.useState<ScreenplayDocument | null>(null)
  const [comparisonDocument, setComparisonDocument] = React.useState<ScreenplayDocument | null>(null)
  const [updatingProjectStatus, setUpdatingProjectStatus] = React.useState(false)
  const activeAgentSessionRef = React.useRef<number | null>(null)
  const agentPromptRef = React.useRef('')
  const agentSessionLifecycleRef = React.useRef(
    createScreenplayConversationSessionLifecycle(),
  )
  const activeAgentLoadTokenRef = React.useRef<
    ScreenplayConversationLoadToken | undefined
  >(undefined)
  const agentConversationStateRef = React.useRef<ScreenplayConversationState | null>(null)
  const agentChunkReplayRef = React.useRef(new AgentChunkReplay())
  const operationCommandLatchRef = React.useRef(
    createScreenplayOperationCommandLatch(),
  )
  const agentChunkCursorRef = React.useRef(0)
  const agentChunkRenderPendingRef = React.useRef(false)
  const agentChunkReplayCaughtUpRef = React.useRef(true)
  const conversationPollErrorRef = React.useRef('')
  const reconciledConversationTurnRef = React.useRef({ scope: '', key: '' })
  const diagnosticRunMonitorsRef = React.useRef(new Map<string, () => void>())
  const completedDiagnosticRunIdsRef = React.useRef(new Set<string>())
  const conversationClient = React.useMemo(
    () => new ScreenplayConversationClient(services.screenplay),
    [],
  )
  const agentMessages = React.useMemo<AgentConversationMessage[]>(() => (
    agentConversationState?.messages.map((entry) => {
      const turn = agentConversationState.turns.find((item) => item.id === entry.turnId)
      const task = agentConversationState.tasks.find((item) => item.turnId === entry.turnId)
      const operation = agentConversationState.operations.find(
        (item) => item.turnId === entry.turnId,
      )
      const streamed = entry.role === 'assistant'
        ? agentChunkReplayRef.current.assistant(entry.turnId)
        : undefined
      const canonical: AgentConversationMessage = {
        role: entry.role,
        content: entry.content,
        clientTurnId: entry.turnId,
        sentAt: entry.createdAt || undefined,
        agentRunId: entry.runId || undefined,
        model: entry.model || undefined,
        isError: entry.status === 'failed',
        error: entry.status === 'failed'
          ? entry.error?.message || '本轮剧本对话执行失败'
          : undefined,
        termination: entry.status === 'canceled' ? '已终止' : undefined,
        ...(entry.role === 'assistant' && turn
          ? turnTiming(turn, operation, task)
          : {}),
      }
      return streamed
        ? {
            ...canonical,
            ...streamed,
            // Durable Operations publish their formal answer only through the
            // atomic finalization projection. Stream replay remains a work log.
            content: operation
              ? canonical.content
              : streamed.content.trim() ? streamed.content : canonical.content,
            agentRunId: canonical.agentRunId || streamed.agentRunId,
            durationMs: canonical.durationMs ?? streamed.durationMs,
            turnStartedAt: canonical.durationMs == null
              ? streamed.turnStartedAt ?? canonical.turnStartedAt
              : undefined,
            isError: canonical.isError,
            error: canonical.error,
            termination: canonical.termination,
          }
        : canonical
    }) ?? []
  ), [agentChunkVersion, agentConversationState])
  const activeConversationTask = React.useMemo(() => (
    [...(agentConversationState?.tasks ?? [])].reverse().find(
      (task) => task.status === 'queued' || task.status === 'running',
    ) ?? null
  ), [agentConversationState])
  const activeConversationOperation = React.useMemo(() => (
    [...(agentConversationState?.operations ?? [])].reverse().find(
      (operation) => operation.status === 'queued' || operation.status === 'running',
    ) ?? null
  ), [agentConversationState])
  const cancellableConversationOperation = React.useMemo(() => (
    [...(agentConversationState?.operations ?? [])].reverse().find(
      isScreenplayOperationCancellable,
    ) ?? null
  ), [agentConversationState])
  const cancelPendingConversationOperation = React.useMemo(() => (
    [...(agentConversationState?.operations ?? [])].reverse().find(
      (operation) => Boolean(
        operation.cancelRequestedAt
        && ['queued', 'running', 'paused'].includes(operation.status),
      ),
    ) ?? null
  ), [agentConversationState])
  const activeConversationTurn = React.useMemo(() => (
    [...(agentConversationState?.turns ?? [])].reverse().find(
      (turn) => turn.status === 'queued' || turn.status === 'planning',
    ) ?? agentConversationState?.turns.find(
      (turn) => turn.id === (
        activeConversationOperation?.turnId || activeConversationTask?.turnId
      ),
    ) ?? null
  ), [activeConversationOperation, activeConversationTask, agentConversationState])
  const latestConversationTurn = agentConversationState?.turns.at(-1) ?? null
  const latestConversationTask = latestConversationTurn
    ? agentConversationState?.tasks.find(
      (task) => task.turnId === latestConversationTurn.id,
    )
    : undefined
  const latestConversationOperation = latestConversationTurn
    ? agentConversationState?.operations.find(
      (operation) => operation.turnId === latestConversationTurn.id,
    )
    : undefined
  const pausedConversationOperation = latestConversationOperation?.status === 'paused'
    && !latestConversationOperation.cancelRequestedAt
    && !latestConversationOperation.cancelReceiptId
      ? latestConversationOperation
      : null

  React.useEffect(() => {
    for (const operation of agentConversationState?.operations ?? []) {
      operationCommandLatchRef.current.observeOperation(operation)
    }
    setAgentCancelHeld(operationCommandLatchRef.current.hasPendingCancel())
  }, [agentConversationState?.operations])

  const agentRunning = activeConversationTurn != null
    || activeConversationOperation != null

  React.useEffect(() => {
    activeAgentSessionRef.current = agentSessionId
  }, [agentSessionId])

  React.useEffect(() => {
    agentPromptRef.current = agentPrompt
  }, [agentPrompt])

  const updateAgentPrompt = React.useCallback((
    value: React.SetStateAction<string>,
  ) => {
    setAgentPrompt((current) => {
      const next = typeof value === 'function' ? value(current) : value
      agentPromptRef.current = next
      const token = agentSessionLifecycleRef.current.currentToken()
      if (token) agentSessionLifecycleRef.current.setDraft(token, next)
      return next
    })
  }, [])

  React.useEffect(() => {
    const token = activeAgentLoadTokenRef.current
    if (
      !token
      || agentSessionLoading
      || agentChunkHydrating
      || agentConversationState?.sessionId !== token.sessionId
    ) return
    if (agentSessionLifecycleRef.current.finishLoad(token)) {
      setAgentLoadInitializing(false)
    }
  }, [agentChunkHydrating, agentConversationState, agentSessionLoading])

  React.useEffect(() => {
    operationCommandLatchRef.current.clear()
    setAgentCancelHeld(false)
  }, [agentSessionId, openedProject?.id])

  React.useEffect(() => {
    agentConversationStateRef.current = agentConversationState
  }, [agentConversationState])

  const monitorDiagnosticRun = React.useCallback((input: {
    runId: string
    turnId: string
    prompt: string
  }) => {
    if (
      !import.meta.env.DEV
      || !input.runId
      || diagnosticRunMonitorsRef.current.has(input.runId)
      || completedDiagnosticRunIdsRef.current.has(input.runId)
    ) return
    let stopped = false
    let terminal = false
    const cancel = () => {
      stopped = true
    }
    diagnosticRunMonitorsRef.current.set(input.runId, cancel)
    void (async () => {
      let after = 0
      while (!stopped) {
        const result = await services.ai.getAgentRunSnapshot({
          runId: input.runId,
          after,
          limit: 500,
        })
        if (!result.success || !result.data) {
          throw new Error(result.error || '读取剧本 Agent 诊断失败')
        }
        hydrateAiDebugRunSnapshot({
          snapshot: result.data,
          turnId: input.turnId,
          prompt: input.prompt,
          source: '剧本 Agent 对话',
        })
        if (result.data.nextCursor > after) {
          after = result.data.nextCursor
        } else if (result.data.hasMore) {
          throw new Error('剧本 Agent 诊断事件游标没有前进')
        }
        if (result.data.hasMore) continue
        terminal = ['done', 'failed', 'canceled', 'blocked'].includes(
          result.data.run.status,
        )
        if (terminal) break
        await new Promise((resolve) => window.setTimeout(resolve, 200))
      }
    })().catch(() => undefined).finally(() => {
      if (diagnosticRunMonitorsRef.current.get(input.runId) === cancel) {
        diagnosticRunMonitorsRef.current.delete(input.runId)
      }
      if (terminal) completedDiagnosticRunIdsRef.current.add(input.runId)
    })
  }, [])

  React.useEffect(() => {
    if (!import.meta.env.DEV) return
    for (const turn of agentConversationState?.turns ?? []) {
      const task = agentConversationState?.tasks.find((item) => item.turnId === turn.id)
      const runIds = new Set([
        turn.rootRunId,
        ...modelRunIds(task),
      ].filter((runId): runId is string => Boolean(runId?.trim())))
      for (const runId of runIds) {
        monitorDiagnosticRun({
          runId,
          turnId: turn.id,
          prompt: turn.userContent,
        })
      }
    }
  }, [agentConversationState, monitorDiagnosticRun])

  React.useEffect(() => () => {
    diagnosticRunMonitorsRef.current.forEach((cancel) => cancel())
    diagnosticRunMonitorsRef.current.clear()
  }, [agentSessionId])

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

  const loadProjects = React.useCallback(async () => {
    setProjectsLoading(true)
    try {
      const result = await services.screenplay.listScreenplayProjects({
        includeArchived: true,
      })
      if (result.success && Array.isArray(result.data)) {
        setProjects(result.data.map(projectFromV2Project))
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
      const expectedProjectRevision = renameProjectTarget.revision
      if (!expectedProjectRevision) {
        message.error('项目版本状态缺失，请重新打开项目后重试')
        return
      }
      const request = {
        commandId: createScreenplayCommandId('rename-project'),
        projectId: renameProjectTarget.id,
        expectedProjectRevision,
        title,
      }
      let result = await services.screenplay.updateScreenplayV2Project(request)
      if (!result.success) {
        result = await services.screenplay.updateScreenplayV2Project(request)
      }
      if (!result.success || !result.data) {
        message.error(result.error || '重命名项目失败')
        return
      }
      applyProjectUpdate(projectFromWorkspace(renameProjectTarget, result.data))
      if (openedProject?.id === renameProjectTarget.id) {
        setProjectWorkspace(result.data)
      }
      setRenameProjectTarget(null)
      setRenameProjectTitle('')
      message.success('项目名称已更新')
    } finally {
      setProjectMutationId(null)
    }
  }, [
    applyProjectUpdate,
    message,
    openedProject?.id,
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
      const expectedProjectRevision = project.revision
      if (!expectedProjectRevision) {
        message.error('项目版本状态缺失，请重新打开项目后重试')
        return
      }
      const mutate = nextStatus === 'archived'
        ? services.screenplay.archiveScreenplayV2Project
        : services.screenplay.restoreScreenplayV2Project
      const request = {
        commandId: createScreenplayCommandId(`${nextStatus}-project`),
        projectId: project.id,
        expectedProjectRevision,
      }
      let result = await mutate(request)
      if (!result.success) result = await mutate(request)
      if (!result.success || !result.data) {
        message.error(result.error || '更新项目状态失败')
        return
      }
      applyProjectUpdate(projectFromWorkspace(project, result.data))
      if (openedProject?.id === project.id) {
        setProjectWorkspace(result.data)
      }
      message.success(nextStatus === 'archived' ? '项目已归档' : '项目已恢复')
    } finally {
      setProjectMutationId(null)
    }
  }, [applyProjectUpdate, message, openedProject?.id, projectMutationId])

  const deleteProject = React.useCallback(async () => {
    if (!deleteProjectTarget || projectMutationId != null) return
    const projectId = deleteProjectTarget.id
    setProjectMutationId(projectId)
    try {
      let result
      if (!deleteProjectTarget.revision) {
        result = { success: false, error: '项目版本状态缺失，请重新打开项目后重试' }
      } else {
        const request = {
          commandId: createScreenplayCommandId('delete-project'),
          projectId,
          expectedProjectRevision: deleteProjectTarget.revision,
        }
        result = await services.screenplay.deleteScreenplayV2Project(request)
        if (!result.success) {
          result = await services.screenplay.deleteScreenplayV2Project(request)
        }
      }
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
		setOpenedProject(null)
		setProjectWorkspace(null)
		setRevisionLibraryOpen(false)
		setRevisionLibrarySelection(null)
		setProjectDocuments([])
        setDocumentEpisodes([])
        setDraftEpisodes([])
        setProjectSourceRefs([])
        setAgentSessions([])
        setAgentSessionId(null)
        setAgentConversationState(null)
        agentConversationStateRef.current = null
        agentChunkReplayRef.current.reset()
        agentChunkCursorRef.current = 0
        setAgentChunkVersion((current) => current + 1)
        setAgentQueuedSubmissions((current) => current.filter(
          (submission) => submission.projectId !== projectId,
        ))
        setAgentPrompt('')
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
    agentSessionLifecycleRef.current.invalidate()
    activeAgentLoadTokenRef.current = undefined
    setAgentLoadInitializing(false)
    setAgentConversationIdentity('screenplay-session:none:0')
    setBriefStepIndex(0)
    setFurthestBriefStepIndex(0)
    setLaunchDraft(null)
    setOpenedProject(null)
    setProjectWorkspace(null)
    setRevisionLibraryOpen(false)
    setRevisionLibrarySelection(null)
    setProjectDocuments([])
    setDocumentEpisodes([])
    setDraftEpisodes([])
    setProjectSourceRefs([])
    setAgentSessionId(null)
    setAgentSessions([])
    setAgentConversationState(null)
    agentConversationStateRef.current = null
    agentChunkReplayRef.current.reset()
    agentChunkCursorRef.current = 0
    setAgentChunkVersion((current) => current + 1)
    conversationPollErrorRef.current = ''
    agentPromptRef.current = ''
    setAgentPrompt('')
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
    const workspaceResult = await services.screenplay.getScreenplayV2Workspace({
      projectId,
    })
    if (!workspaceResult.success || !workspaceResult.data) {
      setProjectDocuments([])
      setDocumentEpisodes([])
      setDraftEpisodes([])
      message.error(workspaceResult.error || '读取剧本版本失败')
      return null
    }
    const heads = Object.values(workspaceResult.data.workflow.heads).filter(
      (revision): revision is NonNullable<typeof revision> => revision != null,
    )
    const revisionResults = await Promise.all(heads.map((revision) => (
      services.screenplay.getScreenplayV2Revision({
        revisionId: revision.id,
        view: 'full',
      })
    )))
    const revisions = revisionResults.flatMap((result) => (
      result.success && result.data ? [result.data] : []
    ))
    const documents = revisions.map(documentFromRevision)
    setProjectDocuments(documents)
    setDocumentEpisodes(revisions.flatMap(documentEpisodesFromRevision))
    setDraftEpisodes(revisions.flatMap(draftEpisodesFromRevision))
    return documents
  }, [message])

  const loadProjectSourceRefs = React.useCallback(async (_projectId: EntityId) => {
    setProjectSourceRefs([])
    return []
  }, [])

  const applyProjectWorkspace = React.useCallback((workspace: ScreenplayV2Workspace) => {
    setProjectWorkspace(workspace)
    setOpenedProject((current) => (
      current?.id === workspace.project.id
        ? projectFromWorkspace(current, workspace)
        : current
    ))
    setProjects((current) => current.map((project) => (
      project.id === workspace.project.id
        ? projectFromWorkspace(project, workspace)
        : project
    )))
  }, [])

  const loadProjectWorkspace = React.useCallback(async (projectId: EntityId) => {
    const result = await services.screenplay.getScreenplayV2Workspace({ projectId })
    if (!result.success || !result.data) return null
    applyProjectWorkspace(result.data)
    return result.data
  }, [applyProjectWorkspace])

  const applyRevisionWorkspace = React.useCallback(async (
    workspace: ScreenplayV2Workspace,
  ) => {
    applyProjectWorkspace(workspace)
    await Promise.all([
      loadProjectDocuments(workspace.project.id),
      loadProjectSourceRefs(workspace.project.id),
    ])
  }, [applyProjectWorkspace, loadProjectDocuments, loadProjectSourceRefs])

  const runtimeForModel = React.useCallback((model: AiModelConfig) => {
    const { options } = buildStreamOptions({
      cfg: model,
      selectedModel: model.id,
    })
    return {
      apiKey: model.apiKey,
      baseURL: model.baseUrl || undefined,
      apiProvider: normalizeApiProvider(model.apiProvider),
      locale: document.documentElement.lang || 'zh-CN',
      options,
      contextWindow: options.context_window,
    }
  }, [])

  const loadAgentSession = React.useCallback(async (
    sessionId: number,
    project: ScreenplayProject,
  ) => {
    const lifecycle = agentSessionLifecycleRef.current
    const previous = lifecycle.currentToken()
    if (previous) lifecycle.setDraft(previous, agentPromptRef.current)
    const loadToken = lifecycle.beginLoad(project.id, sessionId)
    activeAgentLoadTokenRef.current = loadToken
    setAgentConversationIdentity(loadToken.identity)
    setAgentLoadInitializing(true)
    setAgentSessionLoading(true)
    setAgentChunkHydrating(true)
    agentChunkReplayCaughtUpRef.current = false
    agentChunkRenderPendingRef.current = false
    activeAgentSessionRef.current = sessionId
    setAgentSessionId(sessionId)
    const restoredDraft = lifecycle.getDraft(loadToken)
    agentPromptRef.current = restoredDraft
    setAgentPrompt(restoredDraft)
    storeScreenplayAgentSessionId(project.id, sessionId)
    setAgentConversationState(null)
    agentConversationStateRef.current = null
    agentChunkReplayRef.current.reset()
    agentChunkCursorRef.current = 0
    setAgentChunkVersion((current) => current + 1)
    try {
      const next = await conversationClient.load(project.id, sessionId)
      if (!lifecycle.isCurrent(loadToken)) return
      agentConversationStateRef.current = next
      setAgentConversationState(next)
    } catch (error) {
      if (!lifecycle.isCurrent(loadToken)) return
      agentChunkReplayCaughtUpRef.current = true
      setAgentChunkHydrating(false)
      message.error(error instanceof Error ? error.message : '读取剧本 Agent 对话失败')
    } finally {
      if (lifecycle.isCurrent(loadToken)) setAgentSessionLoading(false)
    }
  }, [conversationClient, message])

  const openProject = React.useCallback(async (project: ScreenplayProject) => {
    agentSessionLifecycleRef.current.invalidate()
    activeAgentLoadTokenRef.current = undefined
    setAgentLoadInitializing(true)
    setLastOpenedProjectId(project.id)
    storeLastOpenedScreenplayProjectId(project.id)
    setOpenedProject(project)
    setProjectWorkspace(null)
    setRevisionLibraryOpen(false)
    setRevisionLibrarySelection(null)
    setProjectDocuments([])
    setDocumentEpisodes([])
    setDraftEpisodes([])
    setProjectSourceRefs([])
    setAgentSessionId(null)
    setAgentSessions([])
    setAgentConversationState(null)
    agentConversationStateRef.current = null
    agentChunkReplayRef.current.reset()
    agentChunkCursorRef.current = 0
    agentChunkReplayCaughtUpRef.current = false
    agentChunkRenderPendingRef.current = false
    setAgentChunkHydrating(true)
    setAgentChunkVersion((current) => current + 1)
    agentPromptRef.current = ''
    setAgentPrompt('')
    setProjectLoading(true)
    setStage('project')
    try {
      const [documents, , sessionResult, workspace] = await Promise.all([
        loadProjectDocuments(project.id),
        loadProjectSourceRefs(project.id),
        services.screenplay.getOrCreateScreenplaySession({
          projectId: project.id,
        }),
        loadProjectWorkspace(project.id),
      ])
      const effectiveProject = workspace
        ? {
            ...project,
            active_stage: workspace.workflow.stage,
            status: workspace.project.lifecycle === 'archived'
              ? 'archived' as const
              : 'active' as const,
          }
        : project
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
          await loadAgentSession(targetSession.id, effectiveProject)
        }
      } else {
        agentChunkReplayCaughtUpRef.current = true
        setAgentChunkHydrating(false)
        message.error(sessionResult.error || '初始化剧本 Agent 会话失败')
      }
    } finally {
      setProjectLoading(false)
    }
  }, [
    loadAgentSession,
    loadProjectDocuments,
    loadProjectSourceRefs,
    loadProjectWorkspace,
    message,
  ])

  const openedProjectId = openedProject?.id ?? null

  React.useEffect(() => {
    if (openedProjectId == null || agentSessionId == null) return undefined
    let stopped = false
    let fallbackTimer: ReturnType<typeof setTimeout> | null = null
    let wakeTimer: ReturnType<typeof setTimeout> | null = null
    let stopWatching: (() => void) | null = null
    let polling = false
    let rerun = false
    const reconciliationScope = `${openedProjectId}:${agentSessionId}`
    if (reconciledConversationTurnRef.current.scope !== reconciliationScope) {
      reconciledConversationTurnRef.current = {
        scope: reconciliationScope,
        key: '',
      }
    }

    const wake = () => {
      if (stopped) return
      if (polling) {
        rerun = true
        return
      }
      if (wakeTimer) return
      if (fallbackTimer) {
        clearTimeout(fallbackTimer)
        fallbackTimer = null
      }
      wakeTimer = setTimeout(() => {
        wakeTimer = null
        void poll()
      }, 80)
    }

    const poll = async () => {
      if (polling) {
        rerun = true
        return
      }
      polling = true
      try {
        const current = agentConversationStateRef.current
        const next = current && current.sessionId === agentSessionId
          ? await conversationClient.refresh(current)
          : await conversationClient.load(openedProjectId, agentSessionId)
        if (stopped || activeAgentSessionRef.current !== agentSessionId) return
        conversationPollErrorRef.current = ''
        if (next !== current) {
          agentConversationStateRef.current = next
          setAgentConversationState(next)
        }
        if (!stopWatching) {
          stopWatching = conversationClient.watch(next, {
            chunkAfter: agentChunkCursorRef.current,
            onInvalidate: wake,
            onChunks: (page) => {
              if (stopped || activeAgentSessionRef.current !== agentSessionId) return
              let changed = false
              for (const event of page.chunks) {
                if (event.cursor <= agentChunkCursorRef.current) continue
                const state = agentConversationStateRef.current
                const turn = state?.turns.find((item) => item.id === event.turnId)
                const task = state?.tasks.find((item) => item.turnId === event.turnId)
                const operation = state?.operations.find(
                  (item) => item.turnId === event.turnId,
                )
                const modelName = event.model || turn?.runtimeProfile.model || ''
                const cfg = modelConfigs.find((item) => item.name === modelName)
                  || modelConfigs.find((item) => item.id === selectedModelId)
                  || modelConfigs[0]
                  || {
                    id: 'screenplay-agent-stream',
                    name: modelName || 'screenplay-agent',
                    supportsThinking: true,
                    thinkingOnly: false,
                    apiKey: '',
                    baseUrl: '',
                  }
                const createdAt = backendTimestampMs(
                  turn?.createdAt || event.turnCreatedAt,
                )
                const chunk = event.chunk as AiStreamChunk
                agentChunkReplayRef.current.dispatch({
                  turnId: event.turnId,
                  rootRunId: turn?.rootRunId || undefined,
                  sessionId: agentSessionId,
                  userContent: event.userContent || turn?.userContent || '',
                  model: modelName || undefined,
                  turnStartedAt: turn
                    ? replayTurnStartedAt(turn, operation, task)
                    : performance.now() - Math.max(
                        0,
                        Date.now() - (createdAt ?? Date.now()),
                      ),
                }, chunk, {
                  cfg,
                  appMessage: message,
                })
                if (import.meta.env.DEV && event.runId) {
                  recordScreenplayAiDebugChunk({
                    runId: event.runId,
                    turnId: event.turnId,
                    sessionId: agentSessionId,
                    prompt: event.userContent || turn?.userContent || '',
                    model: modelName || undefined,
                    chunk: event.chunk as AiDebugChunk,
                  })
                  monitorDiagnosticRun({
                    runId: event.runId,
                    turnId: event.turnId,
                    prompt: event.userContent || turn?.userContent || '',
                  })
                }
                agentChunkCursorRef.current = event.cursor
                const terminalReplay = operation
                  ? ['paused', 'succeeded', 'failed', 'canceled'].includes(
                    operation.status,
                  )
                  : turn && ['paused', 'completed', 'failed', 'canceled'].includes(
                    task?.status || turn.status,
                  )
                changed = (
                  screenplayChunkChangesConversation(chunk)
                  && (!terminalReplay || Boolean(chunk.done || chunk.error))
                ) || changed
              }
              agentChunkCursorRef.current = Math.max(
                agentChunkCursorRef.current,
                page.nextCursor,
              )
              if (!agentChunkReplayCaughtUpRef.current) {
                agentChunkRenderPendingRef.current = (
                  agentChunkRenderPendingRef.current || changed
                )
                if (!page.hasMore) {
                  agentChunkReplayCaughtUpRef.current = true
                  setAgentChunkHydrating(false)
                  if (agentChunkRenderPendingRef.current) {
                    setAgentChunkVersion((current) => current + 1)
                  }
                  agentChunkRenderPendingRef.current = false
                }
                return
              }
              if (changed) setAgentChunkVersion((current) => current + 1)
            },
          })
        }
        const latest = next.turns.at(-1)
        const latestTask = latest
          ? next.tasks.find((task) => task.turnId === latest.id)
          : undefined
        const latestOperation = latest
          ? next.operations.find((operation) => operation.turnId === latest.id)
          : undefined
        const reconciliationKey = screenplayTurnReconciliationKey(
          latest,
          latestOperation,
          latestTask,
        )
        if (
          reconciliationKey
          && reconciliationKey !== reconciledConversationTurnRef.current.key
        ) {
          reconciledConversationTurnRef.current.key = reconciliationKey
          void Promise.all([
            loadProjectWorkspace(openedProjectId),
            loadProjectDocuments(openedProjectId),
            loadProjectSourceRefs(openedProjectId),
          ])
        }
      } catch (error) {
        if (!stopped) {
          const errorMessage = error instanceof Error
            ? error.message
            : '刷新剧本对话失败'
          if (conversationPollErrorRef.current !== errorMessage) {
            conversationPollErrorRef.current = errorMessage
            message.error(errorMessage)
          }
        }
      } finally {
        polling = false
        if (!stopped) {
          if (rerun) {
            rerun = false
            wake()
          } else {
            const state = agentConversationStateRef.current
            const active = state?.turns.some(
              (turn) => turn.status === 'queued' || turn.status === 'planning',
            ) || state?.operations.some(
              (operation) => ['queued', 'running'].includes(operation.status)
                || Boolean(
                  operation.cancelRequestedAt && operation.status === 'paused',
                ),
            )
            fallbackTimer = setTimeout(
              () => void poll(),
              active ? 2000 : 10000,
            )
          }
        }
      }
    }
    void poll()
    return () => {
      stopped = true
      stopWatching?.()
      if (fallbackTimer) clearTimeout(fallbackTimer)
      if (wakeTimer) clearTimeout(wakeTimer)
    }
  }, [
    agentSessionId,
    conversationClient,
    loadProjectDocuments,
    loadProjectSourceRefs,
    loadProjectWorkspace,
    message,
    modelConfigs,
    monitorDiagnosticRun,
    openedProjectId,
    selectedModelId,
  ])

  const switchAgentSession = React.useCallback((sessionId: number) => {
    if (
      !openedProject
      || agentSessionLoading
      || agentChunkHydrating
      || sessionId === agentSessionId
    ) return
    void loadAgentSession(sessionId, openedProject)
  }, [
    agentSessionId,
    agentChunkHydrating,
    agentSessionLoading,
    loadAgentSession,
    openedProject,
  ])

  const createAgentSession = React.useCallback(async () => {
    if (!openedProject || agentSessionLoading || agentChunkHydrating) return
    if (agentSessions.length > 0 && agentMessages.length === 0) return
    setAgentSessionLoading(true)
    try {
      const result = await services.screenplay.createScreenplaySession({
        commandId: createScreenplayCommandId('create-session'),
        projectId: openedProject.id,
      })
      if (!result.success || !result.data) {
        message.error(result.error || '新建对话失败')
        return
      }
      setAgentSessions((current) => [...current, result.data!])
      await loadAgentSession(result.data.id, openedProject)
    } finally {
      setAgentSessionLoading(false)
    }
  }, [
    agentMessages.length,
    agentChunkHydrating,
    agentSessionLoading,
    agentSessions.length,
    loadAgentSession,
    message,
    openedProject,
  ])

  const closeAgentSession = React.useCallback(async (session: AiSession) => {
    if (!openedProject || agentSessionLoading || agentChunkHydrating) return
    setAgentSessionLoading(true)
    try {
      const result = await services.sessions.setSessionClosed({ sessionId: session.id })
      if (!result.success) {
        message.error(result.error || '关闭对话失败')
        return
      }
      const remaining = agentSessions.filter((item) => item.id !== session.id)
      setAgentSessions(remaining)
      if (session.id !== agentSessionId) return
      const nextSession = remaining[remaining.length - 1]
      if (nextSession) {
        await loadAgentSession(nextSession.id, openedProject)
        return
      }
      const created = await services.screenplay.createScreenplaySession({
        commandId: createScreenplayCommandId('create-session'),
        projectId: openedProject.id,
      })
      if (created.success && created.data) {
        setAgentSessions([created.data])
        await loadAgentSession(created.data.id, openedProject)
      }
    } finally {
      setAgentSessionLoading(false)
    }
  }, [
    agentSessionId,
    agentChunkHydrating,
    agentSessionLoading,
    agentSessions,
    loadAgentSession,
    message,
    openedProject,
  ])

  const renameAgentSession = React.useCallback(async (
    sessionId: number,
    nextTitle: string,
  ) => {
    const title = nextTitle.trim()
    if (!title) {
      message.warning('对话名称不能为空')
      return
    }
    const result = await services.sessions.updateSessionTitle({
      sessionId,
      title,
    })
    if (result.success) {
      setAgentSessions((current) => current.map((session) => (
        session.id === sessionId ? { ...session, title } : session
      )))
    } else {
      message.error(result.error || '更新对话名称失败')
    }
  }, [message])

  const stopAgent = React.useCallback(async (): Promise<boolean> => {
    const targetOperation = cancellableConversationOperation
      || cancelPendingConversationOperation
    const targetTurnId = cancellableConversationOperation?.turnId
      || cancelPendingConversationOperation?.turnId
      || activeConversationTurn?.id
    if (!targetTurnId || !openedProject || agentSessionId == null) return true
    if (agentCancelSubmitting || agentResumeSubmitting) return false
    const commandToken = operationCommandLatchRef.current.tryAcquire(
      targetOperation?.id ?? `turn:${targetTurnId}`,
      targetOperation?.revision ?? 0,
      'cancel',
    )
    if (!commandToken) return false
    setAgentCancelSubmitting(true)
    let cancelAccepted = false
    try {
      if (!cancelPendingConversationOperation) {
        await conversationClient.cancel(
          createScreenplayCommandId('cancel-turn'),
          targetTurnId,
        )
        cancelAccepted = true
        if (targetOperation) {
          operationCommandLatchRef.current.holdUntilAuthoritative(commandToken)
          setAgentCancelHeld(true)
        }
      }
      const next = await conversationClient.load(openedProject.id, agentSessionId)
      for (const operation of next.operations) {
        operationCommandLatchRef.current.observeOperation(operation)
      }
      if (activeAgentSessionRef.current !== agentSessionId) return true
      agentConversationStateRef.current = next
      setAgentConversationState(next)
      void loadProjectWorkspace(openedProject.id)
      return true
    } catch (error) {
      message.error(error instanceof Error ? error.message : '终止剧本对话失败')
      return cancelAccepted
    } finally {
      setAgentCancelSubmitting(false)
      operationCommandLatchRef.current.release(commandToken)
    }
  }, [
    activeConversationTurn,
    agentSessionId,
    agentCancelSubmitting,
    agentResumeSubmitting,
    cancelPendingConversationOperation,
    cancellableConversationOperation,
    conversationClient,
    loadProjectWorkspace,
    message,
    openedProject,
  ])

  const resumeAgent = React.useCallback(async () => {
    if (
      !pausedConversationOperation
      || !openedProject
      || agentSessionId == null
      || agentResumeSubmitting
      || agentCancelSubmitting
      || agentCancelHeld
      || cancelPendingConversationOperation
    ) return
    const model = modelConfigs.find((item) => item.id === selectedModelId)
    if (!model?.apiKey?.trim()) {
      message.warning('请先在设置中添加可用模型')
      onOpenSettings()
      return
    }
    const commandToken = operationCommandLatchRef.current.tryAcquire(
      pausedConversationOperation.id,
      pausedConversationOperation.revision,
      'resume',
    )
    if (!commandToken) return
    setAgentResumeSubmitting(true)
    try {
      await conversationClient.resume(
        createScreenplayCommandId('resume-operation'),
        pausedConversationOperation.id,
        pausedConversationOperation.revision,
        runtimeForModel(model),
      )
      const next = await conversationClient.load(openedProject.id, agentSessionId)
      if (activeAgentSessionRef.current !== agentSessionId) return
      agentConversationStateRef.current = next
      setAgentConversationState(next)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '继续执行剧本任务失败')
    } finally {
      setAgentResumeSubmitting(false)
      operationCommandLatchRef.current.release(commandToken)
    }
  }, [
    agentCancelSubmitting,
    agentCancelHeld,
    agentResumeSubmitting,
    agentSessionId,
    cancelPendingConversationOperation,
    conversationClient,
    message,
    modelConfigs,
    onOpenSettings,
    openedProject,
    pausedConversationOperation,
    runtimeForModel,
    selectedModelId,
  ])

  const runAgent = React.useCallback(async (
    promptOverride?: string,
    editMessageIndex?: number,
    runtimeOverride?: ScreenplayConversationRuntimeInput,
    stageCommand?: ScreenplayStageCommand,
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
    const actionToken = agentSessionLifecycleRef.current.currentToken()
    if (
      !agentSessionLifecycleRef.current.canAct(actionToken)
      || actionToken?.projectId !== String(openedProject.id)
      || actionToken.sessionId !== agentSessionId
    ) return
    if (pausedConversationOperation) {
      message.info('当前任务已暂停，请先继续执行或停止任务')
      return
    }
    const prompt = (promptOverride ?? agentPrompt).trim()
    const consumesComposerPrompt = promptOverride == null
    if (!prompt) {
      message.warning('先告诉 Agent 这轮要解决什么')
      return
    }
    const model = modelConfigs.find((item) => item.id === selectedModelId)
    if (!runtimeOverride && !model?.apiKey?.trim()) {
      message.warning('请先在设置中添加可用模型')
      onOpenSettings()
      return
    }
    const runtime = runtimeOverride || runtimeForModel(model!)
    if (agentRunning || agentSubmitting) {
      if (typeof editMessageIndex === 'number') {
        message.info('当前对话仍在执行，完成后再编辑历史消息')
        return
      }
      setAgentQueuedSubmissions((current) => [...current, {
        id: createScreenplayCommandId('queued-turn'),
        projectId: openedProject.id,
        sessionId: agentSessionId,
        content: prompt,
        runtime,
        ...(stageCommand ? { stageCommand } : {}),
      }])
      if (consumesComposerPrompt) updateAgentPrompt('')
      message.info('已加入发送队列')
      return
    }

    setAgentSubmitting(true)
    if (consumesComposerPrompt) updateAgentPrompt('')
    try {
      if (typeof editMessageIndex === 'number') {
        const editedMessage = agentConversationState?.messages[editMessageIndex]
        if (!editedMessage || editedMessage.role !== 'user') {
          message.error('找不到要重新编辑的剧本对话消息')
          return
        }
        await conversationClient.truncateFromTurn(editedMessage.turnId)
        if (!agentSessionLifecycleRef.current.canAct(actionToken)) return
      }
      await conversationClient.submit({
        commandId: createScreenplayCommandId('submit-turn'),
        projectId: openedProject.id,
        sessionId: agentSessionId,
        content: prompt,
        runtime,
        ...(stageCommand ? { stageCommand } : {}),
      })
      if (!agentSessionLifecycleRef.current.isCurrent(actionToken)) return
      const next = await conversationClient.load(openedProject.id, agentSessionId)
      if (!agentSessionLifecycleRef.current.isCurrent(actionToken)) return
      agentConversationStateRef.current = next
      setAgentConversationState(next)
    } catch (error) {
      if (consumesComposerPrompt) {
        if (agentSessionLifecycleRef.current.isCurrent(actionToken)) {
          updateAgentPrompt((current) => current.trim() ? current : prompt)
        }
      }
      if (typeof editMessageIndex === 'number') {
        const next = await conversationClient.load(
          openedProject.id,
          agentSessionId,
        ).catch(() => null)
        if (next && agentSessionLifecycleRef.current.isCurrent(actionToken)) {
          agentConversationStateRef.current = next
          setAgentConversationState(next)
        }
      }
      message.error(error instanceof Error ? error.message : '提交剧本对话失败')
    } finally {
      setAgentSubmitting(false)
    }
  }, [
    agentRunning,
    agentPrompt,
    agentSessionId,
    agentSubmitting,
    agentConversationState,
    conversationClient,
    message,
    modelConfigs,
    onOpenSettings,
    openedProject,
    pausedConversationOperation,
    runtimeForModel,
    selectedModelId,
    updateAgentPrompt,
  ])

  React.useEffect(() => {
    if (
      agentRunning
      || agentSubmitting
      || agentQueueDraining
      || !openedProject
      || agentSessionId == null
    ) return
    const queued = agentQueuedSubmissions.find((submission) => (
      submission.projectId === openedProject.id
      && submission.sessionId === agentSessionId
    ))
    if (!queued) return
    setAgentQueueDraining(true)
    setAgentQueuedSubmissions((current) => current.filter(
      (submission) => submission.id !== queued.id,
    ))
    void runAgent(
      queued.content,
      undefined,
      queued.runtime,
      queued.stageCommand,
    ).finally(() => {
      setAgentQueueDraining(false)
    })
  }, [
    agentQueueDraining,
    agentQueuedSubmissions,
    agentRunning,
    agentSessionId,
    agentSubmitting,
    openedProject,
    runAgent,
  ])

  const acceptAgentRevision = React.useCallback(async (
    artifact: ScreenplayTurnArtifact,
  ) => {
    if (
      !openedProject
      || !projectWorkspace
      || acceptingAgentRevisionId != null
    ) return
    setAcceptingAgentRevisionId(artifact.revisionId)
    try {
      const workspaceForAccept = await loadProjectWorkspace(openedProject.id)
        ?? projectWorkspace
      if (
        workspaceForAccept.workflow.heads[artifact.role]?.id
        === artifact.revisionId
      ) {
        message.info('这个版本已经是当前版本')
        return
      }
      const commandId = createScreenplayCommandId('accept-revision')
      let accepted = await services.screenplay.acceptScreenplayV2Revision({
        commandId,
        projectId: openedProject.id,
        revisionId: artifact.revisionId,
        expectedProjectRevision: workspaceForAccept.project.revision,
      })
      if (!accepted.success || !accepted.data?.workspace) {
        const reconciled = await loadProjectWorkspace(openedProject.id)
        if (
          reconciled?.workflow.heads[artifact.role]?.id
          === artifact.revisionId
        ) {
          await loadProjectDocuments(openedProject.id)
          message.success('这个版本已经应用')
          return
        }
        if (accepted.error?.includes('下游版本失效')) {
          const confirmation = await confirm({
            title: '应用并重置下游版本？',
            content: '这个版本修改了上游基线。应用后，依赖旧基线的下游当前版本会同时失效，需要从新的阶段状态继续生成。',
            confirmText: '应用并重置',
            confirmVariant: 'danger',
            cancelText: '取消',
          })
          if (confirmation !== 'confirm') return
          accepted = await services.screenplay.acceptScreenplayV2Revision({
            commandId,
            projectId: openedProject.id,
            revisionId: artifact.revisionId,
            expectedProjectRevision: workspaceForAccept.project.revision,
            confirmInvalidation: true,
          })
        }
      }
      if (!accepted.success || !accepted.data?.workspace) {
        const reconciled = await loadProjectWorkspace(openedProject.id)
        if (
          reconciled?.workflow.heads[artifact.role]?.id
          === artifact.revisionId
        ) {
          await loadProjectDocuments(openedProject.id)
          message.success('这个版本已经应用')
          return
        }
        message.error(accepted.error || '应用版本失败')
        return
      }
      applyProjectWorkspace(accepted.data.workspace)
      const synchronizedProject = projectFromV2Project(
        accepted.data.workspace.project,
      )
      setOpenedProject(synchronizedProject)
      setProjects((current) => current.map((project) => (
        project.id === synchronizedProject.id ? synchronizedProject : project
      )))
      await Promise.all([
        loadProjectDocuments(openedProject.id),
        loadProjectSourceRefs(openedProject.id),
      ])
      message.success(
        accepted.data.workspace.workflow.stage !== workspaceForAccept.workflow.stage
          ? '已应用版本并推进项目阶段'
          : '已应用为当前剧本版本',
      )
    } finally {
      setAcceptingAgentRevisionId(null)
    }
  }, [
    acceptingAgentRevisionId,
    applyProjectWorkspace,
    confirm,
    loadProjectDocuments,
    loadProjectSourceRefs,
    loadProjectWorkspace,
    message,
    openedProject,
    projectWorkspace,
  ])

  const adjudicateReview = React.useCallback(async (
    issueIds: string[],
    status: ScreenplayV2ReviewFindingStatus,
    note: string,
  ): Promise<boolean> => {
    if (
      !openedProject
      || !projectWorkspace
      || reviewMutationPending
      || issueIds.length === 0
    ) return false
    setReviewMutationPending(true)
    try {
      const latestWorkspace = await loadProjectWorkspace(openedProject.id)
        ?? projectWorkspace
      const reviewRevisionId = latestWorkspace.workflow.review.reviewRevisionId
      if (!reviewRevisionId) {
        message.error('当前没有可处理的审阅报告')
        return false
      }
      const result = await services.screenplay.adjudicateScreenplayV2Review({
        commandId: createScreenplayCommandId('adjudicate-review'),
        projectId: openedProject.id,
        expectedProjectRevision: latestWorkspace.project.revision,
        reviewRevisionId,
        decisions: issueIds.map((issueId) => ({ issueId, status, note })),
      })
      if (!result.success || !result.data) {
        await loadProjectWorkspace(openedProject.id)
        message.error(result.error || '处理审阅意见失败')
        return false
      }
      await applyRevisionWorkspace(result.data)
      message.success(`已处理 ${issueIds.length} 条审阅意见`)
      return true
    } finally {
      setReviewMutationPending(false)
    }
  }, [
    applyRevisionWorkspace,
    loadProjectWorkspace,
    message,
    openedProject,
    projectWorkspace,
    reviewMutationPending,
  ])

  const finalizeProject = React.useCallback(async () => {
    if (!openedProject || !projectWorkspace || reviewMutationPending) return
    const latestWorkspace = await loadProjectWorkspace(openedProject.id)
      ?? projectWorkspace
    const reviewState = latestWorkspace.workflow.review
    if (
      !reviewState.canFinalize
      || !reviewState.draftRevisionId
      || !reviewState.reviewRevisionId
    ) {
      message.warning('当前剧本尚未满足定稿条件')
      return
    }
    const decision = await confirm({
      title: '确认当前剧本定稿？',
      content: `本次定稿包含 ${reviewState.counts.resolved} 条已解决、${reviewState.counts.dismissed} 条不成立和 ${reviewState.counts.riskAccepted} 条接受风险的审阅裁决。定稿后仍可查看完整记录。`,
      confirmText: '确认定稿',
      confirmVariant: 'primary',
      cancelText: '继续检查',
    })
    if (decision !== 'confirm') return
    setReviewMutationPending(true)
    try {
      const result = await services.screenplay.finalizeScreenplayV2Project({
        commandId: createScreenplayCommandId('finalize-project'),
        projectId: openedProject.id,
        expectedProjectRevision: latestWorkspace.project.revision,
        draftRevisionId: reviewState.draftRevisionId,
        reviewRevisionId: reviewState.reviewRevisionId,
      })
      if (!result.success || !result.data) {
        await loadProjectWorkspace(openedProject.id)
        message.error(result.error || '确认定稿失败')
        return
      }
      await applyRevisionWorkspace(result.data)
      message.success('剧本已由你确认定稿')
    } finally {
      setReviewMutationPending(false)
    }
  }, [
    applyRevisionWorkspace,
    confirm,
    loadProjectWorkspace,
    message,
    openedProject,
    projectWorkspace,
    reviewMutationPending,
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
      if (!acceptedScreenplayDraft.content_text.trim()) {
        message.error('当前剧本版本没有可导出的正文')
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
      const commandId = createScreenplayCommandId('create-project')
      const request = {
        commandId,
        title: launchDraft.projectTitle,
        format: screenplayFormatToV2(launchDraft.format),
        source: screenplaySourceToV2(
          launchDraft.sourceKind,
          launchDraft.sourceBookId,
          launchDraft.sourceScope,
        ),
        brief: {
          approach: launchDraft.approach,
          premise: launchDraft.premise,
        },
      }
      let result = await services.screenplay.createScreenplayV2Project(request)
      if (!result.success || !result.data) {
        // The command is idempotent. Retrying with the same key reconciles a
        // response lost after the server committed the project.
        result = await services.screenplay.createScreenplayV2Project(request)
      }
      if (!result.success || !result.data) {
        message.error(result.error || '创建剧本项目失败')
        return
      }
      const project = projectFromV2Project(result.data.project)
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

  const openDocument = React.useCallback(async (
    document: ScreenplayDocument,
    compareWithPrevious = false,
  ) => {
    if (document.kind === 'scene_draft') return
    const selected = document
    setSelectedDocument(selected)
    const previous = compareWithPrevious
      ? previousDocumentVersion(selected, projectDocuments)
      : null
    if (!previous) {
      setComparisonDocument(null)
      return
    }
    setComparisonDocument(previous)
  }, [projectDocuments])

  const openDraftEpisode = React.useCallback(async (
    episode: ScreenplayDraftEpisode,
  ) => {
    if (!openedProject || draftEpisodeLoadingId != null) return
    setDraftEpisodeLoadingId(episode.id)
    try {
      setSelectedDraftEpisode(episode)
    } finally {
      setDraftEpisodeLoadingId(null)
    }
  }, [draftEpisodeLoadingId, message, openedProject])

  const openDocumentEpisode = React.useCallback(async (
    episode: ScreenplayDocumentEpisode,
  ) => {
    if (documentEpisodeLoadingId != null) return
    setDocumentEpisodeLoadingId(episode.id)
    try {
      setSelectedDocumentEpisode(episode)
    } finally {
      setDocumentEpisodeLoadingId(null)
    }
  }, [documentEpisodeLoadingId, message])

  const toggleProjectArchived = React.useCallback(async () => {
    if (
      !openedProject
      || !projectWorkspace
      || updatingProjectStatus
    ) return
    const nextStatus = openedProject.status === 'archived' ? 'active' : 'archived'
    setUpdatingProjectStatus(true)
    try {
      if (nextStatus === 'archived' && agentRunning) {
        if (!await stopAgent()) return
      }
      const mutate = nextStatus === 'archived'
        ? services.screenplay.archiveScreenplayV2Project
        : services.screenplay.restoreScreenplayV2Project
      const request = {
        commandId: createScreenplayCommandId(`${nextStatus}-project`),
        projectId: openedProject.id,
        expectedProjectRevision: projectWorkspace.project.revision,
      }
      let result = await mutate(request)
      if (!result.success) result = await mutate(request)
      if (!result.success || !result.data) {
        message.error(result.error || '更新项目状态失败')
        return
      }
      applyProjectWorkspace(result.data)
      message.success(nextStatus === 'archived' ? '项目已归档并切换为只读' : '项目已恢复，可以继续创作')
    } finally {
      setUpdatingProjectStatus(false)
    }
  }, [
    agentRunning,
    applyProjectWorkspace,
    message,
    openedProject,
    projectWorkspace,
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
    () => getActiveTaskPlan(
      agentMessages,
      agentRunning || agentSubmitting,
    ),
    [agentMessages, agentRunning, agentSubmitting],
  )
  const activeQueuedSubmissions = agentQueuedSubmissions.filter(
    (submission) => (
      submission.projectId === openedProject?.id
      && submission.sessionId === agentSessionId
    ),
  )
  const agentSessionActivities = React.useMemo(() => {
    if (agentSessionId == null || !latestConversationTurn) return {}
    const durableStatus = latestConversationOperation?.status
    const state: AgentConversationActivity['state'] = (
      latestConversationTurn.status === 'planning'
      || latestConversationTurn.status === 'running'
    )
      ? 'running'
      : durableStatus === 'succeeded'
        ? 'completed'
        : durableStatus || latestConversationTurn.status
    return {
      [agentSessionId]: {
        state,
        queuedCount: activeQueuedSubmissions.length + ((
          latestConversationTurn.status === 'queued'
          || latestConversationOperation?.status === 'queued'
        ) ? 1 : 0),
      },
    }
  }, [
    activeQueuedSubmissions.length,
    agentSessionId,
    latestConversationOperation,
    latestConversationTurn,
  ])
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
  const agentTurnArtifacts = React.useMemo(() => screenplayTurnArtifacts(
    agentConversationState?.operations ?? [],
    agentConversationState?.tasks ?? [],
    projectWorkspace,
  ), [agentConversationState?.operations, agentConversationState?.tasks, projectWorkspace])
  const openRevisionLibrary = React.useCallback((
    target: RevisionLibraryTarget | null,
  ) => {
    setRevisionLibrarySelection(revisionLibraryTarget(target))
    setRevisionLibraryOpen(true)
  }, [])
  const renderAgentArtifact = React.useCallback((
    artifact: ScreenplayTurnArtifact,
  ) => {
    const sourceCount = artifact.sourceRunId
      ? projectSourceRefs.filter(
          (ref) => ref.agent_run_id === artifact.sourceRunId,
        ).length
      : 0
    return (
      <ScreenplayProposalActionPanel
        artifact={artifact}
        sourceCount={sourceCount}
        showSources={!agentRunning && Boolean(artifact.sourceRunId)}
        running={agentRunning}
        accepting={acceptingAgentRevisionId === artifact.revisionId}
        archived={openedProject?.status === 'archived'}
        onView={() => openRevisionLibrary(artifact)}
        onApply={() => void acceptAgentRevision(artifact)}
      />
    )
  }, [
    acceptAgentRevision,
    acceptingAgentRevisionId,
    agentRunning,
    openRevisionLibrary,
    openedProject?.status,
    projectSourceRefs,
  ])
  const agentArtifactVersion = [...agentTurnArtifacts.values()]
    .map((artifact) => `${artifact.revisionId}:${artifact.status}`)
    .join('|')
  const screenplayConversationActions = React.useMemo<
    ScreenplayConversationBindings['actions']
  >(() => ({
    selectSession: (sessionId) => {
      if (typeof sessionId === 'number') switchAgentSession(sessionId)
    },
    createSession: createAgentSession,
    closeSession: (sessionId) => {
      const session = agentSessions.find((item) => item.id === sessionId)
      if (session) return closeAgentSession(session)
    },
    renameSession: (sessionId, title) => {
      if (typeof sessionId === 'number') return renameAgentSession(sessionId, title)
    },
    send: (content) => runAgent(content),
    abort: () => void stopAgent(),
    resume: resumeAgent,
    editMessage: (index, content) => runAgent(content, index),
    resolveToolApproval: (approvalId, approved) => (
      services.ai.resolveAiToolApproval({ approvalId, approved })
    ),
    onSubmitErrorReport: (reportId) => (
      services.ai.submitAiErrorReport({ reportId })
    ),
  }), [
    agentSessions,
    closeAgentSession,
    createAgentSession,
    renameAgentSession,
    resumeAgent,
    runAgent,
    stopAgent,
    switchAgentSession,
  ])
  const screenplayConversationBindings = React.useMemo<
    ScreenplayConversationBindings | null
  >(() => openedProject ? ({
    project: openedProject,
    sessions: agentSessions,
    activeSessionId: agentSessionId,
    conversationIdentity: agentConversationIdentity,
    messages: agentMessages,
    activities: agentSessionActivities,
    queuedSubmissions: activeQueuedSubmissions,
    prompt: agentPrompt,
    setPrompt: updateAgentPrompt,
    initializing: projectLoading
      || agentSessionLoading
      || agentChunkHydrating
      || agentLoadInitializing,
    running: agentRunning || agentSubmitting,
    stopping: Boolean(
      cancelPendingConversationOperation
      || agentCancelSubmitting
      || agentCancelHeld
    ),
    paused: Boolean(pausedConversationOperation),
    resuming: agentResumeSubmitting,
    attachmentsVersion: agentArtifactVersion,
    modelConfigs,
    selectedModelId,
    setSelectedModelId,
    updateModel: onUpdateModelConfig,
    openModelSettings: onOpenSettings,
    taskPlan: activeAgentTaskPlan ?? undefined,
    actions: screenplayConversationActions,
  }) : null, [
    activeAgentTaskPlan,
    activeQueuedSubmissions,
    agentArtifactVersion,
    agentCancelSubmitting,
    agentCancelHeld,
    agentChunkHydrating,
    agentConversationIdentity,
    agentLoadInitializing,
    agentMessages,
    agentPrompt,
    agentResumeSubmitting,
    agentRunning,
    agentSessionActivities,
    agentSessionId,
    agentSessionLoading,
    agentSessions,
    agentSubmitting,
    cancelPendingConversationOperation,
    modelConfigs,
    onOpenSettings,
    onUpdateModelConfig,
    openedProject,
    pausedConversationOperation,
    projectLoading,
    screenplayConversationActions,
    selectedModelId,
    updateAgentPrompt,
  ])
  const screenplayConversationController = useScreenplayConversationController(
    screenplayConversationBindings,
  )
  const screenplayConversationExtensions = useScreenplayConversationExtensions({
    projectTitle: openedProject?.title ?? '',
    stageLabel: openedProject ? STAGE_LABELS[openedProject.active_stage] : '',
    messages: agentConversationState?.messages,
    artifacts: agentTurnArtifacts,
    renderArtifact: renderAgentArtifact,
  })
  const hasPendingAgentProposal = Boolean(
    projectWorkspace?.candidates.some((revision) => revision.agentTaskId),
  )
  const reviewState = projectWorkspace?.workflow.review
  const reviewEntry = reviewState?.reviewRevisionId
    ? reviewWorkspaceEntry(reviewState)
    : null
  React.useEffect(() => {
    if (!reviewState?.reviewRevisionId) setReviewAdjudicationOpen(false)
  }, [reviewState?.reviewRevisionId])
  const reviewUsesAgentAction = openedProject?.active_stage !== 'review'
    || !reviewState
    || reviewAgentActionAvailable(reviewState)
  const showStageStartAction = openedProject?.active_stage !== 'completed'
    && reviewUsesAgentAction
  const stageStartActionDisabled = openedProject?.status === 'archived'
    || !projectWorkspace
    || agentSessionLoading
    || agentChunkHydrating
    || agentRunning
    || agentSubmitting
    || reviewMutationPending
    || hasPendingAgentProposal
    || (
      openedProject?.active_stage === 'orientation'
      && openedProject.source_kind === 'book'
      && !openedProject.source_book_id
    )
  const draftBatchScope = openedProject
    ? screenplayDraftBatchScope(
      openedProject,
      projectDocuments,
      draftEpisodes,
      documentEpisodes,
    )
    : null
  const draftBatchActions = draftBatchScope
    ? buildDraftBatchActions(draftBatchScope)
    : []
  const maxCustomDraftEpisodeCount = Math.min(
    MAX_SCREENPLAY_DRAFT_BATCH_EPISODES,
    draftBatchScope?.pendingEpisodeCount ?? 0,
  )
  const primaryStageAction = openedProject
    ? stageAgentAction({
        project: openedProject,
        documents: projectDocuments,
        draftEpisodes,
        documentEpisodes,
        reviewState,
      })
    : { label: '' }
  const handleStageStartAction = React.useCallback(() => {
    if (
      !openedProject
      || !projectWorkspace
      || openedProject.active_stage === 'completed'
      || agentRunning
      || agentSubmitting
      || hasPendingAgentProposal
      || reviewMutationPending
      || (
        openedProject.active_stage === 'review'
        && reviewState
        && !reviewAgentActionAvailable(reviewState)
      )
    ) {
      return
    }
    runAgent(
      primaryStageAction.label,
      undefined,
      undefined,
      primaryStageAction.stageCommand,
    )
  }, [
    agentRunning,
    agentSubmitting,
    hasPendingAgentProposal,
    openedProject,
    primaryStageAction,
    projectWorkspace,
    reviewMutationPending,
    reviewState?.hardChecks,
    reviewState?.phase,
    runAgent,
  ])
  const startDraftRange = React.useCallback((
    scope: ScreenplayDraftScope,
  ) => {
    if (
      !openedProject
      || !projectWorkspace
      || openedProject.active_stage !== 'draft'
      || agentRunning
      || agentSubmitting
      || hasPendingAgentProposal
    ) {
      return
    }
    const stageAction = stageAgentAction({
      project: openedProject,
      documents: projectDocuments,
      draftEpisodes,
      documentEpisodes,
      draftScope: scope,
    })
    runAgent(
      stageAction.label,
      undefined,
      undefined,
      stageAction.stageCommand,
    )
  }, [
    agentRunning,
    agentSubmitting,
    hasPendingAgentProposal,
    documentEpisodes,
    draftEpisodes,
    openedProject,
    projectDocuments,
    projectWorkspace,
    runAgent,
  ])
  const handleDraftBatchAction = React.useCallback((action: DraftBatchAction) => {
    startDraftRange(action.key)
  }, [startDraftRange])
  const openCustomDraftRange = React.useCallback(() => {
    if (maxCustomDraftEpisodeCount < 2) return
    setCustomDraftEpisodeCount(Math.min(3, maxCustomDraftEpisodeCount))
    setDraftRangeModalOpen(true)
  }, [maxCustomDraftEpisodeCount])
  const startCustomDraftRange = React.useCallback(() => {
    const episodeCount = customDraftEpisodeCount ?? 0
    if (
      !Number.isInteger(episodeCount)
      || episodeCount < 2
      || episodeCount > maxCustomDraftEpisodeCount
    ) {
      message.warning(`请输入 2 到 ${maxCustomDraftEpisodeCount} 之间的集数`)
      return
    }
    setDraftRangeModalOpen(false)
    startDraftRange(draftScopeForEpisodeCount(episodeCount))
  }, [customDraftEpisodeCount, maxCustomDraftEpisodeCount, message, startDraftRange])
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
                  将创建项目，并把当前信息作为可继续编辑的起始内容
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
                    {showStageStartAction || reviewEntry ? (
                      <div className="screenplay-project-next__actions">
                        {showStageStartAction && (
                          openedProject.active_stage === 'draft'
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
                                  items: [
                                    ...draftBatchActions
                                      .filter((action) => action.key !== 'all_remaining')
                                      .map((action) => ({
                                        key: action.key,
                                        label: action.label,
                                        onClick: () => handleDraftBatchAction(action),
                                      })),
                                    {
                                      key: 'custom_episode_range',
                                      label: '自定义连续集数…',
                                      onClick: openCustomDraftRange,
                                    },
                                    ...draftBatchActions
                                      .filter((action) => action.key === 'all_remaining')
                                      .map((action) => ({
                                        key: action.key,
                                        label: action.label,
                                        onClick: () => handleDraftBatchAction(action),
                                      })),
                                  ],
                                }}
                              >
                                {primaryStageAction.label}
                              </PurrDropdown.Button>
                            ) : (
                              <PurrButton
                                type="primary"
                                size="small"
                                icon={<ArrowRightIcon />}
                                disabled={stageStartActionDisabled}
                                onClick={handleStageStartAction}
                              >
                                {primaryStageAction.label}
                              </PurrButton>
                            )
                        )}
                        {reviewEntry && (
                          <PurrButton
                            type={reviewEntry.emphasis === 'primary' ? 'primary' : 'default'}
                            size="small"
                            icon={<EyeIcon />}
                            disabled={reviewMutationPending}
                            onClick={() => setReviewAdjudicationOpen(true)}
                          >
                            {reviewEntry.label}
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
                  {screenplayConversationController ? (
                    <AgentConversationPanel
                      controller={screenplayConversationController}
                      extensions={screenplayConversationExtensions}
                    />
                  ) : null}
                </div>
              </section>

              <section className="screenplay-project-documents">
                <div className="screenplay-project-section-title">
                  <div>
                    <span className="screenplay-source-eyebrow">PROJECT FILES</span>
                    <h2>项目文档</h2>
                  </div>
                  <span>不可变 Revision</span>
                </div>
                {projectWorkspace && (
                  <button
                    type="button"
                    className="screenplay-document-library-launch"
                    onClick={() => openRevisionLibrary(null)}
                  >
                    <span className="screenplay-document-library-launch__icon">
                      <HistoryIcon />
                    </span>
                    <span>
                      <strong>打开项目文档</strong>
                      <small>
                        {Object.values(projectWorkspace.workflow.heads).filter(Boolean).length}
                        {' 份当前文档 · '}
                        {projectWorkspace.candidates.length} 份待应用
                      </small>
                    </span>
                    <ArrowRightIcon />
                  </button>
                )}
              </section>
            </section>
          </div>
        )}

        {openedProject && projectWorkspace && (
          <RevisionLibraryModal
            open={revisionLibraryOpen}
            projectId={openedProject.id}
            workspace={projectWorkspace}
            target={revisionLibrarySelection}
            readOnly={openedProject.status === 'archived'}
            onClose={() => {
              setRevisionLibraryOpen(false)
              setRevisionLibrarySelection(null)
            }}
            onWorkspaceChange={applyRevisionWorkspace}
          />
        )}

        {openedProject && reviewState?.reviewRevisionId && (
          <PurrModal
            title="审阅与定稿"
            open={reviewAdjudicationOpen}
            width="min(1120px, calc(100vw - 48px))"
            footer={null}
            destroyOnHidden
            className="screenplay-review-adjudication-modal"
            onCancel={() => {
              if (reviewMutationPending) return
              setReviewAdjudicationOpen(false)
            }}
          >
            <ReviewAdjudicationPanel
              modal
              review={reviewState}
              readOnly={
                openedProject.status === 'archived'
                || reviewState.phase === 'completed'
              }
              busy={reviewMutationPending || agentRunning || agentSubmitting}
              onDecide={adjudicateReview}
              onStartReview={handleStageStartAction}
              onStartRevision={handleStageStartAction}
              onFinalize={() => void finalizeProject()}
            />
          </PurrModal>
        )}

        <PurrModal
          title="连续创作多集"
          open={draftRangeModalOpen && maxCustomDraftEpisodeCount >= 2}
          width={420}
          destroyOnHidden
          okText="开始创作"
          cancelText="取消"
          okButtonProps={{
            disabled: customDraftEpisodeCount == null
              || !Number.isInteger(customDraftEpisodeCount)
              || customDraftEpisodeCount < 2
              || customDraftEpisodeCount > maxCustomDraftEpisodeCount,
          }}
          onOk={startCustomDraftRange}
          onCancel={() => setDraftRangeModalOpen(false)}
        >
          <div className="screenplay-draft-range-modal">
            <p>
              从下一集开始连续创作。系统会按集拆分创作任务，完成后统一检查连贯性。
            </p>
            <label>
              <span>连续创作</span>
              <PurrInputNumber
                value={customDraftEpisodeCount}
                min={2}
                max={maxCustomDraftEpisodeCount}
                step={1}
                onChange={setCustomDraftEpisodeCount}
              />
              <span>集</span>
            </label>
            <small>当前最多可连续创作 {maxCustomDraftEpisodeCount} 集</small>
          </div>
        </PurrModal>

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
                  <span>
                    {selectedProjectDocumentGroup.stage === 'draft' && draftEpisodes.length > 0
                      ? `${draftEpisodes.length} 集 · 按集独立存储`
                      : documentEpisodes.some((episode) => (
                          selectedProjectDocumentGroup.kinds.includes(episode.document_kind)
                        ))
                        ? `${documentEpisodes.filter((episode) => (
                            selectedProjectDocumentGroup.kinds.includes(episode.document_kind)
                          )).length} 个分集版本 · 按集独立存储`
                      : `${selectedProjectDocumentGroup.documents.length} 个版本`}
                  </span>
                </header>
                <div className="screenplay-document-list">
                  {selectedProjectDocumentGroup.stage === 'draft' && draftEpisodes.map((episode) => (
                    <article className="screenplay-document-item" key={episode.id}>
                      <div>
                        <strong>{episode.title}</strong>
                        <span>
                          分集正文 · v{episode.version} · {episode.scene_ids.length} 场
                        </span>
                      </div>
                      <span className="screenplay-document-item__actions">
                        <span className="screenplay-document-status is-accepted">
                          独立分集
                        </span>
                        <PurrButton
                          size="small"
                          type="text"
                          icon={draftEpisodeLoadingId === episode.id
                            ? <LoadingIcon spin />
                            : <EyeIcon />}
                          disabled={draftEpisodeLoadingId != null}
                          onClick={() => {
                            setDocumentLibraryOpen(false)
                            void openDraftEpisode(episode)
                          }}
                          aria-label={`查看${episode.title}`}
                          title="查看分集正文"
                        />
                      </span>
                    </article>
                  ))}
                  {documentEpisodes
                    .filter((episode) => (
                      selectedProjectDocumentGroup.kinds.includes(episode.document_kind)
                    ))
                    .map((episode) => (
                      <article className="screenplay-document-item" key={episode.id}>
                        <div>
                          <strong>{episode.title}</strong>
                          <span>
                            {DOCUMENT_KIND_LABELS[episode.document_kind]} · v{episode.version}
                            {' · '}{episode.item_count} 项
                          </span>
                        </div>
                        <span className="screenplay-document-item__actions">
                          <span className={`screenplay-document-status is-${episode.status}`}>
                            独立分集
                          </span>
                          <PurrButton
                            size="small"
                            type="text"
                            icon={documentEpisodeLoadingId === episode.id
                              ? <LoadingIcon spin />
                              : <EyeIcon />}
                            disabled={documentEpisodeLoadingId != null}
                            onClick={() => {
                              setDocumentLibraryOpen(false)
                              void openDocumentEpisode(episode)
                            }}
                            aria-label={`查看${episode.title}`}
                            title="查看分集文档"
                          />
                        </span>
                      </article>
                    ))}
                  {selectedProjectDocumentGroup.documents
                    .filter((document) => (
                      document.kind !== 'scene_draft'
                      && document.content_json?.storageMode !== 'episode_documents'
                    ))
                    .map((document) => {
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
                              ? '只读草稿'
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
                              void openDocument(document)
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
                                void openDocument(document, true)
                              }}
                              aria-label={`对比${document.title}的历史版本`}
                              title="对比版本"
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
          open={selectedDocumentEpisode != null}
          title={selectedDocumentEpisode
            ? `${selectedDocumentEpisode.title} · v${selectedDocumentEpisode.version}`
            : '分集文档'}
          width="min(920px, calc(100vw - 40px))"
          footer={null}
          destroyOnHidden
          onCancel={() => setSelectedDocumentEpisode(null)}
          className="screenplay-version-modal"
        >
          {selectedDocumentEpisode && (
            <div className="screenplay-version-viewer">
              <div className="screenplay-version-viewer__meta">
                <span className={`screenplay-document-status is-${selectedDocumentEpisode.status}`}>
                  {selectedDocumentEpisode.status === 'accepted' ? '当前版本' : '历史版本'}
                </span>
                <span>第 {selectedDocumentEpisode.episode_number} 集</span>
                <span>{selectedDocumentEpisode.item_count} 项</span>
              </div>
              <article className="screenplay-version-document">
                <h2>{selectedDocumentEpisode.title}</h2>
                <Markdown
                  className="screenplay-version-document__body"
                  preserveSoftBreaks
                >
                  {selectedDocumentEpisode.content_text
                    || structuredContentToMarkdown(selectedDocumentEpisode.content_json)}
                </Markdown>
              </article>
            </div>
          )}
        </PurrModal>

        <PurrModal
          open={selectedDraftEpisode != null}
          title={selectedDraftEpisode
            ? `${selectedDraftEpisode.title} · v${selectedDraftEpisode.version}`
            : '分集正文'}
          width="min(920px, calc(100vw - 40px))"
          footer={null}
          destroyOnHidden
          onCancel={() => setSelectedDraftEpisode(null)}
          className="screenplay-version-modal"
        >
          {selectedDraftEpisode && (
            <div className="screenplay-version-viewer">
              <div className="screenplay-version-viewer__meta">
                <span className="screenplay-document-status is-accepted">已接受</span>
                <span>{selectedDraftEpisode.scene_count} 场</span>
                <span>
                  场景 {selectedDraftEpisode.scene_ids.join('、')}
                </span>
              </div>
              <article className="screenplay-version-document">
                <h2>{selectedDraftEpisode.title}</h2>
                <Markdown
                  className="screenplay-version-document__body"
                  preserveSoftBreaks
                >
                  {selectedDraftEpisode.content_text || '*暂无正文内容*'}
                </Markdown>
              </article>
            </div>
          )}
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
          }}
          footer={selectedDocument ? (
            <div className="screenplay-version-modal__footer">
              <div>
                {selectedPreviousDocument && (
                  <PurrButton
                    icon={<HistoryIcon />}
                    onClick={() => {
                      setComparisonDocument((current) => (
                        current ? null : selectedPreviousDocument
                      ))
                    }}
                  >
                    {comparisonDocument ? '返回文档' : `与 v${selectedPreviousDocument.version} 对比`}
                  </PurrButton>
                )}
              </div>
              <div>
                <PurrButton
                  onClick={() => {
                    setSelectedDocument(null)
                    setComparisonDocument(null)
                  }}
                >
                  关闭
                </PurrButton>
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
                    <span>历史版本 · v{comparisonDocument.version}</span>
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
                  <article className="screenplay-version-document">
                    <h2>{selectedDocument.title}</h2>
                    <Markdown className="screenplay-version-document__body">
                      {selectedDocument.content_text || '*暂无正文内容*'}
                    </Markdown>
                  </article>
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

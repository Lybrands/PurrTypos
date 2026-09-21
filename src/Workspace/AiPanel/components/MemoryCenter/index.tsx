import InheritedPlotMaterials from '../../../InheritedPlotMaterials'
import { services } from '@/services'
import { KNOWLEDGE_TAB } from '../../../utilityPanelTypes'
import { useWorkspaceStore } from '../../../../stores/workspaceStore'
import React from 'react'
import { PurrCollapse, PurrButton, PurrEmpty, PurrInput, PurrModal, PurrRadio, PurrSelect, PurrSpace, PurrSpin, PurrSwitch, PurrTag, PurrTooltip, purrToast, usePurrConfirm } from '@/purr-components'
import {
  CheckCircleIcon,
  ClockIcon,
  CloseIcon,
  HistoryIcon,
  InboxIcon,
  LibraryIcon,
  PlusIcon,
  PushpinIcon,
} from '@/purr-components'
import type {
  ComponentMemoryLinkPage,
  ComponentMemoryOperation,
  EntityId,
  MemoryKind,
  UnifiedMemoryItem,
  UnifiedMemorySource,
  UnifiedMemoryStatus,
} from '../../../../types'
import './index.scss'

interface MemoryCenterProps {
  bookId: EntityId | null
}

type Resolution = 'accepted' | 'rejected'
type DisplayEntry =
  | { type: 'memory'; key: string; item: UnifiedMemoryItem }
  | { type: 'candidate'; key: string; deltaId: string; items: UnifiedMemoryItem[] }

const MEMORY_KIND_OPTIONS: { value: MemoryKind; label: string }[] = [
  { value: 'canon', label: '设定' },
  { value: 'plot', label: '剧情事实' },
  { value: 'character', label: '人物状态' },
  { value: 'world', label: '世界观' },
  { value: 'foreshadowing', label: '伏笔' },
  { value: 'style', label: '风格' },
  { value: 'summary', label: '总结' },
]

const STORY_KIND_OPTIONS = [
  { value: 'character_state', label: '人物状态 · 精确' },
  { value: 'relationship_state', label: '人物关系' },
  { value: 'world_fact', label: '世界事实' },
  { value: 'timeline_event', label: '时间线事件' },
  { value: 'plot_thread', label: '剧情线' },
] as const

const ALL_KIND_OPTIONS = [...MEMORY_KIND_OPTIONS, ...STORY_KIND_OPTIONS]
const KIND_LABEL = Object.fromEntries(
  ALL_KIND_OPTIONS.map((item) => [item.value, item.label]),
) as Record<string, string>

const STATUS_OPTIONS: Array<{ value: UnifiedMemoryStatus; label: string }> = [
  { value: 'active', label: '已生效' },
  { value: 'pending', label: '待审核' },
  { value: 'conflict', label: '冲突' },
  { value: 'stale', label: '已失效' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'archived', label: '已归档' },
]

const STATUS_LABEL = Object.fromEntries(
  STATUS_OPTIONS.map((item) => [item.value, item.label]),
) as Record<UnifiedMemoryStatus, string>

const SOURCE_OPTIONS: Array<{ value: UnifiedMemorySource; label: string }> = [
  { value: 'semantic', label: '语义记忆' },
  { value: 'story_state', label: '精确故事状态' },
  { value: 'story_candidate', label: 'AI 候选' },
]

const SOURCE_LABEL = Object.fromEntries(
  SOURCE_OPTIONS.map((item) => [item.value, item.label]),
) as Record<UnifiedMemorySource, string>

const RELATION_OPTIONS = [
  { value: 'supports', label: '支持' },
  { value: 'contradicts', label: '冲突' },
  { value: 'supersedes', label: '取代' },
  { value: 'relates_to', label: '相关' },
] as const

const MEMORY_ERROR_MESSAGES: Record<string, string> = {
  memory_component_unavailable: '本地记忆组件不可用，请检查组件配置后重试。',
  memory_embedding_unconfigured: '尚未配置记忆 Embedding 模型，无法执行语义记忆操作。',
  memory_model_unconfigured: '当前模型不可用，请选择可用的写作模型或单独配置提炼与评审模型。',
  memory_version_conflict: '这条记忆已被其他操作更新，请刷新后重试。',
  memory_context_stale: '记忆来源已经变化，请重新召回后再执行。',
  memory_not_found: '这条记忆已不存在或不属于当前作品。',
}

function memoryErrorMessage(code: string | undefined, fallback: string): string {
  return (code && MEMORY_ERROR_MESSAGES[code]) || code || fallback
}

const KIND_HINTS: Record<MemoryKind, string> = {
  canon: '记录不会轻易改变的规则、身份或核心设定。',
  plot: '记录已经发生且后续剧情必须遵守的事实。',
  character: '记录人物当前的关系、立场、目标或状态变化。',
  world: '记录地点、组织、制度、能力体系等世界观信息。',
  foreshadowing: '记录需要在后文呼应、推进或回收的线索。',
  style: '记录叙事语气、表达偏好或必须遵守的写作规则。',
  summary: '记录阶段性结论；建议先保存为待确认，审核后再启用。',
}

const STRUCTURED_FIELD_LABELS: Record<string, string> = {
  keywords: '关键词',
  importance: '重要程度',
  characterId: '人物',
  attribute: '属性',
  value: '值',
  note: '备注',
  sourceCharacterId: '关系发起人物',
  targetCharacterId: '关系目标人物',
  relationType: '关系类型',
  state: '状态',
  description: '描述',
  directional: '是否有方向',
  factId: '事实标识',
  statement: '事实描述',
  truthMode: '事实模式',
  subjectId: '关联主体',
  knownByCharacterIds: '知情人物',
  tags: '标签',
  eventId: '事件标识',
  title: '标题',
  summary: '摘要',
  participantIds: '参与者',
  locationId: '地点',
  storyTime: '故事时间',
  storyTimePrecision: '时间精度',
  narrativeOrder: '叙事顺序',
  causedByEventIds: '前置事件',
  threadId: '剧情线标识',
  relatedEntityIds: '关联实体',
  openedChapterId: '开启章节',
  expectedResolutionChapterId: '预计解决章节',
  resolvedChapterId: '解决章节',
  fieldChanges: '字段变更',
}

const MEMORY_CONTENT_PLACEHOLDER = '请输入希望 AI 在后续创作中持续记住的内容…'

function defaultResolution(item: UnifiedMemoryItem): Resolution | undefined {
  if (item.recommendation === 'apply') return 'accepted'
  if (item.recommendation === 'reject') return 'rejected'
  return undefined
}

function semanticId(item: UnifiedMemoryItem): string {
  return item.id.startsWith('semantic:') ? item.id.slice('semantic:'.length) : ''
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function structuredFieldLabel(key: string): string {
  return STRUCTURED_FIELD_LABELS[key] || key
}

export default function MemoryCenter({ bookId }: MemoryCenterProps) {
  const openKnowledgeTab = React.useCallback(
    () => useWorkspaceStore.getState().openUtilityTab(KNOWLEDGE_TAB),
    [],
  )
  const confirm = usePurrConfirm()
  const [query, setQuery] = React.useState('')
  const [status, setStatus] = React.useState<UnifiedMemoryStatus | undefined>()
  const [kind, setKind] = React.useState<UnifiedMemoryItem['kind'] | undefined>()
  const [source, setSource] = React.useState<UnifiedMemorySource | undefined>()
  const [items, setItems] = React.useState<UnifiedMemoryItem[]>([])
  const [loading, setLoading] = React.useState(false)
  const [semanticUnavailable, setSemanticUnavailable] = React.useState<string>()
  const [creating, setCreating] = React.useState(false)
  const [createOpen, setCreateOpen] = React.useState(false)
  const [newKind, setNewKind] = React.useState<MemoryKind>('canon')
  const [newContent, setNewContent] = React.useState('')
  const [newStatus, setNewStatus] = React.useState<'active' | 'pending'>('active')
  const [newPinned, setNewPinned] = React.useState(false)
  const [settingsSaving, setSettingsSaving] = React.useState(false)
  const [intelligenceEnabled, setIntelligenceEnabled] = React.useState(false)
  const [analysisEnabled, setAnalysisEnabled] = React.useState(false)
  const [autoApplyEnabled, setAutoApplyEnabled] = React.useState(false)
  const [minConfidence, setMinConfidence] = React.useState(0.95)
  const [resolutions, setResolutions] = React.useState<Record<string, Record<string, Resolution>>>({})
  const [submittingId, setSubmittingId] = React.useState<string | null>(null)
  const [editingItem, setEditingItem] = React.useState<UnifiedMemoryItem | null>(null)
  const [editingContent, setEditingContent] = React.useState('')
  const [editingSaving, setEditingSaving] = React.useState(false)
  const [historyItem, setHistoryItem] = React.useState<UnifiedMemoryItem | null>(null)
  const [history, setHistory] = React.useState<Array<Record<string, unknown>>>([])
  const [historyLoading, setHistoryLoading] = React.useState(false)
  const [reviewItem, setReviewItem] = React.useState<UnifiedMemoryItem | null>(null)
  const [reviewOperation, setReviewOperation] = React.useState<ComponentMemoryOperation | null>(null)
  const [reviewLoading, setReviewLoading] = React.useState(false)
  const [linkItem, setLinkItem] = React.useState<UnifiedMemoryItem | null>(null)
  const [links, setLinks] = React.useState<ComponentMemoryLinkPage['items']>([])
  const [linkTargetId, setLinkTargetId] = React.useState<string>()
  const [linkRelation, setLinkRelation] = React.useState<ComponentMemoryLinkPage['items'][number]['relation']>('relates_to')
  const [linkNote, setLinkNote] = React.useState('')
  const [linkLoading, setLinkLoading] = React.useState(false)

  const load = React.useCallback(async () => {
    if (bookId == null) {
      setItems([])
      return
    }
    setLoading(true)
    const res = await services.memories.listUnifiedMemories({
      bookId,
      query,
      statuses: status ? [status] : undefined,
      kinds: kind ? [kind] : undefined,
      sources: source ? [source] : undefined,
      limit: 200,
    })
    setLoading(false)
    if (!res.success || !res.data || !Array.isArray(res.data.items)) {
      setItems([])
      purrToast.error(memoryErrorMessage(res.error, '读取记忆失败'))
      return
    }
    if (!source || source === 'semantic') setSemanticUnavailable(res.data.unavailableSources?.semantic)
    setItems(res.data.items)
    setResolutions((previous) => {
      const next = { ...previous }
      res.data.items.forEach((item) => {
        if (item.source !== 'story_candidate' || !item.delta_id || !item.target_key) return
        const suggested = defaultResolution(item)
        if (suggested && !next[item.delta_id]?.[item.target_key]) {
          next[item.delta_id] = { ...(next[item.delta_id] || {}), [item.target_key]: suggested }
        }
      })
      return next
    })
  }, [bookId, kind, query, source, status])

  React.useEffect(() => {
    void load()
  }, [load])

  React.useEffect(() => {
    let cancelled = false
    services.settings.getSettings().then((res) => {
      if (cancelled || !res.success) return
      setIntelligenceEnabled(!!res.data?.memory_intelligence_enabled)
      setAnalysisEnabled(!!res.data?.story_memory_analysis_enabled)
      setAutoApplyEnabled(!!res.data?.story_memory_auto_apply_enabled)
      const value = Number(res.data?.story_memory_auto_apply_min_confidence)
      setMinConfidence(Number.isFinite(value) ? value : 0.95)
    })
    return () => { cancelled = true }
  }, [])

  const saveSettings = React.useCallback(async (patch: Record<string, unknown>) => {
    setSettingsSaving(true)
    const res = await services.settings.setSettings(patch)
    setSettingsSaving(false)
    if (!res.success) purrToast.error(res.error || '保存记忆设置失败')
    return res.success
  }, [])

  const handleCreate = React.useCallback(async () => {
    if (bookId == null || !newContent.trim()) return
    setCreating(true)
    const res = await services.memories.createMemory({
      bookId,
      kind: newKind,
      text: newContent.trim(),
      operationKey: crypto.randomUUID(),
      state: newStatus === 'pending' ? 'pending' : 'active',
      pinned: newPinned,
    })
    setCreating(false)
    if (!res.success) {
      purrToast.error(memoryErrorMessage(res.error, '保存记忆失败'))
      return
    }
    setNewContent('')
    setNewPinned(false)
    setCreateOpen(false)
    purrToast.success('已保存记忆')
    await load()
  }, [bookId, load, newContent, newKind, newPinned, newStatus])

  const updateSemantic = React.useCallback(async (
    item: UnifiedMemoryItem,
    data: { content?: string; status?: 'active' | 'archived'; pinned?: number },
  ) => {
    const id = semanticId(item)
    if (!id || bookId == null || !item.version) return false
    const res = data.status
      ? await services.memories.setMemoryState({
          id,
          bookId,
          version: item.version,
          operationKey: crypto.randomUUID(),
          state: data.status === 'active' ? 'active' : 'disabled',
          reason: data.status === 'active' ? 'activated' : 'archived',
        })
      : await services.memories.updateMemory({
          id,
          bookId,
          version: item.version,
          operationKey: crypto.randomUUID(),
          ...(data.content !== undefined ? { text: data.content } : {}),
          ...(data.pinned !== undefined ? { pinned: Boolean(data.pinned) } : {}),
        })
    if (!res.success) {
      purrToast.error(memoryErrorMessage(res.error, '更新记忆失败'))
      return false
    }
    await load()
    return true
  }, [bookId, load])

  const archiveSemantic = React.useCallback((item: UnifiedMemoryItem) => {
    const id = semanticId(item)
    if (!id || bookId == null || !item.version) return
    const version = item.version
    void confirm({
      title: '归档这条记忆？',
      content: '归档后不会自动召回，但仍可在已归档筛选中查看。',
      confirmText: '归档',
      cancelText: '取消',
      confirmVariant: 'danger',
    }).then(async (result) => {
      if (result === 'confirm') {
        const res = await services.memories.setMemoryState({
          id,
          bookId,
          version,
          operationKey: crypto.randomUUID(),
          state: 'disabled',
          reason: 'archived',
        })
        if (!res.success) purrToast.error(memoryErrorMessage(res.error, '归档失败'))
        await load()
      }
    })
  }, [bookId, confirm, load])

  const submitCandidateGroup = React.useCallback(async (
    deltaId: string,
    candidates: UnifiedMemoryItem[],
  ) => {
    const selected = resolutions[deltaId] || {}
    if (!candidates.every((item) => item.target_key && selected[item.target_key])) {
      purrToast.warning('请先处理这一组中的所有候选')
      return
    }
    setSubmittingId(deltaId)
    const res = await services.storyMemory.resolveStoryMemoryEvolutionReview({
      deltaId,
      resolutions: selected,
    })
    setSubmittingId(null)
    if (!res.success) {
      purrToast.error(res.error || '提交记忆审核失败')
      await load()
      return
    }
    purrToast.success(res.data?.applied_delta_id ? '已应用接受的故事状态' : '候选已全部拒绝')
    await load()
  }, [load, resolutions])

  const openHistory = React.useCallback(async (item: UnifiedMemoryItem) => {
    if (bookId == null) return
    setHistoryItem(item)
    setHistory([])
    setHistoryLoading(true)
    const id = semanticId(item)
    const res = id
      ? await services.memories.getMemoryHistory({ bookId, id })
      : item.memory_key
        ? await services.storyMemory.getStoryMemoryVersions({
            bookId,
            memoryKey: item.memory_key,
          })
        : null
    setHistoryLoading(false)
    if (res?.success && Array.isArray(res.data)) {
      setHistory(res.data.map((row) => ({ ...row })))
    }
    else purrToast.error(memoryErrorMessage(res?.error, '读取版本历史失败'))
  }, [bookId])

  const reviewSemantic = React.useCallback(async (item: UnifiedMemoryItem) => {
    const id = semanticId(item)
    if (!id || !item.version || bookId == null) return
    setReviewItem(item)
    setReviewOperation(null)
    setReviewLoading(true)
    const res = await services.memories.reviewMemory({
      id,
      bookId,
      version: item.version,
      operationKey: crypto.randomUUID(),
    })
    setReviewLoading(false)
    if (res.success) setReviewOperation(res.data)
    else purrToast.error(memoryErrorMessage(res.error, '生成审核建议失败'))
  }, [bookId])

  const applyReviewProposal = React.useCallback(async () => {
    const proposal = reviewOperation?.review?.proposal
    if (!proposal || bookId == null) return
    setReviewLoading(true)
    const res = await services.memories.resolveMemories({
      bookId,
      operationKey: crypto.randomUUID(),
      kind: proposal.kind,
      items: proposal.items,
      keep: proposal.keep,
      reviewKey: proposal.reviewKey,
    })
    setReviewLoading(false)
    if (!res.success) {
      purrToast.error(memoryErrorMessage(res.error, '应用审核决议失败'))
      return
    }
    setReviewItem(null)
    setReviewOperation(null)
    purrToast.success('已应用审核决议')
    await load()
  }, [bookId, load, reviewOperation])

  const deleteSemantic = React.useCallback((item: UnifiedMemoryItem) => {
    const id = semanticId(item)
    if (!id || !item.version || bookId == null) return
    void confirm({
      title: '永久删除这条记忆？',
      content: '删除会保留组件审计历史，但该记忆不能再被召回。',
      confirmText: '删除',
      cancelText: '取消',
      confirmVariant: 'danger',
    }).then(async (result) => {
      if (result !== 'confirm') return
      const res = await services.memories.deleteMemory({
        id,
        bookId,
        version: item.version as number,
        operationKey: crypto.randomUUID(),
      })
      if (!res.success) purrToast.error(memoryErrorMessage(res.error, '删除记忆失败'))
      else await load()
    })
  }, [bookId, confirm, load])

  const openLinks = React.useCallback(async (item: UnifiedMemoryItem) => {
    const id = semanticId(item)
    if (!id || bookId == null) return
    setLinkItem(item)
    setLinks([])
    setLinkTargetId(undefined)
    setLinkNote('')
    setLinkLoading(true)
    const res = await services.memories.getMemoryLinks({ id, bookId, limit: 32 })
    setLinkLoading(false)
    if (res.success) setLinks(res.data.items)
    else purrToast.error(memoryErrorMessage(res.error, '读取记忆关系失败'))
  }, [bookId])

  const createLink = React.useCallback(async () => {
    const fromId = linkItem ? semanticId(linkItem) : ''
    const target = items.find((item) => item.id === linkTargetId)
    const toId = target ? semanticId(target) : ''
    if (!fromId || !linkItem?.version || !toId || !target?.version || bookId == null) return
    setLinkLoading(true)
    const res = await services.memories.linkMemories({
      bookId,
      operationKey: crypto.randomUUID(),
      fromMemory: { id: fromId, version: linkItem.version },
      toMemory: { id: toId, version: target.version },
      relation: linkRelation,
      note: linkNote.trim(),
    })
    if (!res.success) {
      setLinkLoading(false)
      purrToast.error(memoryErrorMessage(res.error, '建立记忆关系失败'))
      return
    }
    const refreshed = await services.memories.getMemoryLinks({ id: fromId, bookId, limit: 32 })
    setLinkLoading(false)
    if (refreshed.success) setLinks(refreshed.data.items)
    setLinkTargetId(undefined)
    setLinkNote('')
  }, [bookId, items, linkItem, linkNote, linkRelation, linkTargetId])

  const displayEntries = React.useMemo<DisplayEntry[]>(() => {
    const candidates = new Map<string, UnifiedMemoryItem[]>()
    items.forEach((item) => {
      if (item.source === 'story_candidate' && item.delta_id) {
        candidates.set(item.delta_id, [...(candidates.get(item.delta_id) || []), item])
      }
    })
    const emitted = new Set<string>()
    return items.reduce<DisplayEntry[]>((entries, item) => {
      if (item.source !== 'story_candidate' || !item.delta_id) {
        entries.push({ type: 'memory', key: item.id, item })
        return entries
      }
      if (emitted.has(item.delta_id)) return entries
      emitted.add(item.delta_id)
      entries.push({
        type: 'candidate' as const,
        key: `candidate-group:${item.delta_id}`,
        deltaId: item.delta_id,
        items: candidates.get(item.delta_id) || [],
      })
      return entries
    }, [])
  }, [items])

  const renderCandidateGroup = (deltaId: string, candidates: UnifiedMemoryItem[]) => {
    const open = candidates.some((item) => item.actions.includes('accept'))
    return (
      <article className="story-memory-review-card" key={deltaId}>
        <div className="story-memory-review-header">
          <div>
            <div className="story-memory-review-title">
              {candidates[0]?.chapter_title || `章节 ${candidates[0]?.chapter_id || '未知'}`}
            </div>
            <div className="story-memory-review-summary">AI 提取的故事状态候选 · {candidates.length} 条</div>
          </div>
          <PurrTag>{STATUS_LABEL[candidates[0]?.status || 'pending']}</PurrTag>
        </div>
        <div className="story-memory-decision-list">
          {candidates.map((item) => (
            <div className="story-memory-decision" key={item.id}>
              <div className="story-memory-decision-header">
                <PurrSpace size={6} wrap>
                  <PurrTag>{KIND_LABEL[item.kind] || item.kind}</PurrTag>
                  {item.classification ? <PurrTag>{item.classification}</PurrTag> : null}
                  {item.risk ? <PurrTag>{item.risk} risk</PurrTag> : null}
                  <span className="story-memory-confidence">可信度 {Math.round(item.confidence * 100)}%</span>
                </PurrSpace>
                {open && item.target_key ? (
                  <PurrRadio.Group
                    size="small"
                    value={resolutions[deltaId]?.[item.target_key]}
                    onChange={(event) => setResolutions((previous) => ({
                      ...previous,
                      [deltaId]: {
                        ...(previous[deltaId] || {}),
                        [item.target_key as string]: event.target.value as Resolution,
                      },
                    }))}
                  >
                    <PurrRadio.Button value="accepted">接受</PurrRadio.Button>
                    <PurrRadio.Button value="rejected">拒绝</PurrRadio.Button>
                  </PurrRadio.Group>
                ) : <PurrTag>{STATUS_LABEL[item.status]}</PurrTag>}
              </div>
              <div className="story-memory-rationale">{item.summary}</div>
              <div className="story-memory-target-key">{item.target_key}</div>
              <PurrCollapse size="small" className="unified-memory-details" items={[{key: "details", label: "查看结构化字段与证据", children: <>
                <div className="story-memory-payload">
                  {Object.entries(item.structured_data).map(([key, value]) => (
                    <div className="story-memory-payload-row" key={key}>
                      <span>{structuredFieldLabel(key)}</span><code>{displayValue(value)}</code>
                    </div>
                  ))}
                </div>
                {item.evidence_excerpt ? <blockquote className="story-memory-evidence">{item.evidence_excerpt}</blockquote> : null}
              </>}]} />
            </div>
          ))}
        </div>
        {open ? (
          <div className="story-memory-review-footer">
            <span>接受项会成为正式故事状态；拒绝项仅保留审计记录。</span>
            <PurrButton
              type="primary"
              loading={submittingId === deltaId}
              onClick={() => void submitCandidateGroup(deltaId, candidates)}
            >
              提交本组决议
            </PurrButton>
          </div>
        ) : null}
      </article>
    )
  }

  const renderMemory = (item: UnifiedMemoryItem) => (
    <article className="unified-memory-card" key={item.id}>
      <div className="unified-memory-card-main">
        <PurrSpace size={6} wrap>
          <PurrTag>{SOURCE_LABEL[item.source]}</PurrTag>
          <PurrTag>{KIND_LABEL[item.kind] || item.kind}</PurrTag>
          <PurrTag>{STATUS_LABEL[item.status]}</PurrTag>
          {item.pinned ? <PurrTag>固定</PurrTag> : null}
          {item.version ? <span className="memory-center-item-source">v{item.version}</span> : null}
          {item.chapter_title ? <span className="memory-center-item-source">{item.chapter_title}</span> : null}
        </PurrSpace>
        <div className="unified-memory-content">{item.content}</div>
        {item.summary ? <div className="memory-center-item-summary">{item.summary}</div> : null}
        {(item.evidence_excerpt || Object.keys(item.structured_data).length) ? (
          <PurrCollapse size="small" className="unified-memory-details" items={[{key: "details", label: "查看来源与详情", children: <>
            {Object.keys(item.structured_data).length ? (
              <div className="story-memory-payload">
                {Object.entries(item.structured_data).map(([key, value]) => (
                  <div className="story-memory-payload-row" key={key}>
                    <span>{structuredFieldLabel(key)}</span><code>{displayValue(value)}</code>
                  </div>
                ))}
              </div>
            ) : null}
            {item.evidence_excerpt ? <blockquote className="story-memory-evidence">{item.evidence_excerpt}</blockquote> : null}
          </>}]} />
        ) : null}
      </div>
      <PurrSpace size={6} wrap className="unified-memory-actions">
        {item.actions.includes('activate') ? (
          <PurrButton size="small" type="primary" onClick={() => void updateSemantic(item, { status: 'active' })}>启用</PurrButton>
        ) : null}
        {item.actions.includes('edit') ? (
          <PurrButton size="small" onClick={() => { setEditingItem(item); setEditingContent(item.content) }}>编辑</PurrButton>
        ) : null}
        {item.actions.includes('pin') ? (
          <PurrTooltip title={item.pinned ? '取消固定' : '固定优先召回'}>
            <PurrButton
              size="small"
              type={item.pinned ? 'primary' : 'default'}
              icon={<PushpinIcon />}
              onClick={() => void updateSemantic(item, { pinned: item.pinned ? 0 : 1 })}
            />
          </PurrTooltip>
        ) : null}
        {item.actions.includes('archive') ? (
          <PurrButton size="small" icon={<InboxIcon />} onClick={() => archiveSemantic(item)}>归档</PurrButton>
        ) : null}
        {item.actions.includes('review') ? (
          <PurrButton size="small" onClick={() => void reviewSemantic(item)}>审核建议</PurrButton>
        ) : null}
        {item.actions.includes('view_links') ? (
          <PurrButton size="small" onClick={() => void openLinks(item)}>关系</PurrButton>
        ) : null}
        {item.actions.includes('view_history') ? (
          <PurrButton size="small" icon={<HistoryIcon />} onClick={() => void openHistory(item)}>版本</PurrButton>
        ) : null}
        {item.actions.includes('delete') ? (
          <PurrButton size="small" danger onClick={() => deleteSemantic(item)}>删除</PurrButton>
        ) : null}
      </PurrSpace>
    </article>
  )

  return (
    <div className="memory-center">
      <InheritedPlotMaterials bookId={bookId} />
      <div className="memory-center-heading">
        <div>
          <div className="memory-center-heading-title">统一记忆中心</div>
          <div className="memory-center-heading-desc">集中管理语义记忆、精确故事状态与 AI 待审核候选。</div>
        </div>
        <PurrTooltip title="创作资料库">
          <PurrButton type="text" size="small" className="memory-center-knowledge" icon={<LibraryIcon />} aria-label="打开创作资料库"
            onClick={openKnowledgeTab} />
        </PurrTooltip>
      </div>
      {semanticUnavailable && <p className="memory-center-source-notice" role="status">
        {semanticUnavailable === 'memory_embedding_unconfigured'
          ? '语义记忆尚未启用。可在设置中配置记忆 Embedding 模型后重启应用；精确故事状态与 AI 候选仍可查看。'
          : '语义记忆暂时不可用。请检查记忆组件配置并重启应用；精确故事状态与 AI 候选仍可查看。'}
      </p>}

      <div className="story-memory-policy-grid unified-memory-policy-grid">
        <div className="memory-center-intelligence">
          <div>
            <div className="memory-center-intelligence-title">智能长期记忆</div>
            <div className="memory-center-intelligence-desc">从已接受的改动中提炼语义记忆。</div>
          </div>
          <PurrSwitch checked={intelligenceEnabled} loading={settingsSaving} onChange={async (checked) => {
            if (await saveSettings({ memory_intelligence_enabled: checked })) setIntelligenceEnabled(checked)
          }} />
        </div>
        <div className="memory-center-intelligence">
          <div>
            <div className="memory-center-intelligence-title">章节故事状态分析</div>
            <div className="memory-center-intelligence-desc">生成带原文证据的精确故事状态候选。</div>
          </div>
          <PurrSwitch checked={analysisEnabled} loading={settingsSaving} onChange={async (checked) => {
            if (await saveSettings({ story_memory_analysis_enabled: checked })) setAnalysisEnabled(checked)
          }} />
        </div>
        <div className="memory-center-intelligence">
          <div>
            <div className="memory-center-intelligence-title">低风险自动应用</div>
            <div className="memory-center-intelligence-desc">只处理白名单内的高置信度低风险新增。</div>
          </div>
          <PurrSpace size={8}>
            <PurrSelect
              size="small"
              value={minConfidence}
              disabled={!autoApplyEnabled || settingsSaving}
              options={[
                { value: 0.9, label: '≥ 90%' },
                { value: 0.95, label: '≥ 95%' },
                { value: 0.98, label: '≥ 98%' },
              ]}
              onChange={async (value) => {
                if (await saveSettings({ story_memory_auto_apply_min_confidence: value })) setMinConfidence(value)
              }}
            />
            <PurrSwitch checked={autoApplyEnabled} loading={settingsSaving} onChange={async (checked) => {
              if (await saveSettings({ story_memory_auto_apply_enabled: checked })) setAutoApplyEnabled(checked)
            }} />
          </PurrSpace>
        </div>
      </div>

      <div className="memory-center-toolbar unified-memory-toolbar">
        <div className="memory-center-filters unified-memory-filters">
          <PurrInput.Search
            allowClear
            placeholder="搜索全部记忆、故事状态与证据…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onSearch={() => void load()}
          />
          <PurrSelect allowClear placeholder="全部状态" value={status} options={STATUS_OPTIONS} onChange={setStatus} />
          <PurrSelect allowClear placeholder="全部类型" value={kind} options={ALL_KIND_OPTIONS} onChange={setKind} />
          <PurrSelect allowClear placeholder="全部来源" value={source} options={SOURCE_OPTIONS} onChange={setSource} />
        </div>
        <PurrButton onClick={() => void load()} loading={loading}>刷新</PurrButton>
      </div>

      <section className={`memory-center-create ${createOpen ? 'is-open' : 'is-collapsed'}`} aria-label="手动添加记忆">
        {!createOpen ? (
          <button type="button" className="memory-center-create-launcher" onClick={() => setCreateOpen(true)} disabled={bookId == null || !!semanticUnavailable}>
            <span className="memory-center-create-launcher-icon"><PlusIcon /></span>
            <span className="memory-center-create-launcher-copy">
              <span className="memory-center-create-launcher-title">手动添加记忆</span>
              <span className="memory-center-create-launcher-desc">补充设定、剧情事实、人物状态、伏笔或风格偏好</span>
            </span>
            <span className="memory-center-create-launcher-action">添加</span>
          </button>
        ) : (
          <div className="memory-center-create-body">
            <div className="memory-center-create-header">
              <div>
                <div className="memory-center-create-title">手动添加记忆</div>
                <div className="memory-center-create-desc">写下希望 AI 在后续创作中持续记住的信息。</div>
              </div>
              <PurrButton type="text" size="small" icon={<CloseIcon />} onClick={() => setCreateOpen(false)} aria-label="关闭添加记忆" />
            </div>
            <div className="memory-center-create-field">
              <div className="memory-center-create-label">记忆类型</div>
              <PurrRadio.Group
                value={newKind}
                onChange={(event) => {
                  const value = event.target.value as MemoryKind
                  setNewKind(value)
                  setNewStatus(value === 'summary' ? 'pending' : 'active')
                }}
                optionType="button"
                buttonStyle="solid"
                options={MEMORY_KIND_OPTIONS}
                className="memory-center-kind-options"
              />
              <div className="memory-center-create-hint">{KIND_HINTS[newKind]}</div>
            </div>
            <div className="memory-center-create-field">
              <div className="memory-center-create-field-header">
                <div className="memory-center-create-label">记忆内容</div>
                <span className="memory-center-create-count">{newContent.length} / 2000</span>
              </div>
              <PurrInput.TextArea
                autoFocus
                autoSize={{ minRows: 3, maxRows: 7 }}
                maxLength={2000}
                value={newContent}
                onChange={(event) => setNewContent(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                    event.preventDefault()
                    void handleCreate()
                  }
                }}
                placeholder={MEMORY_CONTENT_PLACEHOLDER}
              />
            </div>
            <div className="memory-center-create-options">
              <div className="memory-center-create-status">
                <div className="memory-center-create-label">保存后状态</div>
                <PurrRadio.Group value={newStatus} onChange={(event) => setNewStatus(event.target.value)} className="memory-center-create-status-choices">
                  <PurrRadio.Button value="active">
                    <CheckCircleIcon />
                    <span className="memory-center-create-status-copy"><span className="memory-center-create-status-title">立即生效</span><span className="memory-center-create-status-desc">马上参与召回</span></span>
                  </PurrRadio.Button>
                  <PurrRadio.Button value="pending">
                    <ClockIcon />
                    <span className="memory-center-create-status-copy"><span className="memory-center-create-status-title">待确认</span><span className="memory-center-create-status-desc">审核后生效</span></span>
                  </PurrRadio.Button>
                </PurrRadio.Group>
              </div>
              <div className="memory-center-create-pinned">
                <div><div className="memory-center-create-label">固定优先召回</div><div className="memory-center-create-hint">固定后，AI 会优先召回。</div></div>
                <PurrSwitch size="small" checked={newPinned} onChange={setNewPinned} />
              </div>
            </div>
            <div className="memory-center-create-footer">
              <span className="memory-center-create-shortcut">Ctrl/⌘ + Enter 快速添加</span>
              <PurrSpace size={8}>
                <PurrButton onClick={() => setCreateOpen(false)}>取消</PurrButton>
                <PurrButton type="primary" loading={creating} disabled={!newContent.trim()} onClick={() => void handleCreate()}>添加到记忆库</PurrButton>
              </PurrSpace>
            </div>
          </div>
        )}
      </section>

      <PurrSpin spinning={loading}>
        {displayEntries.length ? (
          <div className="unified-memory-list">
            {displayEntries.map((entry) => (
              entry.type === 'candidate'
                ? renderCandidateGroup(entry.deltaId, entry.items)
                : renderMemory(entry.item)
            ))}
          </div>
        ) : <PurrEmpty image={PurrEmpty.PRESENTED_IMAGE_SIMPLE} description="暂无符合条件的记忆" />}
      </PurrSpin>

      <PurrModal
        title="编辑记忆"
        open={!!editingItem}
        confirmLoading={editingSaving}
        okText="保存"
        cancelText="取消"
        onCancel={() => setEditingItem(null)}
        onOk={async () => {
          if (!editingItem || !editingContent.trim()) return
          setEditingSaving(true)
          const saved = await updateSemantic(editingItem, { content: editingContent.trim() })
          setEditingSaving(false)
          if (saved) setEditingItem(null)
        }}
      >
        <PurrInput.TextArea autoSize={{ minRows: 4, maxRows: 10 }} maxLength={2000} value={editingContent} onChange={(event) => setEditingContent(event.target.value)} />
      </PurrModal>

      <PurrModal title={historyItem ? `${historyItem.content} · 版本历史` : '版本历史'} open={!!historyItem} footer={null} onCancel={() => setHistoryItem(null)}>
        <PurrSpin spinning={historyLoading}>
          {history.length ? (
            <div className="unified-memory-history">
              {history.map((version) => (
                <div key={JSON.stringify(version)}>
                  <pre>{JSON.stringify(version, null, 2)}</pre>
                </div>
              ))}
            </div>
          ) : <PurrEmpty image={PurrEmpty.PRESENTED_IMAGE_SIMPLE} description="暂无版本记录" />}
        </PurrSpin>
      </PurrModal>

      <PurrModal
        title="组件审核建议"
        open={!!reviewItem}
        confirmLoading={reviewLoading}
        okText="应用建议"
        cancelText="关闭"
        okButtonProps={{ disabled: !reviewOperation?.review?.proposal }}
        onCancel={() => { setReviewItem(null); setReviewOperation(null) }}
        onOk={() => void applyReviewProposal()}
      >
        <PurrSpin spinning={reviewLoading}>
          {reviewOperation?.review ? (
            <pre>{JSON.stringify(reviewOperation.review, null, 2)}</pre>
          ) : <PurrEmpty image={PurrEmpty.PRESENTED_IMAGE_SIMPLE} description="组件没有给出可执行建议" />}
        </PurrSpin>
      </PurrModal>

      <PurrModal
        title={linkItem ? `${linkItem.content} · 记忆关系` : '记忆关系'}
        open={!!linkItem}
        confirmLoading={linkLoading}
        okText="建立关系"
        cancelText="关闭"
        okButtonProps={{ disabled: !linkTargetId }}
        onCancel={() => setLinkItem(null)}
        onOk={() => void createLink()}
      >
        <PurrSpin spinning={linkLoading}>
          <div style={{ display: 'grid', gap: 8, width: '100%' }}>
            <PurrSelect
              style={{ width: '100%' }}
              placeholder="选择另一条语义记忆"
              value={linkTargetId}
              options={items.filter((item) => item.source === 'semantic' && item.id !== linkItem?.id && item.version).map((item) => ({ value: item.id, label: item.content }))}
              onChange={setLinkTargetId}
            />
            <PurrSelect style={{ width: '100%' }} value={linkRelation} options={[...RELATION_OPTIONS]} onChange={setLinkRelation} />
            <PurrInput.TextArea value={linkNote} onChange={(event) => setLinkNote(event.target.value)} placeholder="关系说明（可选）" maxLength={1000} />
            {links.length ? links.map((link) => (
              <div key={link.key}><PurrTag>{link.relation}</PurrTag>{link.from.id} → {link.to.id}{link.note ? ` · ${link.note}` : ''}{link.valid ? '' : ' · 已失效'}</div>
            )) : <PurrEmpty image={PurrEmpty.PRESENTED_IMAGE_SIMPLE} description="暂无关系" />}
          </div>
        </PurrSpin>
      </PurrModal>
    </div>
  )
}

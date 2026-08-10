import React from 'react'
import { services } from '@/services'
import {
  CheckCircleIcon,
  EditIcon,
  FileTextIcon,
  LoadingIcon,
  PurrButton,
  PurrModal,
  PurrSelect,
  RefreshIcon,
  SaveIcon,
  usePurrConfirm,
} from '@/purr-components'
import { useAppFeedback } from '../hooks/useAppFeedback'
import KnowledgeMarkdownEditor from '@/components/KnowledgeMarkdownEditor'
import Markdown from '../Workspace/AiPanel/components/Markdown'
import type {
  EntityId,
  ScreenplayV2DeliverableRole,
  ScreenplayV2RevisionDetail,
  ScreenplayV2RevisionPart,
  ScreenplayV2RevisionSummary,
  ScreenplayV2WorkingCopy,
  ScreenplayV2Workspace,
} from '../types'
import {
  createScreenplayCommandId,
  type RevisionLibraryTarget,
} from './screenplayProjectModel'
import {
  buildWorkingCopyContent as buildWorkingCopyEditorContent,
  canApplyRevision,
  mergeRequestedRevision,
  reviewComparisonForPart,
  revisionLibraryNavigationDecision,
  revisionLibraryWorkspacePresentation,
  resolveHistoryRevisionId,
  resolveRevisionLibrarySelection,
  shouldShowRevisionDirectory,
  workingCopyDraftEquals,
  workingCopyEditorDraft,
  type WorkingCopyEditorDraft,
} from './revisionLibraryModel'
import {
  revisionPartKey,
  revisionPartKindLabel,
  revisionPartMarkdown,
  revisionPartTitle,
} from './revisionDocumentView'

const ROLE_LABELS: Record<ScreenplayV2DeliverableRole, string> = {
  sourceAnalysis: '原作分析',
  creativeBrief: '创作简报',
  structure: '结构设计',
  sceneList: '场景规划',
  screenplayDraft: '剧本正文',
  review: '审阅修订',
}

const STATUS_LABELS = {
  current: '当前版本',
  historical: '历史版本',
  candidate: '待应用',
} as const

type RevisionHistoryPage = {
  items: ScreenplayV2RevisionSummary[]
  nextCursor: string | null
}

interface RevisionLibraryModalProps {
  open: boolean
  projectId: EntityId
  workspace: ScreenplayV2Workspace
  target?: RevisionLibraryTarget | null
  readOnly: boolean
  onClose: () => void
  onWorkspaceChange: (workspace: ScreenplayV2Workspace) => void | Promise<void>
}

function formatTime(value?: string | null): string {
  if (!value) return '时间未知'
  const normalized = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function workingCopyPart(
  part: Record<string, unknown>,
  index: number,
): ScreenplayV2RevisionPart {
  const rawType = String(part.type || 'document')
  const type: ScreenplayV2RevisionPart['type'] = (
    rawType === 'episode'
    || rawType === 'scene'
    || rawType === 'reviewIssueGroup'
  ) ? rawType : 'document'
  const rawPosition = Number(part.position ?? index + 1)
  return {
    type,
    key: String(part.key || index + 1),
    position: Number.isFinite(rawPosition) ? rawPosition : index + 1,
    payload: isRecord(part.payload) ? part.payload : {},
    contentText: String(part.contentText || ''),
    contentDigest: '',
  }
}

export default function RevisionLibraryModal({
  open,
  projectId,
  workspace,
  target = null,
  readOnly,
  onClose,
  onWorkspaceChange,
}: RevisionLibraryModalProps) {
  const { message } = useAppFeedback()
  const confirm = usePurrConfirm()
  const [role, setRole] = React.useState<ScreenplayV2DeliverableRole>(() => (
    resolveRevisionLibrarySelection(workspace, target).role
  ))
  const [history, setHistory] = React.useState<RevisionHistoryPage>({
    items: [],
    nextCursor: null,
  })
  const [historyLoading, setHistoryLoading] = React.useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = React.useState(false)
  const [selectedRevisionId, setSelectedRevisionId] = React.useState<string | null>(null)
  const [revisionDetail, setRevisionDetail] = React.useState<ScreenplayV2RevisionDetail | null>(null)
  const [selectedPartKey, setSelectedPartKey] = React.useState<string | null>(null)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [applyingRevisionId, setApplyingRevisionId] = React.useState<string | null>(null)
  const [seedingRevisionId, setSeedingRevisionId] = React.useState<string | null>(null)
  const [workingCopy, setWorkingCopy] = React.useState<ScreenplayV2WorkingCopy | null>(null)
  const [savedWorkingCopyDraft, setSavedWorkingCopyDraft] = React.useState<WorkingCopyEditorDraft | null>(null)
  const [mainTextDraft, setMainTextDraft] = React.useState('')
  const [partTextDrafts, setPartTextDrafts] = React.useState<string[]>([])
  const [selectedWorkingCopyPartKey, setSelectedWorkingCopyPartKey] = React.useState<string | null>(null)
  const [savingWorkingCopy, setSavingWorkingCopy] = React.useState(false)
  const [publishingWorkingCopy, setPublishingWorkingCopy] = React.useState(false)
  const [matchingReview, setMatchingReview] = React.useState<ScreenplayV2RevisionDetail | null>(null)
  const [matchingReviewLoading, setMatchingReviewLoading] = React.useState(false)
  const [comparisonTab, setComparisonTab] = React.useState<'draft' | 'review'>('draft')

  const loadHistory = React.useCallback(async (
    targetRole: ScreenplayV2DeliverableRole,
    cursor?: string,
    preferredRevisionId?: string | null,
  ) => {
    cursor ? setHistoryLoadingMore(true) : setHistoryLoading(true)
    const result = await services.screenplay.listScreenplayV2RevisionHistory({
      projectId,
      role: targetRole,
      cursor,
      limit: 30,
    })
    cursor ? setHistoryLoadingMore(false) : setHistoryLoading(false)
    if (!result.success || !result.data) {
      message.error(result.error || '读取版本历史失败')
      return
    }
    setHistory((current) => {
      const requested = preferredRevisionId
        ? current.items.find((item) => item.id === preferredRevisionId)
        : undefined
      const items = cursor
        ? [...current.items, ...result.data.items]
        : result.data.items
      return {
        items: requested ? mergeRequestedRevision(items, requested) : items,
        nextCursor: result.data.nextCursor,
      }
    })
    if (!cursor) {
      setSelectedRevisionId((current) => resolveHistoryRevisionId(
        result.data.items,
        current,
        preferredRevisionId,
      ))
    }
  }, [message, projectId])

  React.useEffect(() => {
    if (!open) return
    const selection = resolveRevisionLibrarySelection(workspace, target)
    setRole(selection.role)
    setHistory({ items: [], nextCursor: null })
    setSelectedRevisionId(selection.revisionId)
    setRevisionDetail(null)
    void loadHistory(selection.role, undefined, selection.revisionId)
  }, [
    loadHistory,
    open,
    projectId,
    target?.revisionId,
    target?.role,
  ]) // workspace updates must not reset an open reader

  React.useEffect(() => {
    if (!open || !selectedRevisionId) {
      setRevisionDetail(null)
      return
    }
    let canceled = false
    setDetailLoading(true)
    void services.screenplay.getScreenplayV2Revision({
      revisionId: selectedRevisionId,
      view: 'full',
    }).then((result) => {
      if (canceled) return
      setDetailLoading(false)
      if (!result.success || !result.data) {
        message.error(result.error || '读取版本内容失败')
        return
      }
      if (result.data.role !== role) {
        setRevisionDetail(null)
        message.error('目标版本不属于当前文档分类')
        return
      }
      setRevisionDetail(result.data)
      setHistory((current) => ({
        ...current,
        items: mergeRequestedRevision(current.items, result.data),
      }))
    })
    return () => {
      canceled = true
    }
  }, [message, open, role, selectedRevisionId])

  React.useEffect(() => {
    if (!open || role !== 'screenplayDraft' || !revisionDetail) {
      setMatchingReview(null)
      setMatchingReviewLoading(false)
      return
    }
    let canceled = false
    setMatchingReview(null)
    setMatchingReviewLoading(true)
    void services.screenplay.getScreenplayV2LatestReviewForDraft({
      projectId,
      draftRevisionId: revisionDetail.id,
    }).then((result) => {
      if (canceled) return
      setMatchingReviewLoading(false)
      if (!result.success) {
        message.error(result.error || '读取对应审阅失败')
        return
      }
      setMatchingReview(result.data ?? null)
    })
    return () => {
      canceled = true
    }
  }, [message, open, projectId, revisionDetail, role])

  const performRoleSelection = React.useCallback((nextRole: ScreenplayV2DeliverableRole) => {
    setRole(nextRole)
    setHistory({ items: [], nextCursor: null })
    setSelectedRevisionId(null)
    setRevisionDetail(null)
    void loadHistory(nextRole)
  }, [loadHistory])

  const refreshWorkspace = React.useCallback(async () => {
    const result = await services.screenplay.getScreenplayV2Workspace({ projectId })
    if (result.success && result.data) {
      await onWorkspaceChange(result.data)
      return result.data
    }
    return null
  }, [onWorkspaceChange, projectId])

  const applyRevision = React.useCallback(async (revision: ScreenplayV2RevisionSummary) => {
    if (readOnly || applyingRevisionId) return
    setApplyingRevisionId(revision.id)
    try {
      const latestWorkspace = await refreshWorkspace() ?? workspace
      if (latestWorkspace.workflow.heads[revision.role]?.id === revision.id) {
        message.info('这个版本已经是当前版本')
        return
      }
      const commandId = createScreenplayCommandId('apply-history-revision')
      let result = await services.screenplay.acceptScreenplayV2Revision({
        commandId,
        projectId,
        revisionId: revision.id,
        expectedProjectRevision: latestWorkspace.project.revision,
      })
      if (!result.success && result.error?.includes('下游版本失效')) {
        const decision = await confirm({
          title: '应用并重置下游当前版本？',
          content: '这个历史版本会改变上游基线。依赖现有基线的下游当前版本将失效，但所有 Revision 历史仍会保留。',
          confirmText: '应用并重置',
          confirmVariant: 'danger',
          cancelText: '取消',
        })
        if (decision !== 'confirm') return
        result = await services.screenplay.acceptScreenplayV2Revision({
          commandId,
          projectId,
          revisionId: revision.id,
          expectedProjectRevision: latestWorkspace.project.revision,
          confirmInvalidation: true,
        })
      }
      if (!result.success || !result.data?.workspace) {
        message.error(result.error || '应用历史版本失败')
        return
      }
      await onWorkspaceChange(result.data.workspace)
      await loadHistory(role)
      message.success('已应用为当前版本')
    } finally {
      setApplyingRevisionId(null)
    }
  }, [
    applyingRevisionId,
    confirm,
    loadHistory,
    message,
    onWorkspaceChange,
    projectId,
    readOnly,
    refreshWorkspace,
    role,
    workspace,
  ])

  const showWorkingCopyEditor = React.useCallback((copy: ScreenplayV2WorkingCopy) => {
    const draft = workingCopyEditorDraft(copy)
    const initialParts = Array.isArray(copy.content.parts)
      ? copy.content.parts.filter(isRecord)
      : []
    setWorkingCopy(copy)
    setSavedWorkingCopyDraft(draft)
    setMainTextDraft(draft.mainText)
    setPartTextDrafts(draft.partText)
    setSelectedWorkingCopyPartKey(
      initialParts[0]
        ? revisionPartKey(workingCopyPart(initialParts[0], 0))
        : 'document:main:0',
    )
  }, [])

  const openWorkingCopy = React.useCallback(async (
    revision: ScreenplayV2RevisionSummary,
  ) => {
    if (readOnly || seedingRevisionId) return
    const latestWorkspace = await refreshWorkspace() ?? workspace
    const existing = latestWorkspace.workingCopies.find(
      (copy) => copy.role === revision.role,
    )
    if (existing?.baseRevisionId === revision.id) {
      showWorkingCopyEditor(existing)
      return
    }
    if (existing) {
      const decision = await confirm({
        title: '替换未发布的编辑内容？',
        content: '当前未发布的修改将被这个版本覆盖，已发布版本不受影响。',
        confirmText: '替换并编辑',
        confirmVariant: 'primary',
        cancelText: '取消',
      })
      if (decision !== 'confirm') return
    }
    setSeedingRevisionId(revision.id)
    const result = await services.screenplay.createScreenplayV2WorkingCopyFromRevision({
      commandId: createScreenplayCommandId('edit-history-revision'),
      projectId,
      revisionId: revision.id,
      expectedProjectRevision: latestWorkspace.project.revision,
      expectedWorkingCopyRevision: existing?.revision,
    })
    setSeedingRevisionId(null)
    if (!result.success || !result.data) {
      message.error(result.error || '创建编辑草稿失败')
      return
    }
    const copy = result.data
    showWorkingCopyEditor(copy)
    await refreshWorkspace()
  }, [
    confirm,
    message,
    projectId,
    readOnly,
    refreshWorkspace,
    seedingRevisionId,
    showWorkingCopyEditor,
    workspace,
  ])

  const buildWorkingCopyContent = React.useCallback(() => {
    if (!workingCopy) throw new Error('编辑草稿不存在')
    return buildWorkingCopyEditorContent(workingCopy, {
      mainText: mainTextDraft,
      partText: partTextDrafts,
    })
  }, [mainTextDraft, partTextDrafts, workingCopy])

  const saveWorkingCopy = React.useCallback(async (): Promise<ScreenplayV2WorkingCopy | null> => {
    if (!workingCopy || savingWorkingCopy) return null
    let content: Record<string, unknown>
    try {
      content = buildWorkingCopyContent()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '编辑内容无效')
      return null
    }
    setSavingWorkingCopy(true)
    const result = await services.screenplay.updateScreenplayV2WorkingCopy({
      workingCopyId: workingCopy.id,
      expectedRevision: workingCopy.revision,
      content,
    })
    setSavingWorkingCopy(false)
    if (!result.success || !result.data) {
      message.error(result.error || '保存编辑草稿失败')
      return null
    }
    setWorkingCopy(result.data)
    setSavedWorkingCopyDraft(workingCopyEditorDraft(result.data))
    await refreshWorkspace()
    message.success('编辑草稿已保存')
    return result.data
  }, [
    buildWorkingCopyContent,
    message,
    refreshWorkspace,
    savingWorkingCopy,
    workingCopy,
  ])

  const publishWorkingCopy = React.useCallback(async () => {
    if (!workingCopy || publishingWorkingCopy) return
    const saved = await saveWorkingCopy()
    if (!saved) return
    const latestWorkspace = await refreshWorkspace() ?? workspace
    setPublishingWorkingCopy(true)
    const result = await services.screenplay.publishScreenplayV2WorkingCopy({
      commandId: createScreenplayCommandId('publish-working-copy'),
      workingCopyId: saved.id,
      expectedProjectRevision: latestWorkspace.project.revision,
      expectedWorkingCopyRevision: saved.revision,
    })
    setPublishingWorkingCopy(false)
    if (!result.success || !result.data?.workspace || !result.data.revision) {
      message.error(result.error || '发布候选版本失败')
      return
    }
    await onWorkspaceChange(result.data.workspace)
    setWorkingCopy(null)
    setSavedWorkingCopyDraft(null)
    setSelectedRevisionId(result.data.revision.id)
    await loadHistory(result.data.revision.role)
    message.success('已发布为候选版本，确认内容后可应用')
  }, [
    loadHistory,
    message,
    onWorkspaceChange,
    publishingWorkingCopy,
    refreshWorkspace,
    saveWorkingCopy,
    workingCopy,
    workspace,
  ])

  const currentWorkingCopyDraft = React.useMemo<WorkingCopyEditorDraft>(() => ({
    mainText: mainTextDraft,
    partText: partTextDrafts,
  }), [mainTextDraft, partTextDrafts])
  const editingBusy = savingWorkingCopy || publishingWorkingCopy
  const workingCopyDirty = Boolean(
    workingCopy
    && savedWorkingCopyDraft
    && !workingCopyDraftEquals(savedWorkingCopyDraft, currentWorkingCopyDraft),
  )
  const workspacePresentation = revisionLibraryWorkspacePresentation(
    workingCopy != null,
  )

  const requestNavigation = React.useCallback(async (
    action: () => void | Promise<void>,
  ) => {
    const decision = revisionLibraryNavigationDecision({
      editing: workingCopy != null,
      dirty: workingCopyDirty,
      busy: editingBusy,
    })
    if (decision === 'block') return
    if (decision === 'confirm') {
      const result = await confirm({
        title: '当前修改尚未保存',
        content: '保存后继续当前操作，或放弃本次尚未保存的修改。',
        confirmText: '保存并继续',
        confirmVariant: 'primary',
        cancelText: '留在此处',
        actions: [{
          id: 'discard',
          label: '放弃未保存修改',
          variant: 'danger',
        }],
      })
      if (result === 'cancel') return
      if (result === 'confirm') {
        const saved = await saveWorkingCopy()
        if (!saved) return
      } else if (result === 'discard' && savedWorkingCopyDraft) {
        setMainTextDraft(savedWorkingCopyDraft.mainText)
        setPartTextDrafts([...savedWorkingCopyDraft.partText])
      } else {
        return
      }
    }
    await action()
  }, [
    confirm,
    editingBusy,
    saveWorkingCopy,
    savedWorkingCopyDraft,
    workingCopy,
    workingCopyDirty,
  ])

  const exitEditing = React.useCallback(() => {
    void requestNavigation(() => {
      setWorkingCopy(null)
      setSavedWorkingCopyDraft(null)
    })
  }, [requestNavigation])

  const selectRole = React.useCallback((nextRole: ScreenplayV2DeliverableRole) => {
    void requestNavigation(() => {
      setWorkingCopy(null)
      setSavedWorkingCopyDraft(null)
      performRoleSelection(nextRole)
    })
  }, [performRoleSelection, requestNavigation])

  const selectRevision = React.useCallback((revisionId: string) => {
    void requestNavigation(() => {
      setWorkingCopy(null)
      setSavedWorkingCopyDraft(null)
      setSelectedRevisionId(revisionId)
    })
  }, [requestNavigation])

  const closeLibrary = React.useCallback(() => {
    void requestNavigation(() => {
      setWorkingCopy(null)
      setSavedWorkingCopyDraft(null)
      onClose()
    })
  }, [onClose, requestNavigation])

  const selectedSummary = history.items.find(
    (item) => item.id === selectedRevisionId,
  ) ?? (
    revisionDetail?.id === selectedRevisionId ? revisionDetail : undefined
  )
  const mainPart = revisionDetail?.parts.find(
    (part) => part.type === 'document' && part.key === 'main',
  )
  const orderedParts = revisionDetail && mainPart && revisionDetail.parts.length > 1
    ? [...revisionDetail.parts.filter((part) => part !== mainPart), mainPart]
    : revisionDetail?.parts ?? []
  const selectedPart = revisionDetail?.parts.find(
    (part) => revisionPartKey(part) === selectedPartKey,
  ) ?? orderedParts[0] ?? null
  const copyParts = workingCopy && Array.isArray(workingCopy.content.parts)
    ? workingCopy.content.parts.filter(isRecord)
    : []
  const copyRevisionParts = copyParts.map(workingCopyPart)
  const workingCopyMainPart: ScreenplayV2RevisionPart | null = workingCopy ? {
    type: 'document',
    key: 'main',
    position: 0,
    payload: isRecord(workingCopy.content.contentJson)
      ? workingCopy.content.contentJson
      : {},
    contentText: String(workingCopy.content.contentText || ''),
    contentDigest: '',
  } : null
  const workingCopyEditorParts = workingCopyMainPart
    ? [...copyRevisionParts, workingCopyMainPart]
    : copyRevisionParts
  const selectedWorkingCopyPart = workingCopyEditorParts.find(
    (part) => revisionPartKey(part) === selectedWorkingCopyPartKey,
  ) ?? workingCopyEditorParts[0] ?? null
  const selectedWorkingCopyPartIndex = selectedWorkingCopyPart
    ? copyRevisionParts.findIndex(
        (part) => revisionPartKey(part) === revisionPartKey(selectedWorkingCopyPart),
      )
    : -1
  const activeParts = workingCopy ? workingCopyEditorParts : orderedParts
  const activeSelectedPart = workingCopy ? selectedWorkingCopyPart : selectedPart
  const activeMainPart = workingCopy ? workingCopyMainPart : mainPart
  const activeSelectedPartIndex = activeSelectedPart
    ? activeParts.indexOf(activeSelectedPart)
    : -1
  const showDirectory = shouldShowRevisionDirectory(activeParts)
  const reviewComparison = (
    role === 'screenplayDraft'
    && revisionDetail
    && activeSelectedPart
    && !matchingReviewLoading
  )
    ? reviewComparisonForPart(
        revisionDetail,
        matchingReview,
        activeSelectedPart,
      )
    : null
  const roleOptions = workspace.deliverables.map((deliverable) => ({
    value: deliverable.role,
    label: ROLE_LABELS[deliverable.role],
  }))
  const revisionOptions = history.items.map((revision) => ({
    value: revision.id,
    label: `v${revision.revisionNo} · ${STATUS_LABELS[revision.status || 'candidate']} · ${formatTime(revision.createdAt)}`,
  }))

  React.useEffect(() => {
    if (!revisionDetail) {
      setSelectedPartKey(null)
      return
    }
    const defaultPart = revisionDetail.parts.length > 1
      ? revisionDetail.parts.find((part) => part !== mainPart)
      : mainPart ?? revisionDetail.parts[0]
    if (!defaultPart) {
      setSelectedPartKey(null)
      return
    }
    setSelectedPartKey((current) => (
      revisionDetail.parts.some((part) => revisionPartKey(part) === current)
        ? current
        : revisionPartKey(defaultPart)
    ))
  }, [mainPart, revisionDetail])

  return (
    <PurrModal
      title={workingCopy ? `编辑${ROLE_LABELS[workingCopy.role]}` : '项目文档'}
      open={open}
      width="min(1280px, calc(100vw - 48px))"
      footer={null}
      destroyOnHidden
      onCancel={closeLibrary}
      className="screenplay-revision-library-modal"
    >
      <div className="screenplay-revision-library">
        <header className="screenplay-revision-library__toolbar">
          <div className="screenplay-revision-library__selectors">
            <div className="screenplay-revision-library__field">
              <span>文档</span>
              <PurrSelect<ScreenplayV2DeliverableRole>
                value={role}
                options={roleOptions}
                className="screenplay-revision-library__role-select"
                onChange={selectRole}
              />
            </div>
            <div className="screenplay-revision-library__field">
              <span>版本</span>
              <PurrSelect<string>
                value={selectedRevisionId}
                options={revisionOptions}
                placeholder={historyLoading ? '读取版本…' : '暂无版本'}
                disabled={historyLoading && history.items.length === 0}
                className="screenplay-revision-library__version-select"
                onChange={selectRevision}
              />
            </div>
            <PurrButton
              type="text"
              size="small"
              icon={<RefreshIcon />}
              loading={historyLoading}
              disabled={workingCopy != null || editingBusy}
              onClick={() => void loadHistory(role)}
              aria-label="刷新版本列表"
              title="刷新版本列表"
            >
              刷新
            </PurrButton>
            {history.nextCursor && (
              <PurrButton
                type="text"
                size="small"
                loading={historyLoadingMore}
                disabled={workingCopy != null || editingBusy}
                onClick={() => void loadHistory(role, history.nextCursor || undefined)}
              >
                更早版本
              </PurrButton>
            )}
          </div>
          {workingCopy && workspacePresentation.actions === 'workingCopy' ? (
            <div className="screenplay-revision-library__actions">
              <PurrButton disabled={editingBusy} onClick={exitEditing}>
                退出编辑
              </PurrButton>
              <PurrButton
                icon={<SaveIcon />}
                loading={savingWorkingCopy}
                disabled={publishingWorkingCopy || !workingCopyDirty}
                onClick={() => void saveWorkingCopy()}
              >
                保存草稿
              </PurrButton>
              <PurrButton
                type="primary"
                icon={<CheckCircleIcon />}
                loading={publishingWorkingCopy}
                disabled={savingWorkingCopy}
                onClick={() => void publishWorkingCopy()}
              >
                发布候选版本
              </PurrButton>
            </div>
          ) : revisionDetail && selectedSummary && !readOnly ? (
            <div className="screenplay-revision-library__actions">
              <PurrButton
                icon={<EditIcon />}
                loading={seedingRevisionId === revisionDetail.id}
                disabled={seedingRevisionId != null || applyingRevisionId != null}
                onClick={() => void openWorkingCopy(revisionDetail)}
              >
                基于此版本编辑
              </PurrButton>
              {selectedSummary.status !== 'current' && (
                <PurrButton
                  type="primary"
                  icon={<CheckCircleIcon />}
                  loading={applyingRevisionId === revisionDetail.id}
                  disabled={
                    !canApplyRevision(selectedSummary)
                    || applyingRevisionId != null
                    || seedingRevisionId != null
                  }
                  onClick={() => void applyRevision(selectedSummary)}
                >
                  应用此版本
                </PurrButton>
              )}
            </div>
          ) : null}
        </header>
        {historyLoading && history.items.length === 0 ? (
          <div className="screenplay-revision-library__loading"><LoadingIcon />读取版本…</div>
        ) : history.items.length === 0 ? (
          <div className="screenplay-revision-library__empty">这个文档还没有发布版本</div>
        ) : detailLoading ? (
          <div className="screenplay-revision-library__loading"><LoadingIcon />读取内容…</div>
        ) : !revisionDetail || !selectedSummary ? (
          <div className="screenplay-revision-library__empty">
            <FileTextIcon />选择一个版本查看内容
          </div>
        ) : (
          <>
            {selectedSummary.applicability === 'stale' && (
              <div className="screenplay-revision-library__stale">
                该版本的上游内容已经变化，不能直接应用；可以基于它创建编辑草稿。
              </div>
            )}
            <div className={`screenplay-revision-document-browser ${showDirectory ? '' : 'is-without-directory'}`}>
              {showDirectory && (
                <nav
                  className="screenplay-revision-document-browser__list"
                  aria-label={workingCopy ? '可编辑内容目录' : '当前版本的内容目录'}
                >
                  <header><strong>{workingCopy ? '编辑内容' : '内容目录'}</strong></header>
                  <div>
                    {activeParts.map((part, index) => {
                      const partKey = revisionPartKey(part)
                      const selected = activeSelectedPart
                        ? partKey === revisionPartKey(activeSelectedPart)
                        : index === 0
                      const title = part === activeMainPart && activeParts.length > 1
                        ? '文档概览'
                        : revisionPartTitle(part, index)
                      return (
                        <button
                          type="button"
                          className={selected ? 'is-selected' : ''}
                          aria-pressed={selected}
                          onClick={() => {
                            workingCopy
                              ? setSelectedWorkingCopyPartKey(partKey)
                              : setSelectedPartKey(partKey)
                          }}
                          key={partKey}
                        >
                          <FileTextIcon />
                          <span>
                            <strong>{title}</strong>
                            <small>{revisionPartKindLabel(part)}</small>
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </nav>
              )}
              <div className="screenplay-revision-document-browser__content">
                {activeSelectedPart ? role === 'screenplayDraft' ? (
                  <>
                    <div className="screenplay-revision-comparison__tabs" aria-label="正文与审阅切换">
                      <button
                        type="button"
                        className={comparisonTab === 'draft' ? 'is-active' : ''}
                        onClick={() => setComparisonTab('draft')}
                      >正文</button>
                      <button
                        type="button"
                        className={comparisonTab === 'review' ? 'is-active' : ''}
                        onClick={() => setComparisonTab('review')}
                      >审阅</button>
                    </div>
                    <div className="screenplay-revision-comparison">
                      <section className={`screenplay-revision-comparison__draft ${comparisonTab === 'draft' ? '' : 'is-mobile-hidden'}`}>
                        <header>
                          <span>{revisionPartKindLabel(activeSelectedPart)}</span>
                          <h4>
                            {activeSelectedPart === activeMainPart && activeParts.length > 1
                              ? '文档概览'
                              : revisionPartTitle(activeSelectedPart, activeSelectedPartIndex)}
                          </h4>
                        </header>
                        {workspacePresentation.body === 'inlineEditor' ? (
                          <div className="screenplay-revision-inline-editor">
                            <KnowledgeMarkdownEditor
                              documentKey={revisionPartKey(activeSelectedPart)}
                              value={selectedWorkingCopyPartIndex === -1
                                ? mainTextDraft
                                : partTextDrafts[selectedWorkingCopyPartIndex] || ''}
                              onChange={(value) => {
                                if (selectedWorkingCopyPartIndex === -1) {
                                  setMainTextDraft(value)
                                  return
                                }
                                setPartTextDrafts((current) => current.map((item, index) => (
                                  index === selectedWorkingCopyPartIndex ? value : item
                                )))
                              }}
                            />
                          </div>
                        ) : (
                          <Markdown preserveSoftBreaks>
                            {revisionPartMarkdown(activeSelectedPart)}
                          </Markdown>
                        )}
                      </section>
                      <aside className={`screenplay-revision-comparison__review ${comparisonTab === 'review' ? '' : 'is-mobile-hidden'}`}>
                        <header>
                          <span>审阅意见</span>
                          <h4>
                            {matchingReview
                              ? `基于 v${revisionDetail.revisionNo} · 审阅 v${matchingReview.revisionNo}`
                              : `基于 v${revisionDetail.revisionNo}`}
                          </h4>
                        </header>
                        {matchingReviewLoading ? (
                          <div className="screenplay-revision-library__loading"><LoadingIcon />读取审阅…</div>
                        ) : reviewComparison?.kind === 'failed' ? (
                          <div className="screenplay-revision-comparison__state is-failed">
                            <strong>{reviewComparison.message}</strong>
                            <span>本集没有生成可处理的审阅意见，请重新审阅。</span>
                          </div>
                        ) : reviewComparison?.kind === 'unavailable' || !reviewComparison ? (
                          <div className="screenplay-revision-comparison__state">
                            该版本尚无对应审阅
                          </div>
                        ) : (
                          <Markdown preserveSoftBreaks>{reviewComparison.markdown}</Markdown>
                        )}
                      </aside>
                    </div>
                  </>
                ) : (
                  <article className="screenplay-revision-document-browser__reader">
                    <header>
                      <span>{revisionPartKindLabel(activeSelectedPart)}</span>
                      <h4>
                        {activeSelectedPart === activeMainPart && activeParts.length > 1
                          ? '文档概览'
                          : revisionPartTitle(activeSelectedPart, activeSelectedPartIndex)}
                      </h4>
                    </header>
                    {workspacePresentation.body === 'inlineEditor' ? (
                      <div className="screenplay-revision-inline-editor">
                        <KnowledgeMarkdownEditor
                          documentKey={revisionPartKey(activeSelectedPart)}
                          value={selectedWorkingCopyPartIndex === -1
                            ? mainTextDraft
                            : partTextDrafts[selectedWorkingCopyPartIndex] || ''}
                          onChange={(value) => {
                            if (selectedWorkingCopyPartIndex === -1) {
                              setMainTextDraft(value)
                              return
                            }
                            setPartTextDrafts((current) => current.map((item, index) => (
                              index === selectedWorkingCopyPartIndex ? value : item
                            )))
                          }}
                        />
                      </div>
                    ) : (
                      <Markdown preserveSoftBreaks>
                        {revisionPartMarkdown(activeSelectedPart)}
                      </Markdown>
                    )}
                  </article>
                ) : (
                  <div className="screenplay-revision-library__empty">
                    暂无可阅读的文档内容
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </PurrModal>
  )
}

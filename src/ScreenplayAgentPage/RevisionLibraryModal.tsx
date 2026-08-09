import React from 'react'
import { services } from '@/services'
import {
  CheckCircleIcon,
  EditIcon,
  FileTextIcon,
  HistoryIcon,
  LoadingIcon,
  PurrButton,
  PurrInput,
  PurrModal,
  RefreshIcon,
  SaveIcon,
  usePurrConfirm,
} from '@/purr-components'
import { useAppFeedback } from '../hooks/useAppFeedback'
import type {
  EntityId,
  ScreenplayV2DeliverableRole,
  ScreenplayV2RevisionDetail,
  ScreenplayV2RevisionPart,
  ScreenplayV2RevisionSummary,
  ScreenplayV2WorkingCopy,
  ScreenplayV2Workspace,
} from '../types'
import { createScreenplayCommandId } from './operationWorkflow'
import {
  buildWorkingCopyContent as buildWorkingCopyEditorContent,
  canApplyRevision,
  workingCopyEditorDraft,
} from './revisionLibraryModel'

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
  readOnly: boolean
  onClose: () => void
  onWorkspaceChange: (workspace: ScreenplayV2Workspace) => void | Promise<void>
}

function defaultRole(workspace: ScreenplayV2Workspace): ScreenplayV2DeliverableRole {
  const roles = workspace.deliverables.map((item) => item.role)
  return [...roles].reverse().find((role) => (
    workspace.workflow.heads[role]
    || workspace.candidates.some((candidate) => candidate.role === role)
    || workspace.workingCopies.some((copy) => copy.role === role)
  )) ?? roles[0] ?? 'creativeBrief'
}

function createdByLabel(createdBy: ScreenplayV2RevisionDetail['createdBy']): string {
  if (createdBy === 'agent') return 'Agent 生成'
  return '用户编辑'
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

function partLabel(part: ScreenplayV2RevisionPart, index: number): string {
  if (part.type === 'episode') return `第 ${part.key} 集`
  if (part.type === 'scene') return `场景 ${part.key}`
  if (part.type === 'reviewIssueGroup') return `问题组 ${part.key}`
  return index === 0 ? '主文档' : part.key
}

export default function RevisionLibraryModal({
  open,
  projectId,
  workspace,
  readOnly,
  onClose,
  onWorkspaceChange,
}: RevisionLibraryModalProps) {
  const { message } = useAppFeedback()
  const confirm = usePurrConfirm()
  const [role, setRole] = React.useState<ScreenplayV2DeliverableRole>(() => (
    defaultRole(workspace)
  ))
  const [history, setHistory] = React.useState<RevisionHistoryPage>({
    items: [],
    nextCursor: null,
  })
  const [historyLoading, setHistoryLoading] = React.useState(false)
  const [historyLoadingMore, setHistoryLoadingMore] = React.useState(false)
  const [selectedRevisionId, setSelectedRevisionId] = React.useState<string | null>(null)
  const [revisionDetail, setRevisionDetail] = React.useState<ScreenplayV2RevisionDetail | null>(null)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [applyingRevisionId, setApplyingRevisionId] = React.useState<string | null>(null)
  const [seedingRevisionId, setSeedingRevisionId] = React.useState<string | null>(null)
  const [workingCopy, setWorkingCopy] = React.useState<ScreenplayV2WorkingCopy | null>(null)
  const [mainJsonDraft, setMainJsonDraft] = React.useState('{}')
  const [mainTextDraft, setMainTextDraft] = React.useState('')
  const [partJsonDrafts, setPartJsonDrafts] = React.useState<string[]>([])
  const [partTextDrafts, setPartTextDrafts] = React.useState<string[]>([])
  const [savingWorkingCopy, setSavingWorkingCopy] = React.useState(false)
  const [publishingWorkingCopy, setPublishingWorkingCopy] = React.useState(false)

  const loadHistory = React.useCallback(async (
    targetRole: ScreenplayV2DeliverableRole,
    cursor?: string,
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
    setHistory((current) => ({
      items: cursor
        ? [...current.items, ...result.data.items]
        : result.data.items,
      nextCursor: result.data.nextCursor,
    }))
    if (!cursor) {
      setSelectedRevisionId((current) => (
        result.data.items.some((item) => item.id === current)
          ? current
          : result.data.items[0]?.id ?? null
      ))
    }
  }, [message, projectId])

  React.useEffect(() => {
    if (!open) return
    const nextRole = defaultRole(workspace)
    setRole(nextRole)
    void loadHistory(nextRole)
  }, [open, projectId]) // eslint-disable-line react-hooks/exhaustive-deps

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
      setRevisionDetail(result.data)
    })
    return () => {
      canceled = true
    }
  }, [message, open, selectedRevisionId])

  const selectRole = React.useCallback((nextRole: ScreenplayV2DeliverableRole) => {
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

  const openWorkingCopy = React.useCallback(async (
    revision: ScreenplayV2RevisionSummary,
  ) => {
    if (readOnly || seedingRevisionId) return
    const latestWorkspace = await refreshWorkspace() ?? workspace
    const existing = latestWorkspace.workingCopies.find(
      (copy) => copy.role === revision.role,
    )
    if (existing) {
      const decision = await confirm({
        title: '用这个版本重置 Working Copy？',
        content: '该交付物当前未发布的编辑内容会被替换；已经发布的 Revision 和当前版本不会被删除。',
        confirmText: '重置并编辑',
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
      message.error(result.error || '创建 Working Copy 失败')
      return
    }
    const copy = result.data
    const draft = workingCopyEditorDraft(copy)
    setWorkingCopy(copy)
    setMainJsonDraft(draft.mainJson)
    setMainTextDraft(draft.mainText)
    setPartJsonDrafts(draft.partJson)
    setPartTextDrafts(draft.partText)
    await refreshWorkspace()
  }, [
    confirm,
    message,
    projectId,
    readOnly,
    refreshWorkspace,
    seedingRevisionId,
    workspace,
  ])

  const buildWorkingCopyContent = React.useCallback(() => {
    if (!workingCopy) throw new Error('Working Copy 不存在')
    return buildWorkingCopyEditorContent(workingCopy, {
      mainJson: mainJsonDraft,
      mainText: mainTextDraft,
      partJson: partJsonDrafts,
      partText: partTextDrafts,
    })
  }, [mainJsonDraft, mainTextDraft, partJsonDrafts, partTextDrafts, workingCopy])

  const saveWorkingCopy = React.useCallback(async (): Promise<ScreenplayV2WorkingCopy | null> => {
    if (!workingCopy || savingWorkingCopy) return null
    let content: Record<string, unknown>
    try {
      content = buildWorkingCopyContent()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Working Copy 内容无效')
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
      message.error(result.error || '保存 Working Copy 失败')
      return null
    }
    setWorkingCopy(result.data)
    await refreshWorkspace()
    message.success('Working Copy 已保存')
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

  const selectedSummary = history.items.find(
    (item) => item.id === selectedRevisionId,
  )
  const mainPart = revisionDetail?.parts.find(
    (part) => part.type === 'document' && part.key === 'main',
  )
  const extraParts = revisionDetail?.parts.filter((part) => part !== mainPart) ?? []
  const copyParts = workingCopy && Array.isArray(workingCopy.content.parts)
    ? workingCopy.content.parts.filter((item): item is Record<string, unknown> => (
        Boolean(item) && typeof item === 'object' && !Array.isArray(item)
      ))
    : []

  return (
    <>
      <PurrModal
        title="版本历史"
        open={open}
        width="min(1120px, calc(100vw - 40px))"
        footer={null}
        destroyOnHidden
        onCancel={onClose}
        className="screenplay-revision-library-modal"
      >
        <div className="screenplay-revision-library">
          <nav className="screenplay-revision-library__roles" aria-label="交付物版本分类">
            {workspace.deliverables.map((deliverable) => (
              <button
                type="button"
                className={deliverable.role === role ? 'is-active' : ''}
                aria-pressed={deliverable.role === role}
                onClick={() => selectRole(deliverable.role)}
                key={deliverable.role}
              >
                <span>{ROLE_LABELS[deliverable.role]}</span>
                {workspace.workflow.heads[deliverable.role] && <small>有当前版本</small>}
              </button>
            ))}
          </nav>
          <section className="screenplay-revision-library__history">
            <header>
              <div>
                <HistoryIcon />
                <strong>{ROLE_LABELS[role]}</strong>
              </div>
              <PurrButton
                type="text"
                size="small"
                icon={<RefreshIcon />}
                loading={historyLoading}
                onClick={() => void loadHistory(role)}
              >
                刷新
              </PurrButton>
            </header>
            {historyLoading && history.items.length === 0 ? (
              <div className="screenplay-revision-library__loading"><LoadingIcon />读取版本…</div>
            ) : history.items.length === 0 ? (
              <div className="screenplay-revision-library__empty">这个交付物还没有发布版本</div>
            ) : (
              <div className="screenplay-revision-list">
                {history.items.map((revision) => (
                  <button
                    type="button"
                    className={revision.id === selectedRevisionId ? 'is-selected' : ''}
                    onClick={() => setSelectedRevisionId(revision.id)}
                    key={revision.id}
                  >
                    <span className={`is-${revision.status || 'candidate'}`}>
                      {STATUS_LABELS[revision.status || 'candidate']}
                    </span>
                    <strong>v{revision.revisionNo}</strong>
                    <small>{formatTime(revision.createdAt)}</small>
                    {revision.applicability === 'stale' && <em>上游已变化</em>}
                  </button>
                ))}
                {history.nextCursor && (
                  <PurrButton
                    type="text"
                    size="small"
                    loading={historyLoadingMore}
                    onClick={() => void loadHistory(role, history.nextCursor || undefined)}
                  >
                    加载更早版本
                  </PurrButton>
                )}
              </div>
            )}
          </section>
          <section className="screenplay-revision-library__detail">
            {detailLoading ? (
              <div className="screenplay-revision-library__loading"><LoadingIcon />读取内容…</div>
            ) : !revisionDetail || !selectedSummary ? (
              <div className="screenplay-revision-library__empty">
                <FileTextIcon />选择一个版本查看完整内容
              </div>
            ) : (
              <>
                <header>
                  <div>
                    <span>{STATUS_LABELS[selectedSummary.status || 'candidate']}</span>
                    <h3>{ROLE_LABELS[revisionDetail.role]} · v{revisionDetail.revisionNo}</h3>
                    <small>
                      {createdByLabel(revisionDetail.createdBy)} · {formatTime(revisionDetail.createdAt)}
                    </small>
                  </div>
                  {!readOnly && (
                    <div>
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
                  )}
                </header>
                {selectedSummary.applicability === 'stale' && (
                  <div className="screenplay-revision-library__stale">
                    该版本绑定的上游已经变化，不能直接应用；可以基于它建立 Working Copy，发布时会绑定当前上游。
                  </div>
                )}
                <div className="screenplay-revision-library__meta">
                  <span>{revisionDetail.parts.length} 个 Part</span>
                  <span>{revisionDetail.sources.length} 条来源</span>
                  <span>{Object.keys(revisionDetail.inputRevisions).length} 个上游输入</span>
                </div>
                <article className="screenplay-revision-library__content">
                  <pre>{mainPart?.contentText || JSON.stringify(mainPart?.payload || {}, null, 2)}</pre>
                </article>
                {extraParts.length > 0 && (
                  <div className="screenplay-revision-library__parts">
                    {extraParts.map((part, index) => (
                      <details key={`${part.type}-${part.key}`}>
                        <summary>{partLabel(part, index + 1)}</summary>
                        <pre>{part.contentText || JSON.stringify(part.payload, null, 2)}</pre>
                      </details>
                    ))}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </PurrModal>

      <PurrModal
        title={workingCopy ? `${ROLE_LABELS[workingCopy.role]} Working Copy` : 'Working Copy'}
        open={workingCopy != null}
        width="min(920px, calc(100vw - 40px))"
        destroyOnHidden
        onCancel={() => {
          if (savingWorkingCopy || publishingWorkingCopy) return
          setWorkingCopy(null)
        }}
        footer={workingCopy ? (
          <>
            <PurrButton
              onClick={() => setWorkingCopy(null)}
              disabled={savingWorkingCopy || publishingWorkingCopy}
            >
              关闭
            </PurrButton>
            <PurrButton
              icon={<SaveIcon />}
              loading={savingWorkingCopy}
              disabled={publishingWorkingCopy}
              onClick={() => void saveWorkingCopy()}
            >
              保存编辑
            </PurrButton>
            <PurrButton
              type="primary"
              icon={<CheckCircleIcon />}
              loading={publishingWorkingCopy}
              disabled={savingWorkingCopy}
              onClick={() => void publishWorkingCopy()}
            >
              发布为候选
            </PurrButton>
          </>
        ) : null}
        className="screenplay-working-copy-modal"
      >
        {workingCopy && (
          <div className="screenplay-working-copy-editor">
            <div className="screenplay-working-copy-editor__notice">
              Working Copy 可反复保存，不会增加正式版本号；只有“发布为候选”才创建新的 Revision。
            </div>
            <label>
              <span>主文档结构化内容</span>
              <PurrInput.TextArea
                value={mainJsonDraft}
                autoSize={{ minRows: 8, maxRows: 18 }}
                spellCheck={false}
                onChange={(event) => setMainJsonDraft(event.target.value)}
              />
            </label>
            <label>
              <span>主文档正文</span>
              <PurrInput.TextArea
                value={mainTextDraft}
                autoSize={{ minRows: 8, maxRows: 22 }}
                onChange={(event) => setMainTextDraft(event.target.value)}
              />
            </label>
            {copyParts.map((part, index) => (
              <details className="screenplay-working-copy-editor__part" key={`${part.type}-${part.key}`}>
                <summary>{partLabel({
                  type: String(part.type || 'document') as ScreenplayV2RevisionPart['type'],
                  key: String(part.key || index + 1),
                  position: Number(part.position || index + 1),
                  payload: {},
                  contentText: '',
                  contentDigest: '',
                }, index + 1)}</summary>
                <label>
                  <span>结构化内容</span>
                  <PurrInput.TextArea
                    value={partJsonDrafts[index] || '{}'}
                    autoSize={{ minRows: 6, maxRows: 16 }}
                    spellCheck={false}
                    onChange={(event) => setPartJsonDrafts((current) => (
                      current.map((value, itemIndex) => (
                        itemIndex === index ? event.target.value : value
                      ))
                    ))}
                  />
                </label>
                <label>
                  <span>正文</span>
                  <PurrInput.TextArea
                    value={partTextDrafts[index] || ''}
                    autoSize={{ minRows: 5, maxRows: 18 }}
                    onChange={(event) => setPartTextDrafts((current) => (
                      current.map((value, itemIndex) => (
                        itemIndex === index ? event.target.value : value
                      ))
                    ))}
                  />
                </label>
              </details>
            ))}
          </div>
        )}
      </PurrModal>
    </>
  )
}

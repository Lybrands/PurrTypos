import { services } from '@/services'
import { requestGlobalChatPrefill } from '../../stores/chatPrefillStore'
import { useSettingsRevision } from '../../stores/settingsInvalidationStore'
import React from 'react'
import { useMaterialRefresh } from './useMaterialRefresh'
import { AiChatIcon, EditIcon, HistoryIcon, ImportIcon, PaperclipIcon, PlusIcon } from '@/purr-components'
import { PurrCollapse, PurrButton, PurrModal, PurrPopconfirm, PurrSpace, PurrTooltip, PurrTypography } from '@/purr-components'
import KnowledgeMarkdownEditor, {
  appendImportedMarkdown,
} from '@/components/KnowledgeMarkdownEditor'
import MarkdownWithSearch from '../search/MarkdownWithSearch'
import { notifyWorkspaceSearchContentChanged, useSearchQuery } from '../../stores/workspaceStore'
import type { EntityId, StoryBackgroundAttachment } from '../../types'
import { useAppFeedback } from '../../hooks/useAppFeedback'
import SettingDiffView, { useActiveSettingDiffSession } from '../settingDiff/SettingDiffView'
import SettingHistoryDrawer from '../SettingPanel/SettingHistoryDrawer'
import './StoryBackgroundTab.scss'

interface StoryBackgroundTabProps {
  bookId: EntityId | null
}

export default function StoryBackgroundTab({ bookId }: StoryBackgroundTabProps) {
  const { message } = useAppFeedback()
  const [loadError, setLoadError] = React.useState('')
  const workspaceSearchQuery = useSearchQuery()
  const [baseRevision, setBaseRevision] = React.useState<string>()
  const [inheritedBaseline, setInheritedBaseline] = React.useState('')
  const [content, setContent] = React.useState('')
  const [attachments, setAttachments] = React.useState<StoryBackgroundAttachment[]>([])
  const [editing, setEditing] = React.useState(false)
  const [loading, setLoading] = React.useState(false)
  const [attachmentModalOpen, setAttachmentModalOpen] = React.useState(false)
  const [historyOpen, setHistoryOpen] = React.useState(false)
  const [draftContent, setDraftContent] = React.useState('')

  const activeDiffSession = useActiveSettingDiffSession('background', bookId)
  const diffLocked = Boolean(activeDiffSession)

  const loadContent = React.useCallback(async () => {
    if (bookId == null) return
    setLoading(true)
    try {
      const [bg, attRes] = await Promise.all([
        services.storyBackground.getStoryBackground({ bookId }),
        services.storyBackground.getStoryBackgroundAttachments({ bookId }),
      ])
      setLoadError(bg.success ? '' : bg.error || '无法读取资料')
      setContent(bg.success ? bg.data?.content ?? '' : '')
      setBaseRevision(bg.data?.baseRevision)
      setInheritedBaseline(bg.success ? bg.data?.inheritedBaseline || '' : '')
      setAttachments(attRes.success && Array.isArray(attRes.data) ? attRes.data : [])
    } finally {
      setLoading(false)
    }
  }, [bookId])

  useMaterialRefresh(loadContent, editing)

  React.useEffect(() => {
    loadContent()
  }, [loadContent])

  // AI 工具改写背景后刷新；正在手动编辑时不动（避免覆盖未保存草稿）
  const editingRef = React.useRef(editing)
  React.useEffect(() => {
    editingRef.current = editing
  }, [editing])
  const backgroundRevision = useSettingsRevision('background')
  React.useEffect(() => {
    if (backgroundRevision === 0) return
    if (!editingRef.current) loadContent()
  }, [backgroundRevision, loadContent])

  React.useEffect(() => {
    notifyWorkspaceSearchContentChanged()
  }, [content, notifyWorkspaceSearchContentChanged])

  const handleAdd = React.useCallback(() => {
    if (diffLocked) return
    setDraftContent(content)
    setEditing(true)
  }, [content, diffLocked])

  const handleSave = React.useCallback(async () => {
    if (bookId == null) return
    const md = draftContent
    setLoading(true)
    try {
      const res = await services.storyBackground.saveStoryBackground({ bookId, content: md, baseRevision })
      if (res.success) {
        setContent(md)
        setEditing(false)
        await loadContent()
        message.success('已保存')
      } else {
        message.error(res.error || '保存失败')
      }
    } finally {
      setLoading(false)
    }
  }, [bookId, draftContent, message, baseRevision, loadContent])

  const handleCancel = React.useCallback(() => {
    setEditing(false)
  }, [])

  const handleImportFile = React.useCallback(async () => {
    const res = await services.files.openAndReadTextFile()
    if (res.success && res.data != null) {
      setDraftContent((current) => appendImportedMarkdown(current, res.data ?? ''))
      message.success('已追加导入内容')
    } else if (res.error !== 'canceled') {
      message.error(res.error || '读取文件失败')
    }
  }, [message])

  const handlePickAttachments = React.useCallback(async () => {
    if (bookId == null) return
    try {
      const res = await services.storyBackground.pickStoryBackgroundAttachments({ bookId })
      if (res.success) {
        const count = (res as { addedCount?: number }).addedCount ?? res.data?.length ?? 0
        if (count > 0) message.success(`已添加 ${count} 个附件`)
        if (Array.isArray(res.data)) setAttachments(res.data)
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导入附件失败')
        const listRes = await services.storyBackground.getStoryBackgroundAttachments({ bookId })
        if (listRes.success && Array.isArray(listRes.data)) setAttachments(listRes.data)
      }
    } catch (_) {
      message.error('导入附件失败')
      const listRes = await services.storyBackground.getStoryBackgroundAttachments({ bookId })
      if (listRes.success && Array.isArray(listRes.data)) setAttachments(listRes.data)
    }
  }, [bookId, message])

  const handleDeleteAttachment = React.useCallback(async (id: number) => {
    const res = await services.storyBackground.deleteStoryBackgroundAttachment({ id })
    if (res.success) {
      setAttachments((prev) => prev.filter((a) => a.id !== id))
      message.success('已删除')
    } else {
      message.error(res.error || '删除失败')
    }
  }, [message])

  const handleOpenAttachment = React.useCallback((storedPath: string) => {
    services.storyBackground.openStoryBackgroundAttachment({ storedPath })
  }, [])

  const handlePickAttachmentsInModal = React.useCallback(async () => {
    await handlePickAttachments()
  }, [handlePickAttachments])

  const attachmentModalContent = (
    <div className="story-background-attachments-modal">
      {attachments.length === 0 ? (
        <div className="story-background-attachments-empty">暂无附件</div>
      ) : (
        <ul className="story-background-attachment-list">
          {attachments.map((a) => (
            <li key={a.id} className="story-background-attachment-item">
              <PurrSpace size="small" style={{ width: '100%' }}>
                <PaperclipIcon />
                <PurrTypography.Link
                  ellipsis
                  onClick={() => handleOpenAttachment(a.stored_path)}
                  style={{ flex: 1, minWidth: 0 }}
                >
                  {a.name}
                </PurrTypography.Link>
                {editing && (
                  <PurrPopconfirm
                    title="确定删除该附件？"
                    onConfirm={() => handleDeleteAttachment(a.id)}
                  >
                    <PurrButton type="text" size="small" danger>
                      删除
                    </PurrButton>
                  </PurrPopconfirm>
                )}
              </PurrSpace>
            </li>
          ))}
        </ul>
      )}
    </div>
  )

  if (bookId == null) {
    return (
      <div className="story-background-tab">
        <div className="story-background-empty">
          <p>请先选择书籍</p>
        </div>
      </div>
    )
  }

  if (loadError && !editing) return <p role="alert">{loadError} <PurrButton onClick={() => void loadContent()}>重新读取</PurrButton></p>

  if (activeDiffSession) {
    return (
      <div className="story-background-tab story-background-diff-wrap">
        <SettingDiffView sessionKey={activeDiffSession.sessionKey} />
        <SettingHistoryDrawer
          kind="background"
          bookId={bookId}
          entityTitle="故事背景"
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onRestored={loadContent}
        />
      </div>
    )
  }

  if (editing) {
    return (
      <div className="story-background-tab story-background-editing">
        <div className="story-background-editing-header">
          <div className="story-background-toolbar story-background-toolbar-top">
            <PurrTooltip title="导入文件">
              <PurrButton
                type="text"
                size="small"
                icon={<ImportIcon />}
                onClick={handleImportFile}
                disabled={loading}
              />
            </PurrTooltip>
            <PurrTooltip title={attachments.length > 0 ? `附件 (${attachments.length})` : '附件'}>
              <PurrButton
                type="text"
                size="small"
                icon={<PaperclipIcon />}
                onClick={() => setAttachmentModalOpen(true)}
              />
            </PurrTooltip>
          </div>
        </div>
        <KnowledgeMarkdownEditor
          documentKey={`story-background:${bookId}`}
          value={draftContent}
          onChange={setDraftContent}
          ariaLabel="故事背景"
          className="story-background-editor-wrap"
        />
        <div className="story-background-toolbar story-background-toolbar-bottom">
          <PurrButton type="primary" size="small" onClick={handleSave} disabled={loading}>
            保存
          </PurrButton>
          <PurrButton size="small" onClick={handleCancel} disabled={loading}>
            取消
          </PurrButton>
        </div>
        <PurrModal
          title="附件"
          open={attachmentModalOpen}
          onCancel={() => setAttachmentModalOpen(false)}
          footer={
            editing
              ? [
                  <PurrButton key="add" icon={<PaperclipIcon />} onClick={handlePickAttachmentsInModal}>
                    导入附件
                  </PurrButton>,
                  <PurrButton key="close" type="primary" onClick={() => setAttachmentModalOpen(false)}>
                    关闭
                  </PurrButton>,
                ]
              : [
                  <PurrButton key="close" type="primary" onClick={() => setAttachmentModalOpen(false)}>
                    关闭
                  </PurrButton>,
                ]
          }
        >
          {attachmentModalContent}
        </PurrModal>
      </div>
    )
  }

  if (!content.trim() && !inheritedBaseline) {
    return (
      <div className="story-background-tab">
        <div
          className="story-background-empty-card outline-empty-card outline-empty-card-action"
          onClick={handleAdd}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          role="button"
          tabIndex={0}
        >
          <PlusIcon className="outline-empty-card-icon" />
          <p className="outline-empty-card-title">添加小说背景</p>
          <p className="outline-empty-card-desc">点击此处填写世界观、时代背景、设定等</p>
        </div>
      </div>
    )
  }

  return (
    <div className="story-background-tab">
      <div className="story-background-view">
        <div className="story-background-view-header">
          <div className="story-background-toolbar story-background-toolbar-top">
            <PurrTooltip title="编辑">
              <PurrButton type="text" size="small" icon={<EditIcon />} onClick={handleAdd} disabled={diffLocked} />
            </PurrTooltip>
            <PurrTooltip title="与 AI 讨论背景设定">
              <PurrButton
                type="text"
                size="small"
                icon={<AiChatIcon />}
                onClick={() => {
                                requestGlobalChatPrefill('关于小说背景设定：')
                }}
              />
            </PurrTooltip>
            <PurrTooltip title="历史">
              <PurrButton type="text" size="small" icon={<HistoryIcon />} onClick={() => setHistoryOpen(true)} />
            </PurrTooltip>
            <PurrTooltip title={attachments.length > 0 ? `附件 (${attachments.length})` : '附件'}>
              <PurrButton
                type="text"
                size="small"
                icon={<PaperclipIcon />}
                onClick={() => setAttachmentModalOpen(true)}
              />
            </PurrTooltip>
          </div>
        </div>
        <div className="story-background-content story-background-markdown">
          {inheritedBaseline && <PurrCollapse size="small" defaultActiveKeys={["baseline"]} items={[{key: "baseline", label: "原作背景 · 只读", children: <MarkdownWithSearch content={inheritedBaseline} searchQuery={workspaceSearchQuery} />}]} />}
          {inheritedBaseline && <h3>本书后续发展</h3>}
          <MarkdownWithSearch content={content || ''} searchQuery={workspaceSearchQuery} />
        </div>
        <PurrModal
          title="附件"
          open={attachmentModalOpen}
          onCancel={() => setAttachmentModalOpen(false)}
          footer={[
            <PurrButton key="close" type="primary" onClick={() => setAttachmentModalOpen(false)}>
              关闭
            </PurrButton>,
          ]}
        >
          {attachmentModalContent}
        </PurrModal>
        <SettingHistoryDrawer
          kind="background"
          bookId={bookId}
          entityTitle="故事背景"
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onRestored={loadContent}
        />
      </div>
    </div>
  )
}

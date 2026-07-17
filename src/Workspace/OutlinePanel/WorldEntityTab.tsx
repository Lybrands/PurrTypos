import React from 'react'
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  CommentOutlined,
  CompassOutlined,
} from '@ant-design/icons'
import { Button, Modal, Input, Select, Tag, Tooltip, Empty, Segmented } from 'antd'
import type { Editor } from '@tiptap/core'
import { Extension } from '@tiptap/core'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import type { EntityId, SettingEntity, SettingEntityType } from '../../types'
import { useAntdApp } from '../../hooks/useAntdApp'
import { markdownToHtml, htmlToMarkdown } from '../../utils/markdown'
import SettingDiffView, { useActiveSettingDiffSession } from '../settingDiff/SettingDiffView'
import { settingSessionKey, useSettingDiff } from '../settingDiff/SettingDiffContext'
import SettingHistoryDrawer from '../SettingPanel/SettingHistoryDrawer'
import './StoryBackgroundTab.scss'
import './CharacterTab.scss'

/** Tab 键：列表内缩进，非列表插入制表符（与人物/背景编辑器一致） */
const LiteralTab = Extension.create({
  name: 'literalTab',
  addKeyboardShortcuts() {
    return {
      Tab: () => {
        if (this.editor.commands.sinkListItem('listItem')) return true
        this.editor.commands.insertContent('\t')
        return true
      },
    }
  },
})

export const ENTITY_TYPE_OPTIONS: Array<{ value: SettingEntityType; label: string }> = [
  { value: 'location', label: '地点' },
  { value: 'faction', label: '势力' },
  { value: 'item', label: '物品' },
  { value: 'other', label: '其他' },
]

const ENTITY_TYPE_LABEL: Record<string, string> = Object.fromEntries(
  ENTITY_TYPE_OPTIONS.map((o) => [o.value, o.label]),
)

/** 新建条目时的档案脚手架：提供引导但不强制 */
const PROFILE_TEMPLATE = `## 概述

## 关键细节

## 与故事的关联
`

function splitToArray(str?: string): string[] {
  if (!str) return []
  return str.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
}

/** 卡片悬停预览：粗暴去掉 Markdown 标记后截断（与人物卡片一致） */
function profilePreview(md?: string, max = 160): string {
  const plain = (md ?? '')
    .replace(/^#+\s*/gm, '')
    .replace(/^\s*[-*+]\s*/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\n{2,}/g, '\n')
    .trim()
  if (!plain) return ''
  return plain.length > max ? `${plain.slice(0, max)}…` : plain
}

interface WorldEntityTabProps {
  bookId: EntityId | null
  focusEntityId?: number | null
  onFocusEntityHandled?: () => void
}

export default function WorldEntityTab({
  bookId,
  focusEntityId = null,
  onFocusEntityHandled,
}: WorldEntityTabProps) {
  const { message } = useAntdApp()
  const [entities, setEntities] = React.useState<SettingEntity[]>([])
  const [typeFilter, setTypeFilter] = React.useState<SettingEntityType | 'all'>('all')
  const [editModalOpen, setEditModalOpen] = React.useState(false)
  const [editTarget, setEditTarget] = React.useState<SettingEntity | null>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<SettingEntity | null>(null)
  const [historyOpen, setHistoryOpen] = React.useState(false)
  const [historyEntity, setHistoryEntity] = React.useState<SettingEntity | null>(null)
  const [saving, setSaving] = React.useState(false)
  const cardRefs = React.useRef<Record<number, HTMLDivElement | null>>({})

  const activeDiffSession = useActiveSettingDiffSession('entity', focusEntityId)
  const diff = useSettingDiff()

  const [draftType, setDraftType] = React.useState<SettingEntityType>('location')
  const [draftName, setDraftName] = React.useState('')
  const [draftTags, setDraftTags] = React.useState<string[]>([])

  const editorRef = React.useRef<Editor | null>(null)
  const editor = useEditor({
    immediatelyRender: true,
    extensions: [
      LiteralTab,
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4] },
      }),
    ],
    content: '<p></p>',
    editorProps: {
      attributes: {
        class: 'story-background-tiptap-editable',
        spellcheck: 'false',
      },
      handlePaste: (_view, event) => {
        const text = event.clipboardData?.getData('text/plain') ?? ''
        if (!text.trim()) return false
        const looksLikeMarkdown =
          /^#+\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+|\n\s*[-*+]\s|\n#+\s|^>\s|^\s*\|.+\|/m.test(text)
        if (looksLikeMarkdown) {
          event.preventDefault()
          editorRef.current?.commands.insertContent(markdownToHtml(text))
          return true
        }
        return false
      },
    },
  }, [editModalOpen])

  React.useEffect(() => {
    editorRef.current = editor ?? null
  }, [editor])

  const loadEntities = React.useCallback(async () => {
    if (bookId == null) return
    const res = await window.electronAPI.getSettingEntities({ bookId })
    if (res.success) setEntities(res.data ?? [])
  }, [bookId])

  React.useEffect(() => {
    loadEntities()
  }, [loadEntities])

  // AI 工具创建/修改条目后刷新列表（面板可能与 AI 对话同屏开着）
  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ kind?: string }>).detail
      if (detail?.kind === 'entity') loadEntities()
    }
    window.addEventListener('setting-updated', handler)
    return () => window.removeEventListener('setting-updated', handler)
  }, [loadEntities])

  // 打开编辑弹窗时灌入草稿（新建给模板脚手架）
  React.useEffect(() => {
    if (!editModalOpen || !editor) return
    const md = editTarget ? (editTarget.profile_md ?? '') : PROFILE_TEMPLATE
    editor.commands.setContent(markdownToHtml(md))
  }, [editModalOpen, editTarget, editor])

  React.useEffect(() => {
    if (focusEntityId == null) return
    const el = cardRefs.current[focusEntityId]
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      onFocusEntityHandled?.()
    }
  }, [focusEntityId, entities, onFocusEntityHandled])

  const openCreate = React.useCallback(() => {
    setEditTarget(null)
    setDraftType(typeFilter === 'all' ? 'location' : typeFilter)
    setDraftName('')
    setDraftTags([])
    setEditModalOpen(true)
  }, [typeFilter])

  const openEdit = React.useCallback((ent: SettingEntity) => {
    if (diff.hasSession(settingSessionKey('entity', ent.id))) return
    setEditTarget(ent)
    setDraftType(ent.entity_type || 'other')
    setDraftName(ent.name ?? '')
    setDraftTags(splitToArray(ent.tags))
    setEditModalOpen(true)
  }, [diff])

  const closeModal = React.useCallback(() => {
    setEditModalOpen(false)
    setEditTarget(null)
  }, [])

  const handleSave = React.useCallback(async () => {
    const name = draftName.trim()
    if (!name) {
      message.error('请输入条目名称')
      return
    }
    if (bookId == null) return
    const ed = editorRef.current
    const profileMd = ed ? htmlToMarkdown(ed.getHTML()) : ''
    setSaving(true)
    try {
      const res = editTarget
        ? await window.electronAPI.updateSettingEntity({
            id: editTarget.id,
            data: { entityType: draftType, name, tags: draftTags.join(', '), profileMd },
          })
        : await window.electronAPI.createSettingEntity({
            bookId,
            entityType: draftType,
            name,
            tags: draftTags.join(', '),
            profileMd,
          })
      if (res.success) {
        message.success(editTarget ? '已保存' : '条目已创建')
        closeModal()
        loadEntities()
      } else {
        message.error(res.error || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }, [bookId, draftType, draftName, draftTags, editTarget, closeModal, loadEntities, message])

  /** 打开 AI 全局对话并携带条目上下文（不依赖章节对话区） */
  const openAiChat = React.useCallback((ent: SettingEntity) => {
    window.dispatchEvent(new CustomEvent('workspace-open-panel', { detail: { panel: 'ai', open: true } }))
    window.dispatchEvent(new CustomEvent('open-setting-chat', {
      detail: { prefill: `关于${ENTITY_TYPE_LABEL[ent.entity_type] || '设定'}「${ent.name}」：` },
    }))
  }, [])

  const handleDelete = React.useCallback(async () => {
    if (!deleteTarget) return
    const res = await window.electronAPI.deleteSettingEntity({ id: deleteTarget.id })
    if (res.success) {
      message.success('已删除')
      setDeleteTarget(null)
      loadEntities()
    } else {
      message.error(res.error || '删除失败')
    }
  }, [deleteTarget, loadEntities, message])

  const visibleEntities = React.useMemo(
    () => (typeFilter === 'all' ? entities : entities.filter((e) => e.entity_type === typeFilter)),
    [entities, typeFilter],
  )

  if (bookId == null) {
    return (
      <div className="character-tab character-tab-empty">
        <Empty description="请先选择书籍" />
      </div>
    )
  }

  return (
    <div className="character-tab world-entity-tab">
      <div className="character-tab-header">
        <Segmented
          size="small"
          value={typeFilter}
          onChange={(v) => setTypeFilter(v as SettingEntityType | 'all')}
          options={[{ value: 'all', label: '全部' }, ...ENTITY_TYPE_OPTIONS]}
        />
        <Tooltip title="新建设定条目">
          <Button
            type="text"
            size="small"
            icon={<PlusOutlined style={{ fontSize: 14 }} />}
            onClick={openCreate}
            className="character-add-btn"
          />
        </Tooltip>
      </div>
      <div className="character-tab-list">
        {visibleEntities.length === 0 ? (
          <div className="character-tab-empty-card">
            <CompassOutlined className="character-tab-empty-icon" />
            <p>暂无设定条目</p>
            <small>地点、势力、物品等世界观设定都可以记录在这里</small>
          </div>
        ) : (
          visibleEntities.map((ent, index) => {
            const preview = profilePreview(ent.profile_md)
            const isFocus = focusEntityId === ent.id
            const hasDiff = diff.hasSession(settingSessionKey('entity', ent.id))
            return (
              <div
                key={ent.id}
                ref={(el) => { cardRefs.current[ent.id] = el }}
                className={`character-card${isFocus || hasDiff ? ' is-diff-focus' : ''}`}
              >
                <div className="character-card-main">
                  <div className="character-card-name-row">
                    <span className="character-card-index">{index + 1}.</span>
                    {preview ? (
                      <Tooltip
                        zIndex={1301}
                        title={<div className="character-info-tooltip">{preview}</div>}
                      >
                        <span className="character-card-name">{ent.name}</span>
                      </Tooltip>
                    ) : (
                      <span className="character-card-name">{ent.name}</span>
                    )}
                    <Tag className="world-entity-type-tag">
                      {ENTITY_TYPE_LABEL[ent.entity_type] || '其他'}
                    </Tag>
                  </div>
                  {ent.tags && (
                    <div className="character-card-tags">
                      {splitToArray(ent.tags).map((t, i) => (
                        <Tag key={i} variant="filled" color="default">{t}</Tag>
                      ))}
                    </div>
                  )}
                </div>
                <div className="character-card-actions">
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(ent)}
                    title={hasDiff ? '审阅 diff 中，暂不可编辑' : '编辑'}
                    disabled={hasDiff}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<CommentOutlined />}
                    onClick={() => openAiChat(ent)}
                    title="与 AI 讨论此设定"
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<HistoryOutlined />}
                    onClick={() => { setHistoryEntity(ent); setHistoryOpen(true) }}
                    title="历史"
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => setDeleteTarget(ent)}
                    title="删除"
                    className="character-add-btn"
                    disabled={hasDiff}
                  />
                </div>
              </div>
            )
          })
        )}
      </div>

      {activeDiffSession ? (
        <div className="character-tab-diff-wrap">
          <SettingDiffView sessionKey={activeDiffSession.sessionKey} />
        </div>
      ) : null}

      <SettingHistoryDrawer
        kind="entity"
        settingEntityId={historyEntity?.id ?? null}
        entityTitle={historyEntity?.name}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onRestored={loadEntities}
      />

      <Modal
        title={editTarget ? '编辑设定条目' : '新建设定条目'}
        open={editModalOpen}
        onOk={handleSave}
        onCancel={closeModal}
        okText={editTarget ? '保存' : '创建'}
        cancelText="取消"
        okButtonProps={{ loading: saving }}
        width={680}
        destroyOnHidden
        className="character-edit-modal"
      >
        <div className="character-edit-meta">
          <Select
            value={draftType}
            onChange={(v) => setDraftType(v as SettingEntityType)}
            options={ENTITY_TYPE_OPTIONS}
            className="world-entity-edit-type"
            popupMatchSelectWidth={false}
          />
          <Input
            placeholder="条目名称（必填）"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            maxLength={50}
            className="character-edit-name"
          />
          <Select
            mode="tags"
            placeholder="标签：输入后回车确认"
            value={draftTags}
            onChange={setDraftTags}
            open={false}
            suffixIcon={null}
            maxCount={10}
            className="character-edit-tags"
          />
        </div>
        <div className="character-edit-profile story-background-tiptap-wrap">
          <EditorContent editor={editor} className="story-background-tiptap-container" />
        </div>
      </Modal>

      <Modal
        title="删除设定条目"
        open={!!deleteTarget}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>确认删除「{deleteTarget?.name}」？此操作不可恢复。</p>
      </Modal>
    </div>
  )
}

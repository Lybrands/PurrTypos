import React from 'react'
import { PlusOutlined, UserOutlined, DeleteOutlined, EditOutlined, SettingOutlined, HistoryOutlined, CommentOutlined } from '@ant-design/icons'
import { Button, Modal, Input, Select, Tag, Tooltip, Empty } from 'antd'
import type { Editor } from '@tiptap/core'
import { Extension } from '@tiptap/core'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import type { Character, CharacterOption, EntityId } from '../../types'
import { useAntdApp } from '../../hooks/useAntdApp'
import { getBookCharacters } from '../utils'
import { markdownToHtml, htmlToMarkdown } from '../../utils/markdown'
import CharacterOptionsModal from './CharacterOptionsModal'
import SettingDiffView, { useActiveSettingDiffSession } from '../settingDiff/SettingDiffView'
import { settingSessionKey, useSettingDiff } from '../settingDiff/SettingDiffContext'
import SettingHistoryDrawer from '../SettingPanel/SettingHistoryDrawer'
import './StoryBackgroundTab.scss'
import './CharacterTab.scss'

/** Tab 键：列表内缩进，非列表插入制表符（与小说背景编辑器一致） */
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

/** 新建人物时的档案脚手架：提供引导但不强制，小节可按需增删 */
const PROFILE_TEMPLATE = `## 基本信息
- 性别：
- 年龄：
- 职业：

## 外貌

## 性格

## 经历

## 在故事中的定位
`

function splitToArray(str?: string): string[] {
  if (!str) return []
  return str.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
}

/** 卡片悬停预览：粗暴去掉 Markdown 标记后截断 */
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

interface CharacterTabProps {
  bookId: EntityId | null
  hideHeader?: boolean
  onActionActiveChange?: (active: boolean) => void
  focusCharacterId?: number | null
  onFocusCharacterHandled?: () => void
}

export default function CharacterTab({
  bookId,
  hideHeader = false,
  onActionActiveChange,
  focusCharacterId = null,
  onFocusCharacterHandled,
}: CharacterTabProps) {
  const { message } = useAntdApp()
  const [characters, setCharacters] = React.useState<Character[]>([])
  const [editModalOpen, setEditModalOpen] = React.useState(false)
  const [editTarget, setEditTarget] = React.useState<Character | null>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<Character | null>(null)
  const [configOpen, setConfigOpen] = React.useState(false)
  const [historyOpen, setHistoryOpen] = React.useState(false)
  const [historyCharacter, setHistoryCharacter] = React.useState<Character | null>(null)
  const [saving, setSaving] = React.useState(false)
  const cardRefs = React.useRef<Record<number, HTMLDivElement | null>>({})

  const activeDiffSession = useActiveSettingDiffSession('character', focusCharacterId)
  const diff = useSettingDiff()

  const [draftName, setDraftName] = React.useState('')
  const [draftTags, setDraftTags] = React.useState<string[]>([])
  const [tagOptions, setTagOptions] = React.useState<CharacterOption[]>([])

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

  const loadCharacters = React.useCallback(async () => {
    if (bookId == null) return
    const list = await getBookCharacters(bookId)
    setCharacters(list)
  }, [bookId])

  const loadOptions = React.useCallback(async () => {
    const res = await window.electronAPI.getCharacterOptions({ category: 'tag' })
    if (res.success && res.data) setTagOptions(res.data)
  }, [])

  React.useEffect(() => {
    loadCharacters()
    loadOptions()
  }, [loadCharacters, loadOptions])

  // AI 工具创建/修改人物后刷新列表（面板可能与 AI 对话同屏开着）
  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ kind?: string }>).detail
      if (detail?.kind === 'character') loadCharacters()
    }
    window.addEventListener('setting-updated', handler)
    return () => window.removeEventListener('setting-updated', handler)
  }, [loadCharacters])

  // 打开编辑弹窗时灌入草稿（新建给模板脚手架）
  React.useEffect(() => {
    if (!editModalOpen || !editor) return
    const md = editTarget ? (editTarget.profile_md ?? '') : PROFILE_TEMPLATE
    editor.commands.setContent(markdownToHtml(md))
  }, [editModalOpen, editTarget, editor])

  React.useEffect(() => {
    onActionActiveChange?.(editModalOpen || !!deleteTarget || configOpen)
  }, [editModalOpen, deleteTarget, configOpen, onActionActiveChange])

  React.useEffect(() => {
    if (focusCharacterId == null) return
    const el = cardRefs.current[focusCharacterId]
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      onFocusCharacterHandled?.()
    }
  }, [focusCharacterId, characters, onFocusCharacterHandled])

  const openCreate = React.useCallback(() => {
    setEditTarget(null)
    setDraftName('')
    setDraftTags([])
    setEditModalOpen(true)
  }, [])

  const openEdit = React.useCallback((c: Character) => {
    if (diff.hasSession(settingSessionKey('character', c.id))) return
    setEditTarget(c)
    setDraftName(c.name ?? '')
    setDraftTags(splitToArray(c.tags))
    setEditModalOpen(true)
  }, [diff])

  const closeModal = React.useCallback(() => {
    setEditModalOpen(false)
    setEditTarget(null)
  }, [])

  const handleSave = React.useCallback(async () => {
    const name = draftName.trim()
    if (!name) {
      message.error('请输入人物名称')
      return
    }
    if (bookId == null) return
    const ed = editorRef.current
    const data: Partial<Character> = {
      name,
      tags: draftTags.join(', '),
      profile_md: ed ? htmlToMarkdown(ed.getHTML()) : '',
    }
    setSaving(true)
    try {
      const res = editTarget
        ? await window.electronAPI.updateCharacter({ id: editTarget.id, data })
        : await window.electronAPI.createCharacter({ bookId, data })
      if (res.success) {
        message.success(editTarget ? '已保存' : '人物已创建')
        closeModal()
        loadCharacters()
      } else {
        message.error(res.error || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }, [bookId, draftName, draftTags, editTarget, closeModal, loadCharacters, message])

  /** 打开 AI 全局对话并携带人物上下文（不依赖章节对话区） */
  const openAiChat = React.useCallback((c: Character) => {
    window.dispatchEvent(new CustomEvent('workspace-open-panel', { detail: { panel: 'ai', open: true } }))
    window.dispatchEvent(new CustomEvent('open-setting-chat', {
      detail: { prefill: `关于人物「${c.name}」：` },
    }))
  }, [])

  const handleDelete = React.useCallback(async () => {
    if (!deleteTarget) return
    const res = await window.electronAPI.deleteCharacter({ id: deleteTarget.id })
    if (res.success) {
      message.success('已删除')
      setDeleteTarget(null)
      loadCharacters()
    } else {
      message.error(res.error || '删除失败')
    }
  }, [deleteTarget, loadCharacters, message])

  if (bookId == null) {
    return (
      <div className="character-tab character-tab-empty">
        <Empty description="请先选择书籍" />
      </div>
    )
  }

  return (
    <div className="character-tab">
      <div className="character-tab-header">
        {!hideHeader && <span className="character-tab-title">人物列表</span>}
        <div style={{ display: 'flex', gap: 2, marginLeft: hideHeader ? 'auto' : undefined }}>
          <Tooltip title="配置标签选项">
            <Button
              type="text"
              size="small"
              icon={<SettingOutlined style={{ fontSize: 14 }} />}
              onClick={() => setConfigOpen(true)}
              className="character-add-btn"
            />
          </Tooltip>
          <Tooltip title="新建人物">
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined style={{ fontSize: 14 }} />}
              onClick={openCreate}
              className="character-add-btn"
            />
          </Tooltip>
        </div>
      </div>
      <div className="character-tab-list">
        {characters.length === 0 ? (
          <div className="character-tab-empty-card">
            <UserOutlined className="character-tab-empty-icon" />
            <p>暂无人物</p>
            <small>点击「新建人物」添加角色</small>
          </div>
        ) : (
          characters.map((c, index) => {
            const preview = profilePreview(c.profile_md)
            const isFocus = focusCharacterId === c.id
            const hasDiff = diff.hasSession(settingSessionKey('character', c.id))
            return (
              <div
                key={c.id}
                ref={(el) => { cardRefs.current[c.id] = el }}
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
                        <span className="character-card-name">{c.name}</span>
                      </Tooltip>
                    ) : (
                      <span className="character-card-name">{c.name}</span>
                    )}
                  </div>
                  {c.tags && (
                    <div className="character-card-tags">
                      {splitToArray(c.tags).map((t, i) => (
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
                    onClick={() => openEdit(c)}
                    title={hasDiff ? '审阅 diff 中，暂不可编辑' : '编辑'}
                    disabled={hasDiff}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<CommentOutlined />}
                    onClick={() => openAiChat(c)}
                    title="与 AI 讨论此人物"
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<HistoryOutlined />}
                    onClick={() => { setHistoryCharacter(c); setHistoryOpen(true) }}
                    title="历史"
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => setDeleteTarget(c)}
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
        kind="character"
        characterId={historyCharacter?.id ?? null}
        entityTitle={historyCharacter?.name}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onRestored={loadCharacters}
      />

      <Modal
        title={editTarget ? '编辑人物' : '新建人物'}
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
          <Input
            placeholder="人物姓名（必填）"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            maxLength={50}
            className="character-edit-name"
          />
          <Select
            mode="tags"
            placeholder="标签：选择或输入，回车确认"
            value={draftTags}
            onChange={setDraftTags}
            options={tagOptions.map((t) => ({ label: t.value, value: t.value }))}
            maxCount={10}
            className="character-edit-tags"
          />
        </div>
        <div className="character-edit-profile story-background-tiptap-wrap">
          <EditorContent editor={editor} className="story-background-tiptap-container" />
        </div>
      </Modal>

      <CharacterOptionsModal
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        onOptionsChange={loadOptions}
      />

      <Modal
        title="删除人物"
        open={!!deleteTarget}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>确认删除人物「{deleteTarget?.name}」？此操作不可恢复。</p>
      </Modal>
    </div>
  )
}

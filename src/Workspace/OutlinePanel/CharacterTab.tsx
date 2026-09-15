import { services } from '@/services'
import React from 'react'
import { useMaterialRefresh } from './useMaterialRefresh'
import { AiChatIcon, PlusIcon, UserIcon, DeleteIcon, EditIcon, SettingsIcon, HistoryIcon } from '@/purr-components'
import { PurrButton, PurrEmpty, PurrModal, PurrTag, PurrTooltip } from '@/purr-components'
import type { Character, CharacterOption, EntityId } from '../../types'
import { useAppFeedback } from '../../hooks/useAppFeedback'
import CharacterOptionsModal from './CharacterOptionsModal'
import MaterialProfileEditorModal from './MaterialProfileEditorModal'
import SettingDiffView, { useActiveSettingDiffSession } from '../settingDiff/SettingDiffView'
import { settingSessionKey, useSettingDiff } from '../settingDiff/SettingDiffContext'
import SettingHistoryDrawer from '../SettingPanel/SettingHistoryDrawer'
import './StoryBackgroundTab.scss'
import './CharacterTab.scss'

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
  const { message } = useAppFeedback()
  const [loadError, setLoadError] = React.useState('')
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
  const [draftProfileMd, setDraftProfileMd] = React.useState('')
  const [tagOptions, setTagOptions] = React.useState<CharacterOption[]>([])

  const loadCharacters = React.useCallback(async () => {
    if (bookId == null) return
    const res = await services.characters.getCharacters({ bookId })
    setLoadError(res.success ? '' : res.error || '无法读取资料')
    setCharacters(res.success ? res.data || [] : [])
  }, [bookId])

  const loadOptions = React.useCallback(async () => {
    const res = await services.characters.getCharacterOptions({ category: 'tag' })
    if (res.success && res.data) setTagOptions(res.data)
  }, [])

  useMaterialRefresh(loadCharacters)

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
    setDraftProfileMd(PROFILE_TEMPLATE)
    setEditModalOpen(true)
  }, [])

  const openEdit = React.useCallback((c: Character) => {
    if (diff.hasSession(settingSessionKey('character', c.id))) return
    setEditTarget(c)
    setDraftName(c.name ?? '')
    setDraftTags(splitToArray(c.tags))
    setDraftProfileMd(c.profile_md ?? '')
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
    const data: Partial<Character> = {
      name,
      tags: draftTags.join(', '),
      profile_md: draftProfileMd,
      baseRevision: editTarget?.baseRevision,
    }
    setSaving(true)
    try {
      const res = editTarget
        ? await services.characters.updateCharacter({ id: editTarget.id, data })
        : await services.characters.createCharacter({ bookId, data })
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
  }, [bookId, draftName, draftTags, draftProfileMd, editTarget, closeModal, loadCharacters, message])

  /** 打开 AI 全局对话并携带人物上下文（不依赖章节对话区） */
  const openAiChat = React.useCallback((c: Character) => {
    window.dispatchEvent(new CustomEvent('workspace-open-panel', { detail: { panel: 'ai', open: true } }))
    window.dispatchEvent(new CustomEvent('open-setting-chat', {
      detail: { prefill: `关于人物「${c.name}」：` },
    }))
  }, [])

  const handleDelete = React.useCallback(async () => {
    if (!deleteTarget) return
    const res = await services.characters.deleteCharacter({ id: deleteTarget.id, baseRevision: deleteTarget.baseRevision })
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
        <PurrEmpty description="请先选择书籍" />
      </div>
    )
  }

  if (loadError && !editModalOpen) return <p role="alert">{loadError} <PurrButton onClick={() => void loadCharacters()}>重新读取</PurrButton></p>

  return (
    <div className="character-tab">
      <div className="character-tab-header">
        {!hideHeader && <span className="character-tab-title">人物列表</span>}
        <div style={{ display: 'flex', gap: 2, marginLeft: hideHeader ? 'auto' : undefined }}>
          <PurrTooltip title="配置标签选项">
            <PurrButton
              type="text"
              size="small"
              icon={<SettingsIcon />}
              onClick={() => setConfigOpen(true)}
              className="character-add-btn"
            />
          </PurrTooltip>
          <PurrTooltip title="新建人物">
            <PurrButton
              type="text"
              size="small"
              icon={<PlusIcon style={{ fontSize: 14 }} />}
              onClick={openCreate}
              className="character-add-btn"
            />
          </PurrTooltip>
        </div>
      </div>
      <div className="character-tab-list">
        {characters.length === 0 ? (
          <div className="character-tab-empty-card">
            <UserIcon className="character-tab-empty-icon" />
            <p>暂无人物</p>
            <small>点击「新建人物」添加角色</small>
          </div>
        ) : (
          characters.map((c, index) => {
            const preview = profilePreview([c.inheritedBaseline, c.profile_md].filter(Boolean).join("\n"))
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
                      <PurrTooltip
                        title={<div className="character-info-tooltip">{preview}</div>}
                      >
                        <span className="character-card-name">{c.name}</span>
                      </PurrTooltip>
                    ) : (
                      <span className="character-card-name">{c.name}</span>
                    )}
                  </div>
                  {c.tags && (
                    <div className="character-card-tags">
                      {splitToArray(c.tags).map((t, i) => (
                        <PurrTag key={i} variant="filled" color="default">{t}</PurrTag>
                      ))}
                    </div>
                  )}
                </div>

                <div className="character-card-actions">
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<EditIcon />}
                    onClick={() => openEdit(c)}
                    title={hasDiff ? '审阅 diff 中，暂不可编辑' : '编辑'}
                    disabled={hasDiff}
                  />
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<AiChatIcon />}
                    onClick={() => openAiChat(c)}
                    title="与 AI 讨论此人物"
                  />
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<HistoryIcon />}
                    onClick={() => { setHistoryCharacter(c); setHistoryOpen(true) }}
                    title="历史"
                  />
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<DeleteIcon />}
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

      <MaterialProfileEditorModal kind="character" open={editModalOpen} creating={!editTarget} name={draftName} tags={draftTags} profileMd={draftProfileMd} documentKey={`character:${editTarget?.id ?? 'new'}`} onNameChange={setDraftName} onTagsChange={setDraftTags} onProfileChange={setDraftProfileMd} onOk={handleSave} onCancel={closeModal} tagOptions={tagOptions.map((item) => ({ label: item.value, value: item.value }))} inheritedBaseline={editTarget?.inheritedBaseline} saving={saving} />

      <CharacterOptionsModal
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        onOptionsChange={loadOptions}
      />

      <PurrModal
        title="删除人物"
        open={!!deleteTarget}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>确认删除人物「{deleteTarget?.name}」？此操作不可恢复。</p>
      </PurrModal>
    </div>
  )
}

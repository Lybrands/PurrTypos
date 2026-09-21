import { services } from '@/services'
import { requestGlobalChatPrefill } from '../../stores/chatPrefillStore'
import { useSettingsRevision } from '../../stores/settingsInvalidationStore'
import React from 'react'
import { useMaterialRefresh } from './useMaterialRefresh'
import {
  PlusIcon,
  DeleteIcon,
  EditIcon,
  HistoryIcon,
  AiChatIcon,
  CompassIcon,
} from '@/purr-components'
import { PurrButton, PurrEmpty, PurrModal, PurrSegmented, PurrTag, PurrTooltip } from '@/purr-components'
import type { EntityId, SettingEntity, SettingEntityType } from '../../types'
import { useAppFeedback } from '../../hooks/useAppFeedback'
import SettingDiffView, { useActiveSettingDiffSession } from '../settingDiff/SettingDiffView'
import { settingSessionKey, useSettingDiff } from '../settingDiff/SettingDiffContext'
import SettingHistoryDrawer from '../SettingPanel/SettingHistoryDrawer'
import MaterialProfileEditorModal, { MATERIAL_ENTITY_TYPE_OPTIONS } from './MaterialProfileEditorModal'
import './StoryBackgroundTab.scss'
import './CharacterTab.scss'

export const ENTITY_TYPE_OPTIONS = MATERIAL_ENTITY_TYPE_OPTIONS

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
  const { message } = useAppFeedback()
  const [loadError, setLoadError] = React.useState('')
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
  const [draftProfileMd, setDraftProfileMd] = React.useState('')

  const loadEntities = React.useCallback(async () => {
    if (bookId == null) return
    const res = await services.settingEntities.getSettingEntities({ bookId })
    setLoadError(res.success ? '' : res.error || '无法读取资料')
    setEntities(res.success ? res.data || [] : [])
  }, [bookId])

  useMaterialRefresh(loadEntities)

  React.useEffect(() => {
    loadEntities()
  }, [loadEntities])

  // AI 工具创建/修改条目后刷新列表（修订号驱动，仅 entity 变化时触发）
  const entityRevision = useSettingsRevision('entity')
  React.useEffect(() => {
    if (entityRevision === 0) return
    loadEntities()
  }, [entityRevision, loadEntities])

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
    setDraftProfileMd(PROFILE_TEMPLATE)
    setEditModalOpen(true)
  }, [typeFilter])

  const openEdit = React.useCallback((ent: SettingEntity) => {
    if (diff.hasSession(settingSessionKey('entity', ent.id))) return
    setEditTarget(ent)
    setDraftType(ent.entity_type || 'other')
    setDraftName(ent.name ?? '')
    setDraftTags(splitToArray(ent.tags))
    setDraftProfileMd(ent.profile_md ?? '')
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
    setSaving(true)
    try {
      const res = editTarget
        ? await services.settingEntities.updateSettingEntity({
            id: editTarget.id,
            data: { baseRevision: editTarget.baseRevision, entityType: draftType, name, tags: draftTags.join(', '), profileMd: draftProfileMd },
          })
        : await services.settingEntities.createSettingEntity({
            bookId,
            entityType: draftType,
            name,
            tags: draftTags.join(', '),
            profileMd: draftProfileMd,
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
  }, [bookId, draftType, draftName, draftTags, draftProfileMd, editTarget, closeModal, loadEntities, message])

  /** 打开 AI 全局对话并携带条目上下文（不依赖章节对话区） */
  const openAiChat = React.useCallback((ent: SettingEntity) => {
    requestGlobalChatPrefill(`关于${ENTITY_TYPE_LABEL[ent.entity_type] || '设定'}「${ent.name}」：`)
  }, [])

  const handleDelete = React.useCallback(async () => {
    if (!deleteTarget) return
    const res = await services.settingEntities.deleteSettingEntity({ id: deleteTarget.id, baseRevision: deleteTarget.baseRevision })
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
        <PurrEmpty description="请先选择书籍" />
      </div>
    )
  }

  if (loadError && !editModalOpen) return <p role="alert">{loadError} <PurrButton onClick={() => void loadEntities()}>重新读取</PurrButton></p>

  return (
    <div className="character-tab world-entity-tab">
      <div className="character-tab-header">
        <PurrSegmented
          size="small"
          value={typeFilter}
          onChange={(v) => setTypeFilter(v as SettingEntityType | 'all')}
          options={[{ value: 'all', label: '全部' }, ...ENTITY_TYPE_OPTIONS]}
        />
        <PurrTooltip title="新建设定条目">
          <PurrButton
            type="text"
            size="small"
            icon={<PlusIcon style={{ fontSize: 14 }} />}
            onClick={openCreate}
            className="character-add-btn"
          />
        </PurrTooltip>
      </div>
      <div className="character-tab-list">
        {visibleEntities.length === 0 ? (
          <div className="character-tab-empty-card">
            <CompassIcon className="character-tab-empty-icon" />
            <p>暂无设定条目</p>
            <small>地点、势力、物品等世界观设定都可以记录在这里</small>
          </div>
        ) : (
          visibleEntities.map((ent, index) => {
            const preview = profilePreview([ent.inheritedBaseline, ent.profile_md].filter(Boolean).join("\n"))
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
                      <PurrTooltip
                        title={<div className="character-info-tooltip">{preview}</div>}
                      >
                        <span className="character-card-name">{ent.name}</span>
                      </PurrTooltip>
                    ) : (
                      <span className="character-card-name">{ent.name}</span>
                    )}
                    <PurrTag className="world-entity-type-tag">
                      {ENTITY_TYPE_LABEL[ent.entity_type] || '其他'}
                    </PurrTag>
                  </div>
                  {ent.tags && (
                    <div className="character-card-tags">
                      {splitToArray(ent.tags).map((t, i) => (
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
                    onClick={() => openEdit(ent)}
                    title={hasDiff ? '审阅 diff 中，暂不可编辑' : '编辑'}
                    disabled={hasDiff}
                  />
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<AiChatIcon />}
                    onClick={() => openAiChat(ent)}
                    title="与 AI 讨论此设定"
                  />
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<HistoryIcon />}
                    onClick={() => { setHistoryEntity(ent); setHistoryOpen(true) }}
                    title="历史"
                  />
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<DeleteIcon />}
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

      <MaterialProfileEditorModal kind="entity" open={editModalOpen} creating={!editTarget} name={draftName} tags={draftTags} profileMd={draftProfileMd} documentKey={`world-entity:${editTarget?.id ?? 'new'}`} onNameChange={setDraftName} onTagsChange={setDraftTags} onProfileChange={setDraftProfileMd} onOk={handleSave} onCancel={closeModal} entityType={draftType} onEntityTypeChange={setDraftType} inheritedBaseline={editTarget?.inheritedBaseline} saving={saving} />

      <PurrModal
        title="删除设定条目"
        open={!!deleteTarget}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>确认删除「{deleteTarget?.name}」？此操作不可恢复。</p>
      </PurrModal>
    </div>
  )
}

import React from 'react'
import { Button, Checkbox, Empty, Input, message, Modal, Popconfirm, Select, Spin, Tabs } from 'antd'
import { CheckOutlined, CloseOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import type { AiForeshadowing, AiSparkIdea, Character, EntityId, SparkIdeaLayer } from '../../../../types'
import {
  FORESHADOWING_TYPES,
  SparkIdeaLayerFour,
  SPARK_IDEA_LAYER_FOUR_VALUES,
  SPARK_IDEA_LAYER_LABELS,
} from '../../types'
import './index.scss'

export interface MemoryModalProps {
  open: boolean
  onCancel: () => void
  bookId: EntityId | null
  writingChapters?: { id: EntityId; title: string }[]
  selectedIds: (number | string)[]
  selectedForeshadowingIds?: (number | string)[]
  onSelectConfirm: (memoryIds: (number | string)[], foreshadowingIds: (number | string)[]) => void
}

export default function MemoryModal({
  open,
  onCancel,
  bookId,
  writingChapters = [],
  selectedIds,
  selectedForeshadowingIds = [],
  onSelectConfirm,
}: MemoryModalProps) {
  const [activeTab, setActiveTab] = React.useState<string>('select')
  const [sparkIdeas, setSparkIdeas] = React.useState<AiSparkIdea[]>([])
  const [foreshadowing, setForeshadowing] = React.useState<AiForeshadowing[]>([])
  const [loading, setLoading] = React.useState(false)
  const [checkedIds, setCheckedIds] = React.useState<(number | string)[]>(selectedIds)
  const [checkedForeshadowingIds, setCheckedForeshadowingIds] = React.useState<(number | string)[]>(
    selectedForeshadowingIds
  )

  // 管理：四层记忆新增表单
  const [addLayer, setAddLayer] = React.useState<SparkIdeaLayerFour>(SparkIdeaLayerFour.Global)
  const [addContent, setAddContent] = React.useState('')
  /** 关联实体：大纲/章节 用 chapterId，人物 用 characterId（同一控件按层级切换语义） */
  const [addChapterId, setAddChapterId] = React.useState<EntityId | null>(null)
  const [addCharacterId, setAddCharacterId] = React.useState<number | null>(null)
  const [adding, setAdding] = React.useState(false)

  // 本书人物列表（用于"人物设定"关联选择）
  const [characters, setCharacters] = React.useState<Character[]>([])

  // 管理：伏笔记忆新增表单
  const [foreshadowChapterId, setForeshadowChapterId] = React.useState<EntityId | null>(null)
  const [foreshadowContent, setForeshadowContent] = React.useState('')
  const [foreshadowType, setForeshadowType] = React.useState<string>('悬念')
  const [addingForeshadow, setAddingForeshadow] = React.useState(false)

  React.useEffect(() => {
    if (!open || bookId == null) return
    setLoading(true)
    setCheckedIds(selectedIds)
    setCheckedForeshadowingIds(selectedForeshadowingIds)
    Promise.all([
      window.electronAPI.getSparkIdeasByBook({ bookId }),
      window.electronAPI.getForeshadowingByBook({ bookId }),
      window.electronAPI.getCharacters({ bookId }),
    ]).then(([memRes, forRes, charRes]) => {
      setLoading(false)
      if (memRes.success && Array.isArray(memRes.data)) setSparkIdeas(memRes.data)
      else setSparkIdeas([])
      if (forRes.success && Array.isArray(forRes.data)) setForeshadowing(forRes.data)
      else setForeshadowing([])
      if (charRes.success && Array.isArray(charRes.data)) setCharacters(charRes.data)
      else setCharacters([])
    })
  }, [open, bookId, selectedIds, selectedForeshadowingIds])

  const handleSelectOk = React.useCallback(() => {
    onSelectConfirm(checkedIds, checkedForeshadowingIds)
    onCancel()
  }, [checkedIds, checkedForeshadowingIds, onSelectConfirm, onCancel])

  const handleToggle = React.useCallback((id: number | string, checked: boolean) => {
    setCheckedIds((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)))
  }, [])

  const handleToggleForeshadowing = React.useCallback((id: number | string, checked: boolean) => {
    setCheckedForeshadowingIds((prev) =>
      checked ? [...prev, id] : prev.filter((x) => x !== id)
    )
  }, [])

  const handleAdd = React.useCallback(async () => {
    if (bookId == null || !addContent.trim()) return
    // 大纲 / 章节 必须选章节，人物 必须选人物，全局可不关联
    const layerLabel = SPARK_IDEA_LAYER_LABELS[addLayer] as SparkIdeaLayer
    const needsChapter = addLayer === SparkIdeaLayerFour.Outline || addLayer === SparkIdeaLayerFour.Chapter
    const needsCharacter = addLayer === SparkIdeaLayerFour.Character
    if (needsChapter && addChapterId == null) {
      message.warning(`请先选择关联${addLayer === SparkIdeaLayerFour.Outline ? '大纲' : '章节'}`)
      return
    }
    if (needsCharacter && addCharacterId == null) {
      message.warning('请先选择关联人物')
      return
    }
    setAdding(true)
    const res = await window.electronAPI.addSparkIdea({
      bookId,
      layer: layerLabel,
      content: addContent.trim(),
      chapterId: needsChapter ? addChapterId ?? undefined : undefined,
      characterId: needsCharacter ? addCharacterId ?? undefined : undefined,
    })
    setAdding(false)
    if (res.success && res.data) {
      setSparkIdeas((prev) => [res.data as AiSparkIdea, ...prev])
      setAddContent('')
    } else if (!res.success) {
      message.error(res.error || '添加失败')
    }
  }, [bookId, addLayer, addContent, addChapterId, addCharacterId])

  // 切换层级时清掉与新层级无关的关联，避免污染下一次提交
  const handleAddLayerChange = React.useCallback((next: SparkIdeaLayerFour) => {
    setAddLayer(next)
    setAddChapterId(null)
    setAddCharacterId(null)
  }, [])

  const handleDelete = React.useCallback(async (id: number | string) => {
    await window.electronAPI.deleteSparkIdea({ id })
    setSparkIdeas((prev) => prev.filter((m) => m.id !== id))
    setCheckedIds((prev) => prev.filter((x) => x !== id))
  }, [])

  // 编辑「四层设定」：内联编辑（content + layer + 关联实体），保存后该条目可能换层归位。
  const [editingId, setEditingId] = React.useState<number | string | null>(null)
  const [editingContent, setEditingContent] = React.useState('')
  const [editingLayer, setEditingLayer] = React.useState<SparkIdeaLayerFour>(SparkIdeaLayerFour.Global)
  const [editingChapterId, setEditingChapterId] = React.useState<EntityId | null>(null)
  const [editingCharacterId, setEditingCharacterId] = React.useState<number | null>(null)
  const [editSaving, setEditSaving] = React.useState(false)

  const handleEditStart = React.useCallback((m: AiSparkIdea) => {
    // 当前 layer 是中文 label（"全局"/"大纲"/"人物"/"章节"），反查回 SparkIdeaLayerFour 数值
    const currentLayerLabel = String(m.layer)
    const matchedValue =
      SPARK_IDEA_LAYER_FOUR_VALUES.find(
        (v) => SPARK_IDEA_LAYER_LABELS[v] === currentLayerLabel
      ) ?? SparkIdeaLayerFour.Global
    setEditingId(m.id)
    setEditingContent(m.content || '')
    setEditingLayer(matchedValue)
    setEditingChapterId(m.chapter_id ?? null)
    setEditingCharacterId(m.character_id ?? null)
  }, [])

  // 切换层级时清掉与新层级无关的关联实体
  const handleEditLayerChange = React.useCallback((next: SparkIdeaLayerFour) => {
    setEditingLayer(next)
    setEditingChapterId(null)
    setEditingCharacterId(null)
  }, [])

  const handleEditCancel = React.useCallback(() => {
    setEditingId(null)
    setEditingContent('')
    setEditingChapterId(null)
    setEditingCharacterId(null)
  }, [])

  const handleEditSave = React.useCallback(async () => {
    if (editingId == null) return
    const trimmed = editingContent.trim()
    if (!trimmed) {
      message.warning('设定内容不能为空')
      return
    }
    const needsChapter =
      editingLayer === SparkIdeaLayerFour.Outline || editingLayer === SparkIdeaLayerFour.Chapter
    const needsCharacter = editingLayer === SparkIdeaLayerFour.Character
    if (needsChapter && editingChapterId == null) {
      message.warning(`请先选择关联${editingLayer === SparkIdeaLayerFour.Outline ? '大纲' : '章节'}`)
      return
    }
    if (needsCharacter && editingCharacterId == null) {
      message.warning('请先选择关联人物')
      return
    }
    setEditSaving(true)
    const res = await window.electronAPI.updateSparkIdea({
      id: editingId,
      data: {
        content: trimmed,
        layer: SPARK_IDEA_LAYER_LABELS[editingLayer] as SparkIdeaLayer,
        // 显式写 null 把不再需要的关联清除
        chapter_id: needsChapter ? editingChapterId : null,
        character_id: needsCharacter ? editingCharacterId : null,
      },
    })
    setEditSaving(false)
    if (res.success && res.data) {
      const updated = res.data as AiSparkIdea
      setSparkIdeas((prev) => prev.map((m) => (m.id === editingId ? updated : m)))
      handleEditCancel()
    } else {
      message.error(res.error || '保存失败')
    }
  }, [editingId, editingContent, editingLayer, editingChapterId, editingCharacterId, handleEditCancel])

  const handleAddForeshadowing = React.useCallback(async () => {
    if (bookId == null || foreshadowChapterId == null || !foreshadowContent.trim()) return
    setAddingForeshadow(true)
    const res = await window.electronAPI.addForeshadowing({
      bookId,
      chapterId: foreshadowChapterId,
      content: foreshadowContent.trim(),
      type: foreshadowType,
    })
    setAddingForeshadow(false)
    if (res.success && res.data) {
      setForeshadowing((prev) => [res.data as AiForeshadowing, ...prev])
      setForeshadowContent('')
    }
  }, [bookId, foreshadowChapterId, foreshadowContent, foreshadowType])

  const handleDeleteForeshadowing = React.useCallback(async (id: number | string) => {
    await window.electronAPI.deleteForeshadowing({ id })
    setForeshadowing((prev) => prev.filter((f) => f.id !== id))
  }, [])

  const handleUpdateForeshadowingStatus = React.useCallback(
    async (id: number | string, status: '未回收' | '已回收', resolvedChapterId?: EntityId | null) => {
      const res = await window.electronAPI.updateForeshadowing({
        id,
        data: { status, resolved_chapter_id: resolvedChapterId ?? undefined },
      })
      if (res.success && res.data) {
        setForeshadowing((prev) => prev.map((f) => (f.id === id ? (res.data as AiForeshadowing) : f)))
      }
    },
    []
  )

  const chapterTitleById = React.useMemo(() => {
    const map = new Map<string, string>()
    for (const c of writingChapters) map.set(String(c.id), c.title)
    return map
  }, [writingChapters])

  const characterNameById = React.useMemo(() => {
    const map = new Map<string, string>()
    for (const c of characters) map.set(String(c.id), c.name)
    return map
  }, [characters])

  /**
   * 关联实体小标签：纯文字「类别：实体名」，按类别配色，配色用于视觉分类。
   */
  const renderSparkIdeaMeta = React.useCallback(
    (m: AiSparkIdea): React.ReactNode => {
      const layerLabel = String(m.layer)
      if (layerLabel === '大纲' || layerLabel === '章节') {
        if (m.chapter_id == null) return null
        const title = chapterTitleById.get(String(m.chapter_id)) ?? `#${m.chapter_id}`
        const variant = layerLabel === '大纲' ? 'outline' : 'chapter'
        return (
          <span className={`memory-spark-meta memory-spark-meta--${variant}`} title={`${layerLabel}：${title}`}>
            <span className="memory-spark-meta-label">{layerLabel}：</span>
            <span className="memory-spark-meta-text">{title}</span>
          </span>
        )
      }
      if (layerLabel === '人物') {
        if (m.character_id == null) return null
        const name = characterNameById.get(String(m.character_id)) ?? `#${m.character_id}`
        return (
          <span className="memory-spark-meta memory-spark-meta--character" title={`人物：${name}`}>
            <span className="memory-spark-meta-label">人物：</span>
            <span className="memory-spark-meta-text">{name}</span>
          </span>
        )
      }
      return null
    },
    [chapterTitleById, characterNameById]
  )

  const byLayer = React.useMemo(() => {
    const map = new Map<SparkIdeaLayer, AiSparkIdea[]>()
    for (const m of sparkIdeas) {
      const arr = map.get(m.layer as SparkIdeaLayer) || []
      arr.push(m)
      map.set(m.layer as SparkIdeaLayer, arr)
    }
    return SPARK_IDEA_LAYER_FOUR_VALUES.map((value) => {
      const layerLabel = SPARK_IDEA_LAYER_LABELS[value]
      return {
        layer: layerLabel,
        list: map.get(layerLabel as SparkIdeaLayer) || [],
      }
    })
  }, [sparkIdeas])

  return (
    <Modal
      title="本轮强制注入"
      open={open}
      onCancel={onCancel}
      width={680}
      destroyOnHidden
      className="ai-memory-modal"
      footer={
        activeTab === 'select' ? (
          <Button type="primary" onClick={handleSelectOk}>
            本轮带上（{checkedIds.length + checkedForeshadowingIds.length} 条）
          </Button>
        ) : null
      }
      styles={{ body: { height: '60vh', overflow: 'auto' } }}
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'select',
            label: '本轮强制注入',
            children: (
              <>
                {loading ? (
                  <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
                ) : sparkIdeas.length === 0 && foreshadowing.length === 0 ? (
                  <Empty image={false} description="暂无可强制注入的旧设定/伏笔，可在导演笔记本的「记忆 / 伏笔」中管理长期记忆" />
                ) : (
                  <div className="memory-select-list">
                    {byLayer.map(({ layer, list }) =>
                      list.length === 0 ? null : (
                        <div key={layer} className="memory-layer-block">
                          <div className="memory-layer-title">{layer}设定</div>
                          {list.map((m) => {
                            const meta = renderSparkIdeaMeta(m)
                            return (
                              <div key={m.id} className="memory-select-item">
                                <Checkbox
                                  checked={checkedIds.includes(m.id)}
                                  onChange={(e) => handleToggle(m.id, e.target.checked)}
                                />
                                <span
                                  className="memory-select-content"
                                  role="button"
                                  tabIndex={0}
                                  onClick={() => handleToggle(m.id, !checkedIds.includes(m.id))}
                                  onKeyDown={(e) => e.key === 'Enter' && handleToggle(m.id, !checkedIds.includes(m.id))}
                                >
                                  {m.content || '（无内容）'}
                                  {meta}
                                </span>
                              </div>
                            )
                          })}
                        </div>
                      )
                    )}
                    {foreshadowing.length > 0 && (
                      <div className="memory-layer-block">
                        <div className="memory-layer-title">伏笔记忆</div>
                        {foreshadowing.map((f) => (
                          <div key={`f-${f.id}`} className="memory-select-item">
                            <Checkbox
                              checked={checkedForeshadowingIds.includes(f.id)}
                              onChange={(e) => handleToggleForeshadowing(f.id, e.target.checked)}
                            />
                            <span
                              className="memory-select-content"
                              role="button"
                              tabIndex={0}
                              onClick={() =>
                                handleToggleForeshadowing(f.id, !checkedForeshadowingIds.includes(f.id))
                              }
                              onKeyDown={(e) =>
                                e.key === 'Enter' &&
                                handleToggleForeshadowing(f.id, !checkedForeshadowingIds.includes(f.id))
                              }
                            >
                              {f.content || '（无内容）'}
                              <span className="memory-foreshadow-meta memory-foreshadow-meta--inline">
                                【{chapterTitleById.get(String(f.chapter_id)) ?? f.chapter_id} · {f.type}】
                              </span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </>
            ),
          },
          {
            key: 'manage',
            label: '快速添加旧设定',
            children: (
              <div className="memory-manage-tab">
                <section className="memory-manage-section">
                  <h4 className="memory-manage-section-title">四层设定</h4>
                  <p className="memory-manage-section-desc">全局 / 大纲 / 人物 / 章节</p>
                  <div className="memory-add-card">
                    <div className="memory-add-form memory-add-form--layer">
                      <div className="memory-add-form-row memory-add-form-row--controls">
                        <Select
                          size="small"
                          value={addLayer}
                          onChange={handleAddLayerChange}
                          options={SPARK_IDEA_LAYER_FOUR_VALUES.map((v) => ({
                            label: SPARK_IDEA_LAYER_LABELS[v],
                            value: v,
                          }))}
                          className="memory-add-layer-select"
                        />
                        {(addLayer === SparkIdeaLayerFour.Outline ||
                          addLayer === SparkIdeaLayerFour.Chapter) && (
                          <Select
                            size="small"
                            placeholder={
                              addLayer === SparkIdeaLayerFour.Outline ? '关联大纲（章节）' : '关联章节'
                            }
                            value={addChapterId}
                            onChange={setAddChapterId}
                            options={writingChapters.map((c) => ({ label: c.title, value: c.id }))}
                            className="memory-add-relation-select"
                            allowClear
                            showSearch
                            optionFilterProp="label"
                            disabled={writingChapters.length === 0}
                          />
                        )}
                        {addLayer === SparkIdeaLayerFour.Character && (
                          <Select
                            size="small"
                            placeholder="关联人物"
                            value={addCharacterId}
                            onChange={setAddCharacterId}
                            options={characters.map((c) => ({ label: c.name, value: c.id }))}
                            className="memory-add-relation-select"
                            allowClear
                            showSearch
                            optionFilterProp="label"
                            disabled={characters.length === 0}
                            notFoundContent="本书暂无人物，请先在人物管理中添加"
                          />
                        )}
                      </div>
                      <div className="memory-add-form-row memory-add-form-row--content">
                        <Input.TextArea
                          placeholder="设定内容"
                          value={addContent}
                          onChange={(e) => setAddContent(e.target.value)}
                          rows={1}
                          autoSize={{ minRows: 1, maxRows: 4 }}
                          className="memory-add-content-input"
                        />
                        <Button type="primary" size="small" loading={adding} onClick={handleAdd} disabled={!addContent.trim()}>
                          添加
                        </Button>
                      </div>
                    </div>
                  </div>
                  {loading ? (
                    <div className="memory-manage-loading"><Spin /></div>
                  ) : sparkIdeas.length === 0 ? (
                    <Empty image={false} description="暂无四层设定" className="memory-manage-empty" />
                  ) : (
                    <div className="memory-manage-list">
                      {byLayer.map(({ layer, list }) =>
                        list.length === 0 ? null : (
                          <div key={layer} className="memory-layer-block">
                            <div className="memory-layer-title">{layer}设定</div>
                            {list.map((m) => {
                              const isEditing = editingId === m.id
                              if (isEditing) {
                                return (
                                  <div key={m.id} className="memory-manage-item memory-manage-item--editing">
                                    <div className="memory-manage-edit-form">
                                      <Input.TextArea
                                        autoFocus
                                        value={editingContent}
                                        onChange={(e) => setEditingContent(e.target.value)}
                                        autoSize={{ minRows: 1, maxRows: 6 }}
                                        className="memory-manage-edit-input"
                                        onPressEnter={(e) => {
                                          // Enter 保存，Shift+Enter 换行
                                          if (!e.shiftKey) {
                                            e.preventDefault()
                                            handleEditSave()
                                          }
                                        }}
                                      />
                                      <div className="memory-manage-edit-actions">
                                        <Select
                                          size="small"
                                          value={editingLayer}
                                          onChange={handleEditLayerChange}
                                          options={SPARK_IDEA_LAYER_FOUR_VALUES.map((v) => ({
                                            label: SPARK_IDEA_LAYER_LABELS[v],
                                            value: v,
                                          }))}
                                          className="memory-manage-edit-layer-select"
                                        />
                                        {(editingLayer === SparkIdeaLayerFour.Outline ||
                                          editingLayer === SparkIdeaLayerFour.Chapter) && (
                                          <Select
                                            size="small"
                                            placeholder={
                                              editingLayer === SparkIdeaLayerFour.Outline
                                                ? '关联大纲（章节）'
                                                : '关联章节'
                                            }
                                            value={editingChapterId}
                                            onChange={setEditingChapterId}
                                            options={writingChapters.map((c) => ({
                                              label: c.title,
                                              value: c.id,
                                            }))}
                                            className="memory-manage-edit-relation-select"
                                            allowClear
                                            showSearch
                                            optionFilterProp="label"
                                            disabled={writingChapters.length === 0}
                                          />
                                        )}
                                        {editingLayer === SparkIdeaLayerFour.Character && (
                                          <Select
                                            size="small"
                                            placeholder="关联人物"
                                            value={editingCharacterId}
                                            onChange={setEditingCharacterId}
                                            options={characters.map((c) => ({
                                              label: c.name,
                                              value: c.id,
                                            }))}
                                            className="memory-manage-edit-relation-select"
                                            allowClear
                                            showSearch
                                            optionFilterProp="label"
                                            disabled={characters.length === 0}
                                          />
                                        )}
                                        <Button
                                          type="primary"
                                          size="small"
                                          icon={<CheckOutlined />}
                                          loading={editSaving}
                                          onClick={handleEditSave}
                                          disabled={!editingContent.trim()}
                                        >
                                          保存
                                        </Button>
                                        <Button
                                          type="text"
                                          size="small"
                                          icon={<CloseOutlined />}
                                          onClick={handleEditCancel}
                                          disabled={editSaving}
                                        >
                                          取消
                                        </Button>
                                      </div>
                                    </div>
                                  </div>
                                )
                              }
                              const meta = renderSparkIdeaMeta(m)
                              return (
                                <div key={m.id} className="memory-manage-item">
                                  <div className="memory-manage-content-wrap">
                                    <span className="memory-manage-content">{m.content || '（无内容）'}</span>
                                    {meta}
                                  </div>
                                  <div className="memory-manage-item-actions">
                                    <Button
                                      type="text"
                                      size="small"
                                      icon={<EditOutlined />}
                                      className="memory-manage-edit-btn"
                                      onClick={() => handleEditStart(m)}
                                      title="编辑"
                                    />
                                    <Popconfirm
                                      title="删除这条本书设定？"
                                      description="删除后不可恢复，引用此设定的提示词将失效。"
                                      okText="删除"
                                      okButtonProps={{ danger: true }}
                                      cancelText="取消"
                                      placement="topRight"
                                      onConfirm={() => handleDelete(m.id)}
                                    >
                                      <Button
                                        type="text"
                                        size="small"
                                        icon={<DeleteOutlined />}
                                        className="memory-manage-delete-btn"
                                      />
                                    </Popconfirm>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )
                      )}
                    </div>
                  )}
                </section>

                <section className="memory-manage-section memory-manage-section--foreshadow">
                  <h4 className="memory-manage-section-title">伏笔记忆</h4>
                  {writingChapters.length === 0 ? (
                    <div className="memory-foreshadow-hint">请先在写作大纲中添加章节后再添加伏笔</div>
                  ) : (
                    <>
                      <div className="memory-add-card">
                        <div className="memory-add-form memory-add-form--layer memory-add-form--foreshadow">
                          <div className="memory-add-form-row memory-add-form-row--controls">
                            <Select
                              size="small"
                              placeholder="埋入章节"
                              value={foreshadowChapterId}
                              onChange={setForeshadowChapterId}
                              options={writingChapters.map((c) => ({ label: c.title, value: c.id }))}
                              className="memory-add-foreshadow-chapter"
                            />
                            <Select
                              size="small"
                              value={foreshadowType}
                              onChange={setForeshadowType}
                              options={FORESHADOWING_TYPES.map((t) => ({ label: t, value: t }))}
                              className="memory-add-foreshadow-type"
                            />
                          </div>
                          <div className="memory-add-form-row memory-add-form-row--content">
                            <Input.TextArea
                              placeholder="伏笔内容"
                              value={foreshadowContent}
                              onChange={(e) => setForeshadowContent(e.target.value)}
                              rows={1}
                              autoSize={{ minRows: 1, maxRows: 4 }}
                              className="memory-add-content-input"
                            />
                            <Button
                              type="primary"
                              size="small"
                              loading={addingForeshadow}
                              onClick={handleAddForeshadowing}
                              disabled={!foreshadowContent.trim() || foreshadowChapterId == null}
                            >
                              添加伏笔
                            </Button>
                          </div>
                        </div>
                      </div>
                      {foreshadowing.length === 0 ? (
                        <Empty image={false} description="暂无伏笔" className="memory-manage-empty" />
                      ) : (
                        <div className="memory-manage-list">
                          <div className="memory-layer-block">
                            {foreshadowing.map((f) => (
                              <div key={f.id} className="memory-manage-item memory-manage-item--foreshadow">
                                <div className="memory-manage-content-wrap">
                                  <span className="memory-manage-content">{f.content || '（无内容）'}</span>
                                  <span className="memory-foreshadow-meta">
                                    【{chapterTitleById.get(String(f.chapter_id)) ?? f.chapter_id} · {f.type}】
                                  </span>
                                </div>
                                <div className="memory-foreshadow-actions">
                                  <Select
                                    size="small"
                                    value={f.status}
                                    onChange={(status) => handleUpdateForeshadowingStatus(f.id, status)}
                                    options={[
                                      { label: '未回收', value: '未回收' },
                                      { label: '已回收', value: '已回收' },
                                    ]}
                                    className="memory-foreshadow-status-select"
                                  />
                                  {f.status === '已回收' && (
                                    <Select
                                      size="small"
                                      placeholder="回收于"
                                      allowClear
                                      value={f.resolved_chapter_id ?? undefined}
                                      onChange={(v) => handleUpdateForeshadowingStatus(f.id, '已回收', v != null ? String(v) : null)}
                                      options={writingChapters.map((c) => ({ label: c.title, value: c.id }))}
                                      className="memory-foreshadow-resolved-select"
                                    />
                                  )}
                                  <Popconfirm
                                    title="删除这条伏笔？"
                                    description="删除后不可恢复，已回收/未回收状态一并丢失。"
                                    okText="删除"
                                    okButtonProps={{ danger: true }}
                                    cancelText="取消"
                                    placement="topRight"
                                    onConfirm={() => handleDeleteForeshadowing(f.id)}
                                  >
                                    <Button
                                      type="text"
                                      size="small"
                                      icon={<DeleteOutlined />}
                                      className="memory-manage-delete-btn"
                                    />
                                  </Popconfirm>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </section>
              </div>
            ),
          },
        ]}
      />
    </Modal>
  )
}

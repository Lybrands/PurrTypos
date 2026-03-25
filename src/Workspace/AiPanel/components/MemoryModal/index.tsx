import React from 'react'
import { Button, Checkbox, Empty, Input, Modal, Select, Spin, Tabs } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import type { AiMemory, AiForeshadowing, EntityId, MemoryLayer } from '../../../../types'
import {
  FORESHADOWING_TYPES,
  MemoryLayerFour,
  MEMORY_LAYER_FOUR_VALUES,
  MEMORY_LAYER_LABELS,
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
  const [memories, setMemories] = React.useState<AiMemory[]>([])
  const [foreshadowing, setForeshadowing] = React.useState<AiForeshadowing[]>([])
  const [loading, setLoading] = React.useState(false)
  const [checkedIds, setCheckedIds] = React.useState<(number | string)[]>(selectedIds)
  const [checkedForeshadowingIds, setCheckedForeshadowingIds] = React.useState<(number | string)[]>(
    selectedForeshadowingIds
  )

  // 管理：四层记忆新增表单
  const [addLayer, setAddLayer] = React.useState<MemoryLayerFour>(MemoryLayerFour.Global)
  const [addContent, setAddContent] = React.useState('')
  const [adding, setAdding] = React.useState(false)

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
      window.electronAPI.getMemoriesByBook({ bookId }),
      window.electronAPI.getForeshadowingByBook({ bookId }),
    ]).then(([memRes, forRes]) => {
      setLoading(false)
      if (memRes.success && Array.isArray(memRes.data)) setMemories(memRes.data)
      else setMemories([])
      if (forRes.success && Array.isArray(forRes.data)) setForeshadowing(forRes.data)
      else setForeshadowing([])
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
    setAdding(true)
    const res = await window.electronAPI.addMemory({
      bookId,
      layer: MEMORY_LAYER_LABELS[addLayer] as MemoryLayer,
      content: addContent.trim(),
    })
    setAdding(false)
    if (res.success && res.data) {
      setMemories((prev) => [res.data as AiMemory, ...prev])
      setAddContent('')
    }
  }, [bookId, addLayer, addContent])

  const handleDelete = React.useCallback(async (id: number | string) => {
    await window.electronAPI.deleteMemory({ id })
    setMemories((prev) => prev.filter((m) => m.id !== id))
    setCheckedIds((prev) => prev.filter((x) => x !== id))
  }, [])

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

  const byLayer = React.useMemo(() => {
    const map = new Map<MemoryLayer, AiMemory[]>()
    for (const m of memories) {
      const arr = map.get(m.layer as MemoryLayer) || []
      arr.push(m)
      map.set(m.layer as MemoryLayer, arr)
    }
    return MEMORY_LAYER_FOUR_VALUES.map((value) => {
      const layerLabel = MEMORY_LAYER_LABELS[value]
      return {
        layer: layerLabel,
        list: map.get(layerLabel as MemoryLayer) || [],
      }
    })
  }, [memories])

  return (
    <Modal
      title="长期记忆"
      open={open}
      onCancel={onCancel}
      width={680}
      destroyOnHidden
      className="ai-memory-modal"
      footer={
        activeTab === 'select' ? (
          <Button type="primary" onClick={handleSelectOk}>
            确定选用（{checkedIds.length + checkedForeshadowingIds.length} 条）
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
            label: '选用记忆',
            children: (
              <>
                {loading ? (
                  <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
                ) : memories.length === 0 && foreshadowing.length === 0 ? (
                  <Empty image={false} description="本书暂无记忆，请先在「管理记忆」中添加" />
                ) : (
                  <div className="memory-select-list">
                    {byLayer.map(({ layer, list }) =>
                      list.length === 0 ? null : (
                        <div key={layer} className="memory-layer-block">
                          <div className="memory-layer-title">{layer}记忆</div>
                          {list.map((m) => (
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
                              </span>
                            </div>
                          ))}
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
            label: '管理记忆',
            children: (
              <div className="memory-manage-tab">
                <section className="memory-manage-section">
                  <h4 className="memory-manage-section-title">四层记忆</h4>
                  <p className="memory-manage-section-desc">全局 / 大纲 / 人物 / 章节</p>
                  <div className="memory-add-card">
                    <div className="memory-add-form memory-add-form--layer">
                      <div className="memory-add-form-row memory-add-form-row--controls">
                        <Select
                          size="small"
                          value={addLayer}
                          onChange={setAddLayer}
                          options={MEMORY_LAYER_FOUR_VALUES.map((v) => ({
          label: MEMORY_LAYER_LABELS[v],
          value: v,
        }))}
                          className="memory-add-layer-select"
                        />
                      </div>
                      <div className="memory-add-form-row memory-add-form-row--content">
                        <Input.TextArea
                          placeholder="记忆内容"
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
                  ) : memories.length === 0 ? (
                    <Empty image={false} description="暂无四层记忆" className="memory-manage-empty" />
                  ) : (
                    <div className="memory-manage-list">
                      {byLayer.map(({ layer, list }) =>
                        list.length === 0 ? null : (
                          <div key={layer} className="memory-layer-block">
                            <div className="memory-layer-title">{layer}记忆</div>
                            {list.map((m) => (
                              <div key={m.id} className="memory-manage-item">
                                <span className="memory-manage-content">{m.content || '（无内容）'}</span>
                                <Button
                                  type="text"
                                  size="small"
                                  icon={<DeleteOutlined />}
                                  className="memory-manage-delete-btn"
                                  onClick={() => handleDelete(m.id)}
                                />
                              </div>
                            ))}
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
                                  <Button
                                    type="text"
                                    size="small"
                                    icon={<DeleteOutlined />}
                                    className="memory-manage-delete-btn"
                                    onClick={() => handleDeleteForeshadowing(f.id)}
                                  />
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

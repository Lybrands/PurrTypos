import React from 'react'
import { Button, Input, Popconfirm, Select } from '../../../../ui'
import { Empty } from '../../../../ui'
import { DeleteOutlined } from '../../../../ui'
import type { AiForeshadowing } from '../../../../types'
import { FORESHADOWING_TYPES } from '../../types'
import type { WritingChapter } from './types'
import type { MemoryModalController } from './useMemoryModal'

interface ForeshadowingManagerProps {
  controller: MemoryModalController
  writingChapters: WritingChapter[]
}

const foreshadowingTypeOptions = FORESHADOWING_TYPES.map((type) => ({
  label: type,
  value: type,
}))

const foreshadowingStatusOptions: {
  label: AiForeshadowing['status']
  value: AiForeshadowing['status']
}[] = [
  { label: '未回收', value: '未回收' },
  { label: '已回收', value: '已回收' },
]

export default function ForeshadowingManager({
  controller,
  writingChapters,
}: ForeshadowingManagerProps) {
  const {
    foreshadowing,
    foreshadowChapterId,
    setForeshadowChapterId,
    foreshadowContent,
    setForeshadowContent,
    foreshadowType,
    setForeshadowType,
    addingForeshadow,
    chapterTitleById,
    handleAddForeshadowing,
    handleDeleteForeshadowing,
    handleUpdateForeshadowingStatus,
  } = controller

  if (writingChapters.length === 0) {
    return (
      <section className="memory-manage-section memory-manage-section--foreshadow">
        <h4 className="memory-manage-section-title">伏笔记忆</h4>
        <div className="memory-foreshadow-hint">请先在写作大纲中添加章节后再添加伏笔</div>
      </section>
    )
  }

  const chapterOptions = writingChapters.map((chapter) => ({
    label: chapter.title,
    value: chapter.id,
  }))

  return (
    <section className="memory-manage-section memory-manage-section--foreshadow">
      <h4 className="memory-manage-section-title">伏笔记忆</h4>
      <div className="memory-add-card">
        <div className="memory-add-form memory-add-form--layer memory-add-form--foreshadow">
          <div className="memory-add-form-row memory-add-form-row--controls">
            <Select
              size="small"
              placeholder="埋入章节"
              value={foreshadowChapterId}
              onChange={setForeshadowChapterId}
              options={chapterOptions}
              className="memory-add-foreshadow-chapter"
            />
            <Select
              size="small"
              value={foreshadowType}
              onChange={setForeshadowType}
              options={foreshadowingTypeOptions}
              className="memory-add-foreshadow-type"
            />
          </div>
          <div className="memory-add-form-row memory-add-form-row--content">
            <Input.TextArea
              placeholder="伏笔内容"
              value={foreshadowContent}
              onChange={(event) => setForeshadowContent(event.target.value)}
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
            {foreshadowing.map((item) => (
              <div
                key={item.id}
                className="memory-manage-item memory-manage-item--foreshadow"
              >
                <div className="memory-manage-content-wrap">
                  <span className="memory-manage-content">{item.content || '（无内容）'}</span>
                  <span className="memory-foreshadow-meta">
                    【{chapterTitleById.get(String(item.chapter_id)) ?? item.chapter_id} ·{' '}
                    {item.type}】
                  </span>
                </div>
                <div className="memory-foreshadow-actions">
                  <Select
                    size="small"
                    value={item.status}
                    onChange={(status: AiForeshadowing['status']) =>
                      handleUpdateForeshadowingStatus(item.id, status)
                    }
                    options={foreshadowingStatusOptions}
                    className="memory-foreshadow-status-select"
                  />
                  {item.status === '已回收' && (
                    <Select
                      size="small"
                      placeholder="回收于"
                      allowClear
                      value={item.resolved_chapter_id ?? undefined}
                      onChange={(chapterId) =>
                        handleUpdateForeshadowingStatus(
                          item.id,
                          '已回收',
                          chapterId != null ? String(chapterId) : null
                        )
                      }
                      options={chapterOptions}
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
                    onConfirm={() => handleDeleteForeshadowing(item.id)}
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
    </section>
  )
}

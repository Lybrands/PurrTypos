import React from 'react'
import { PurrButton, PurrInput, PurrPopconfirm, PurrSelect } from '@/purr-components'
import { PurrEmpty, PurrSpin } from '@/purr-components'
import { CheckIcon, CloseIcon, DeleteIcon, EditIcon } from '@/purr-components'
import {
  SPARK_IDEA_LAYER_FOUR_VALUES,
  SPARK_IDEA_LAYER_LABELS,
} from '../../types'
import type { WritingChapter } from './types'
import type { MemoryModalController } from './useMemoryModal'
import LayerRelationSelect from './LayerRelationSelect'
import SparkIdeaMeta from './SparkIdeaMeta'

interface SparkIdeaManagerProps {
  controller: MemoryModalController
  writingChapters: WritingChapter[]
}

const layerOptions = SPARK_IDEA_LAYER_FOUR_VALUES.map((value) => ({
  label: SPARK_IDEA_LAYER_LABELS[value],
  value,
}))

export default function SparkIdeaManager({
  controller,
  writingChapters,
}: SparkIdeaManagerProps) {
  const {
    sparkIdeas,
    sparkIdeasByLayer,
    characters,
    loading,
    addLayer,
    addContent,
    setAddContent,
    addChapterId,
    setAddChapterId,
    addCharacterId,
    setAddCharacterId,
    adding,
    editingId,
    editingContent,
    setEditingContent,
    editingLayer,
    editingChapterId,
    setEditingChapterId,
    editingCharacterId,
    setEditingCharacterId,
    editSaving,
    chapterTitleById,
    characterNameById,
    handleAdd,
    handleAddLayerChange,
    handleDelete,
    handleEditStart,
    handleEditLayerChange,
    handleEditCancel,
    handleEditSave,
  } = controller

  return (
    <section className="memory-manage-section">
      <h4 className="memory-manage-section-title">四层设定</h4>
      <p className="memory-manage-section-desc">全局 / 大纲 / 人物 / 章节</p>
      <div className="memory-add-card">
        <div className="memory-add-form memory-add-form--layer">
          <div className="memory-add-form-row memory-add-form-row--controls">
            <PurrSelect
              size="small"
              value={addLayer}
              onChange={handleAddLayerChange}
              options={layerOptions}
              className="memory-add-layer-select"
            />
            <LayerRelationSelect
              layer={addLayer}
              chapterId={addChapterId}
              onChapterChange={setAddChapterId}
              characterId={addCharacterId}
              onCharacterChange={setAddCharacterId}
              writingChapters={writingChapters}
              characters={characters}
              className="memory-add-relation-select"
              characterEmptyContent="本书暂无人物，请先在人物管理中添加"
            />
          </div>
          <div className="memory-add-form-row memory-add-form-row--content">
            <PurrInput.TextArea
              placeholder="设定内容"
              value={addContent}
              onChange={(event) => setAddContent(event.target.value)}
              rows={1}
              autoSize={{ minRows: 1, maxRows: 4 }}
              className="memory-add-content-input"
            />
            <PurrButton
              type="primary"
              size="small"
              loading={adding}
              onClick={handleAdd}
              disabled={!addContent.trim()}
            >
              添加
            </PurrButton>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="memory-manage-loading">
          <PurrSpin />
        </div>
      ) : sparkIdeas.length === 0 ? (
        <PurrEmpty image={false} description="暂无四层设定" className="memory-manage-empty" />
      ) : (
        <div className="memory-manage-list">
          {sparkIdeasByLayer.map(({ layer, list }) =>
            list.length === 0 ? null : (
              <div key={layer} className="memory-layer-block">
                <div className="memory-layer-title">{layer}设定</div>
                {list.map((memory) =>
                  editingId === memory.id ? (
                    <div
                      key={memory.id}
                      className="memory-manage-item memory-manage-item--editing"
                    >
                      <div className="memory-manage-edit-form">
                        <PurrInput.TextArea
                          autoFocus
                          value={editingContent}
                          onChange={(event) => setEditingContent(event.target.value)}
                          autoSize={{ minRows: 1, maxRows: 6 }}
                          className="memory-manage-edit-input"
                          onPressEnter={(event) => {
                            if (!event.shiftKey) {
                              event.preventDefault()
                              handleEditSave()
                            }
                          }}
                        />
                        <div className="memory-manage-edit-actions">
                          <PurrSelect
                            size="small"
                            value={editingLayer}
                            onChange={handleEditLayerChange}
                            options={layerOptions}
                            className="memory-manage-edit-layer-select"
                          />
                          <LayerRelationSelect
                            layer={editingLayer}
                            chapterId={editingChapterId}
                            onChapterChange={setEditingChapterId}
                            characterId={editingCharacterId}
                            onCharacterChange={setEditingCharacterId}
                            writingChapters={writingChapters}
                            characters={characters}
                            className="memory-manage-edit-relation-select"
                          />
                          <PurrButton
                            type="primary"
                            size="small"
                            icon={<CheckIcon />}
                            loading={editSaving}
                            onClick={handleEditSave}
                            disabled={!editingContent.trim()}
                          >
                            保存
                          </PurrButton>
                          <PurrButton
                            type="text"
                            size="small"
                            icon={<CloseIcon />}
                            onClick={handleEditCancel}
                            disabled={editSaving}
                          >
                            取消
                          </PurrButton>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div key={memory.id} className="memory-manage-item">
                      <div className="memory-manage-content-wrap">
                        <span className="memory-manage-content">
                          {memory.content || '（无内容）'}
                        </span>
                        <SparkIdeaMeta
                          memory={memory}
                          chapterTitleById={chapterTitleById}
                          characterNameById={characterNameById}
                        />
                      </div>
                      <div className="memory-manage-item-actions">
                        <PurrButton
                          type="text"
                          size="small"
                          icon={<EditIcon />}
                          className="memory-manage-edit-btn"
                          onClick={() => handleEditStart(memory)}
                          title="编辑"
                        />
                        <PurrPopconfirm
                          title="删除这条本书设定？"
                          description="删除后不可恢复，引用此设定的提示词将失效。"
                          okText="删除"
                          okButtonProps={{ danger: true }}
                          cancelText="取消"
                          placement="topRight"
                          onConfirm={() => handleDelete(memory.id)}
                        >
                          <PurrButton
                            type="text"
                            size="small"
                            icon={<DeleteIcon />}
                            className="memory-manage-delete-btn"
                          />
                        </PurrPopconfirm>
                      </div>
                    </div>
                  )
                )}
              </div>
            )
          )}
        </div>
      )}
    </section>
  )
}

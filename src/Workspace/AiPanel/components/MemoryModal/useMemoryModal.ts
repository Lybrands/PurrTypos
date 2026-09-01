import { services } from '@/services'
import React from 'react'
import { purrToast } from '@/purr-components'
import type {
  AiForeshadowing,
  AiSparkIdea,
  Character,
  EntityId,
  SparkIdeaLayer,
  UnifiedMemoryItem,
} from '../../../../types'
import {
  SparkIdeaLayerFour,
  SPARK_IDEA_LAYER_LABELS,
} from '../../types'
import type { MemoryId, MemoryModalProps, WritingChapter } from './types'
import {
  createChapterTitleMap,
  createCharacterNameMap,
  findLayerValue,
  groupSparkIdeasByLayer,
  needsChapterRelation,
  needsCharacterRelation,
} from './utils'

interface UseMemoryModalOptions
  extends Omit<MemoryModalProps, 'writingChapters' | 'selectedForeshadowingIds' | 'selectedLongTermMemoryIds'> {
  writingChapters: WritingChapter[]
  selectedLongTermMemoryIds: string[]
  selectedForeshadowingIds: MemoryId[]
}

export function useMemoryModal({
  open,
  onCancel,
  bookId,
  writingChapters,
  selectedIds,
  selectedLongTermMemoryIds,
  selectedForeshadowingIds,
  onSelectConfirm,
}: UseMemoryModalOptions) {
  const [activeTab, setActiveTab] = React.useState<string>('select')
  const [sparkIdeas, setSparkIdeas] = React.useState<AiSparkIdea[]>([])
  const [longTermMemories, setLongTermMemories] = React.useState<UnifiedMemoryItem[]>([])
  const [foreshadowing, setForeshadowing] = React.useState<AiForeshadowing[]>([])
  const [characters, setCharacters] = React.useState<Character[]>([])
  const [loading, setLoading] = React.useState(false)
  const [checkedIds, setCheckedIds] = React.useState<MemoryId[]>(selectedIds)
  const [checkedLongTermMemoryIds, setCheckedLongTermMemoryIds] = React.useState<string[]>(
    selectedLongTermMemoryIds
  )
  const [checkedForeshadowingIds, setCheckedForeshadowingIds] = React.useState<MemoryId[]>(
    selectedForeshadowingIds
  )

  const [addLayer, setAddLayer] = React.useState<SparkIdeaLayerFour>(SparkIdeaLayerFour.Global)
  const [addContent, setAddContent] = React.useState('')
  const [addChapterId, setAddChapterId] = React.useState<EntityId | null>(null)
  const [addCharacterId, setAddCharacterId] = React.useState<number | null>(null)
  const [adding, setAdding] = React.useState(false)

  const [editingId, setEditingId] = React.useState<MemoryId | null>(null)
  const [editingContent, setEditingContent] = React.useState('')
  const [editingLayer, setEditingLayer] = React.useState<SparkIdeaLayerFour>(
    SparkIdeaLayerFour.Global
  )
  const [editingChapterId, setEditingChapterId] = React.useState<EntityId | null>(null)
  const [editingCharacterId, setEditingCharacterId] = React.useState<number | null>(null)
  const [editSaving, setEditSaving] = React.useState(false)

  const [foreshadowChapterId, setForeshadowChapterId] = React.useState<EntityId | null>(null)
  const [foreshadowContent, setForeshadowContent] = React.useState('')
  const [foreshadowType, setForeshadowType] = React.useState<string>('悬念')
  const [addingForeshadow, setAddingForeshadow] = React.useState(false)

  React.useEffect(() => {
    if (!open || bookId == null) return
    setLoading(true)
    setCheckedIds(selectedIds)
    setCheckedLongTermMemoryIds(selectedLongTermMemoryIds)
    setCheckedForeshadowingIds(selectedForeshadowingIds)
    Promise.all([
      services.memories.getSparkIdeasByBook({ bookId }),
      services.memories.getForeshadowingByBook({ bookId }),
      services.characters.getCharacters({ bookId }),
      services.memories.listUnifiedMemories({
        bookId,
        statuses: ['active'],
        sources: ['semantic'],
        limit: 100,
      }),
    ]).then(([memoryResponse, foreshadowingResponse, characterResponse, longTermResponse]) => {
      setLoading(false)
      if (memoryResponse.success && Array.isArray(memoryResponse.data)) {
        setSparkIdeas(memoryResponse.data)
      } else {
        setSparkIdeas([])
      }
      if (foreshadowingResponse.success && Array.isArray(foreshadowingResponse.data)) {
        setForeshadowing(foreshadowingResponse.data)
      } else {
        setForeshadowing([])
      }
      if (characterResponse.success && Array.isArray(characterResponse.data)) {
        setCharacters(characterResponse.data)
      } else {
        setCharacters([])
      }
      if (longTermResponse.success && Array.isArray(longTermResponse.data?.items)) {
        setLongTermMemories(longTermResponse.data.items)
      } else {
        setLongTermMemories([])
      }
    })
  }, [open, bookId, selectedIds, selectedLongTermMemoryIds, selectedForeshadowingIds])

  const handleSelectOk = React.useCallback(() => {
    onSelectConfirm(
      checkedLongTermMemoryIds,
      checkedIds,
      checkedForeshadowingIds,
    )
    onCancel()
  }, [checkedLongTermMemoryIds, checkedIds, checkedForeshadowingIds, onSelectConfirm, onCancel])

  const handleToggleLongTermMemory = React.useCallback((id: string, checked: boolean) => {
    setCheckedLongTermMemoryIds((previous) =>
      checked ? [...previous, id] : previous.filter((candidate) => candidate !== id)
    )
  }, [])

  const handleToggle = React.useCallback((id: MemoryId, checked: boolean) => {
    setCheckedIds((previous) =>
      checked ? [...previous, id] : previous.filter((candidate) => candidate !== id)
    )
  }, [])

  const handleToggleForeshadowing = React.useCallback((id: MemoryId, checked: boolean) => {
    setCheckedForeshadowingIds((previous) =>
      checked ? [...previous, id] : previous.filter((candidate) => candidate !== id)
    )
  }, [])

  const handleAdd = React.useCallback(async () => {
    if (bookId == null || !addContent.trim()) return
    const needsChapter = needsChapterRelation(addLayer)
    const needsCharacter = needsCharacterRelation(addLayer)
    if (needsChapter && addChapterId == null) {
      purrToast.warning(
        `请先选择关联${addLayer === SparkIdeaLayerFour.Outline ? '大纲' : '章节'}`
      )
      return
    }
    if (needsCharacter && addCharacterId == null) {
      purrToast.warning('请先选择关联人物')
      return
    }

    setAdding(true)
    const response = await services.memories.addSparkIdea({
      bookId,
      layer: SPARK_IDEA_LAYER_LABELS[addLayer] as SparkIdeaLayer,
      content: addContent.trim(),
      chapterId: needsChapter ? addChapterId ?? undefined : undefined,
      characterId: needsCharacter ? addCharacterId ?? undefined : undefined,
    })
    setAdding(false)
    if (response.success && response.data) {
      setSparkIdeas((previous) => [response.data as AiSparkIdea, ...previous])
      setAddContent('')
    } else if (!response.success) {
      purrToast.error(response.error || '添加失败')
    }
  }, [bookId, addLayer, addContent, addChapterId, addCharacterId])

  const handleAddLayerChange = React.useCallback((nextLayer: SparkIdeaLayerFour) => {
    setAddLayer(nextLayer)
    setAddChapterId(null)
    setAddCharacterId(null)
  }, [])

  const handleDelete = React.useCallback(async (id: MemoryId) => {
    if (bookId == null) return
    await services.memories.deleteSparkIdea({ bookId, id })
    setSparkIdeas((previous) => previous.filter((memory) => memory.id !== id))
    setCheckedIds((previous) => previous.filter((candidate) => candidate !== id))
  }, [bookId])

  const handleEditStart = React.useCallback((memory: AiSparkIdea) => {
    setEditingId(memory.id)
    setEditingContent(memory.content || '')
    setEditingLayer(findLayerValue(memory.layer))
    setEditingChapterId(memory.chapter_id ?? null)
    setEditingCharacterId(memory.character_id ?? null)
  }, [])

  const handleEditLayerChange = React.useCallback((nextLayer: SparkIdeaLayerFour) => {
    setEditingLayer(nextLayer)
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
    if (bookId == null || editingId == null) return
    const trimmedContent = editingContent.trim()
    if (!trimmedContent) {
      purrToast.warning('设定内容不能为空')
      return
    }

    const needsChapter = needsChapterRelation(editingLayer)
    const needsCharacter = needsCharacterRelation(editingLayer)
    if (needsChapter && editingChapterId == null) {
      purrToast.warning(
        `请先选择关联${editingLayer === SparkIdeaLayerFour.Outline ? '大纲' : '章节'}`
      )
      return
    }
    if (needsCharacter && editingCharacterId == null) {
      purrToast.warning('请先选择关联人物')
      return
    }

    setEditSaving(true)
    const response = await services.memories.updateSparkIdea({
      bookId,
      id: editingId,
      data: {
        content: trimmedContent,
        layer: SPARK_IDEA_LAYER_LABELS[editingLayer] as SparkIdeaLayer,
        chapter_id: needsChapter ? editingChapterId : null,
        character_id: needsCharacter ? editingCharacterId : null,
      },
    })
    setEditSaving(false)
    if (response.success && response.data) {
      const updated = response.data as AiSparkIdea
      setSparkIdeas((previous) =>
        previous.map((memory) => (memory.id === editingId ? updated : memory))
      )
      handleEditCancel()
    } else {
      purrToast.error(response.error || '保存失败')
    }
  }, [
    editingId,
    bookId,
    editingContent,
    editingLayer,
    editingChapterId,
    editingCharacterId,
    handleEditCancel,
  ])

  const handleAddForeshadowing = React.useCallback(async () => {
    if (bookId == null || foreshadowChapterId == null || !foreshadowContent.trim()) return
    setAddingForeshadow(true)
    const response = await services.memories.addForeshadowing({
      bookId,
      chapterId: foreshadowChapterId,
      content: foreshadowContent.trim(),
      type: foreshadowType,
    })
    setAddingForeshadow(false)
    if (response.success && response.data) {
      setForeshadowing((previous) => [response.data as AiForeshadowing, ...previous])
      setForeshadowContent('')
    }
  }, [bookId, foreshadowChapterId, foreshadowContent, foreshadowType])

  const handleDeleteForeshadowing = React.useCallback(async (id: MemoryId) => {
    if (bookId == null) return
    await services.memories.deleteForeshadowing({ bookId, id })
    setForeshadowing((previous) => previous.filter((item) => item.id !== id))
  }, [bookId])

  const handleUpdateForeshadowingStatus = React.useCallback(
    async (
      id: MemoryId,
      status: AiForeshadowing['status'],
      resolvedChapterId?: EntityId | null
    ) => {
      if (bookId == null) return
      const response = await services.memories.updateForeshadowing({
        bookId,
        id,
        data: { status, resolved_chapter_id: resolvedChapterId ?? undefined },
      })
      if (response.success && response.data) {
        setForeshadowing((previous) =>
          previous.map((item) =>
            item.id === id ? (response.data as AiForeshadowing) : item
          )
        )
      }
    },
    [bookId]
  )

  const chapterTitleById = React.useMemo(
    () => createChapterTitleMap(writingChapters),
    [writingChapters]
  )
  const characterNameById = React.useMemo(
    () => createCharacterNameMap(characters),
    [characters]
  )
  const sparkIdeasByLayer = React.useMemo(
    () => groupSparkIdeasByLayer(sparkIdeas),
    [sparkIdeas]
  )

  return {
    activeTab,
    setActiveTab,
    sparkIdeas,
    longTermMemories,
    sparkIdeasByLayer,
    foreshadowing,
    characters,
    loading,
    checkedIds,
    checkedLongTermMemoryIds,
    checkedForeshadowingIds,
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
    foreshadowChapterId,
    setForeshadowChapterId,
    foreshadowContent,
    setForeshadowContent,
    foreshadowType,
    setForeshadowType,
    addingForeshadow,
    chapterTitleById,
    characterNameById,
    handleSelectOk,
    handleToggle,
    handleToggleLongTermMemory,
    handleToggleForeshadowing,
    handleAdd,
    handleAddLayerChange,
    handleDelete,
    handleEditStart,
    handleEditLayerChange,
    handleEditCancel,
    handleEditSave,
    handleAddForeshadowing,
    handleDeleteForeshadowing,
    handleUpdateForeshadowingStatus,
  }
}

export type MemoryModalController = ReturnType<typeof useMemoryModal>

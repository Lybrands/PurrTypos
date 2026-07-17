import type { AiSparkIdea, EntityId, SparkIdeaLayer } from '../../../../types'

export type MemoryId = number | string

export interface WritingChapter {
  id: EntityId
  title: string
}

export interface MemoryModalProps {
  open: boolean
  onCancel: () => void
  bookId: EntityId | null
  writingChapters?: WritingChapter[]
  selectedIds: MemoryId[]
  selectedForeshadowingIds?: MemoryId[]
  onSelectConfirm: (memoryIds: MemoryId[], foreshadowingIds: MemoryId[]) => void
}

export interface SparkIdeaGroup {
  layer: SparkIdeaLayer
  list: AiSparkIdea[]
}

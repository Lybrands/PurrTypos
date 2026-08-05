import React from 'react'
import { PurrSelect } from '@/purr-components'
import type { Character, EntityId } from '../../../../types'
import { SparkIdeaLayerFour } from '../../types'
import type { WritingChapter } from './types'

interface LayerRelationSelectProps {
  layer: SparkIdeaLayerFour
  chapterId: EntityId | null
  onChapterChange: (chapterId: EntityId | null) => void
  characterId: number | null
  onCharacterChange: (characterId: number | null) => void
  writingChapters: WritingChapter[]
  characters: Character[]
  className: string
  characterEmptyContent?: React.ReactNode
}

export default function LayerRelationSelect({
  layer,
  chapterId,
  onChapterChange,
  characterId,
  onCharacterChange,
  writingChapters,
  characters,
  className,
  characterEmptyContent,
}: LayerRelationSelectProps) {
  if (layer === SparkIdeaLayerFour.Outline || layer === SparkIdeaLayerFour.Chapter) {
    return (
      <PurrSelect
        size="small"
        placeholder={layer === SparkIdeaLayerFour.Outline ? '关联大纲（章节）' : '关联章节'}
        value={chapterId}
        onChange={onChapterChange}
        options={writingChapters.map((chapter) => ({
          label: chapter.title,
          value: chapter.id,
        }))}
        className={className}
        allowClear
        showSearch
        optionFilterProp="label"
        disabled={writingChapters.length === 0}
      />
    )
  }

  if (layer === SparkIdeaLayerFour.Character) {
    return (
      <PurrSelect
        size="small"
        placeholder="关联人物"
        value={characterId}
        onChange={onCharacterChange}
        options={characters.map((character) => ({
          label: character.name,
          value: character.id,
        }))}
        className={className}
        allowClear
        showSearch
        optionFilterProp="label"
        disabled={characters.length === 0}
        notFoundContent={characterEmptyContent}
      />
    )
  }

  return null
}

import type { AiSparkIdea, Character, SparkIdeaLayer } from '../../../../types'
import {
  SparkIdeaLayerFour,
  SPARK_IDEA_LAYER_FOUR_VALUES,
  SPARK_IDEA_LAYER_LABELS,
} from '../../types'
import type { SparkIdeaGroup, WritingChapter } from './types'

export function needsChapterRelation(layer: SparkIdeaLayerFour): boolean {
  return layer === SparkIdeaLayerFour.Outline || layer === SparkIdeaLayerFour.Chapter
}

export function needsCharacterRelation(layer: SparkIdeaLayerFour): boolean {
  return layer === SparkIdeaLayerFour.Character
}

export function findLayerValue(layer: SparkIdeaLayer): SparkIdeaLayerFour {
  return (
    SPARK_IDEA_LAYER_FOUR_VALUES.find((value) => SPARK_IDEA_LAYER_LABELS[value] === layer) ??
    SparkIdeaLayerFour.Global
  )
}

export function groupSparkIdeasByLayer(sparkIdeas: AiSparkIdea[]): SparkIdeaGroup[] {
  const ideasByLayer = new Map<SparkIdeaLayer, AiSparkIdea[]>()
  for (const sparkIdea of sparkIdeas) {
    const layerIdeas = ideasByLayer.get(sparkIdea.layer) ?? []
    layerIdeas.push(sparkIdea)
    ideasByLayer.set(sparkIdea.layer, layerIdeas)
  }

  return SPARK_IDEA_LAYER_FOUR_VALUES.map((value) => {
    const layer = SPARK_IDEA_LAYER_LABELS[value] as SparkIdeaLayer
    return { layer, list: ideasByLayer.get(layer) ?? [] }
  })
}

export function createChapterTitleMap(chapters: WritingChapter[]): Map<string, string> {
  return new Map(chapters.map((chapter) => [String(chapter.id), chapter.title]))
}

export function createCharacterNameMap(characters: Character[]): Map<string, string> {
  return new Map(characters.map((character) => [String(character.id), character.name]))
}

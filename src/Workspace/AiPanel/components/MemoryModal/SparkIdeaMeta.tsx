import React from 'react'
import type { AiSparkIdea } from '../../../../types'

interface SparkIdeaMetaProps {
  memory: AiSparkIdea
  chapterTitleById: Map<string, string>
  characterNameById: Map<string, string>
}

export default function SparkIdeaMeta({
  memory,
  chapterTitleById,
  characterNameById,
}: SparkIdeaMetaProps) {
  const layer = String(memory.layer)
  if (layer === '大纲' || layer === '章节') {
    if (memory.chapter_id == null) return null
    const title = chapterTitleById.get(String(memory.chapter_id)) ?? `#${memory.chapter_id}`
    const variant = layer === '大纲' ? 'outline' : 'chapter'
    return (
      <span
        className={`memory-spark-meta memory-spark-meta--${variant}`}
        title={`${layer}：${title}`}
      >
        <span className="memory-spark-meta-label">{layer}：</span>
        <span className="memory-spark-meta-text">{title}</span>
      </span>
    )
  }

  if (layer === '人物') {
    if (memory.character_id == null) return null
    const name =
      characterNameById.get(String(memory.character_id)) ?? `#${memory.character_id}`
    return (
      <span className="memory-spark-meta memory-spark-meta--character" title={`人物：${name}`}>
        <span className="memory-spark-meta-label">人物：</span>
        <span className="memory-spark-meta-text">{name}</span>
      </span>
    )
  }

  return null
}

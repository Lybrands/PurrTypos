import React from 'react'
import type { WritingChapter } from './types'
import type { MemoryModalController } from './useMemoryModal'
import ForeshadowingManager from './ForeshadowingManager'
import SparkIdeaManager from './SparkIdeaManager'

interface ManageTabProps {
  controller: MemoryModalController
  writingChapters: WritingChapter[]
}

export default function ManageTab({ controller, writingChapters }: ManageTabProps) {
  return (
    <div className="memory-manage-tab">
      <SparkIdeaManager controller={controller} writingChapters={writingChapters} />
      <ForeshadowingManager controller={controller} writingChapters={writingChapters} />
    </div>
  )
}

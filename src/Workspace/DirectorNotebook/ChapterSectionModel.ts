import type { Chapter, EntityId } from '../../types'

export interface ChapterSectionModel {
  volumes: Chapter[]
  chaptersByVolumeId: Map<EntityId, Chapter[]>
  writableChapters: Chapter[]
  chaptersById: Map<EntityId, Chapter>
}

export function buildChapterSectionModel(
  chapters: Chapter[],
  enableVolume: boolean,
): ChapterSectionModel {
  const volumes: Chapter[] = []
  const chaptersByVolumeId = new Map<EntityId, Chapter[]>()

  if (enableVolume) {
    for (const chapter of chapters) {
      if (chapter.parent_id == null) {
        volumes.push(chapter)
        continue
      }
      const siblings = chaptersByVolumeId.get(chapter.parent_id) ?? []
      siblings.push(chapter)
      chaptersByVolumeId.set(chapter.parent_id, siblings)
    }
  }

  return {
    volumes,
    chaptersByVolumeId,
    writableChapters: enableVolume
      ? chapters.filter((chapter) => chapter.parent_id != null)
      : chapters,
    chaptersById: new Map(chapters.map((chapter) => [chapter.id, chapter])),
  }
}

export function numberedChapterTitle(
  unit: '章' | '卷',
  number: number,
  subtitle = '',
): string {
  const trimmedSubtitle = subtitle.trim()
  return trimmedSubtitle
    ? `第${number}${unit} ${trimmedSubtitle}`
    : `第${number}${unit}`
}

export function getItemAndDescendantIds(
  chapter: Chapter,
  enableVolume: boolean,
  chaptersByVolumeId: Map<EntityId, Chapter[]>,
): EntityId[] {
  if (!enableVolume || chapter.parent_id != null) return [chapter.id]
  return [chapter.id, ...(chaptersByVolumeId.get(chapter.id) ?? []).map((child) => child.id)]
}

export function expandSelectedChapterIds(
  selectedIds: ReadonlySet<EntityId>,
  enableVolume: boolean,
  chaptersById: Map<EntityId, Chapter>,
  chaptersByVolumeId: Map<EntityId, Chapter[]>,
): EntityId[] {
  if (!enableVolume) return Array.from(selectedIds)

  const expandedIds = new Set<EntityId>()
  for (const id of selectedIds) {
    expandedIds.add(id)
    if (chaptersById.get(id)?.parent_id == null) {
      for (const child of chaptersByVolumeId.get(id) ?? []) expandedIds.add(child.id)
    }
  }
  return Array.from(expandedIds)
}

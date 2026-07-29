import type {
  Chapter,
  EntityId,
  ScreenplaySourceScope,
  ScreenplaySourceScopeMode,
  ScreenplaySourceScopeRequest,
} from '../types'

export interface SourceChapterOption {
  id: EntityId
  title: string
  index: number
  volumeId: EntityId | null
  volumeTitle: string | null
}

export interface SourceVolumeOption {
  id: EntityId
  title: string
  chapterIds: EntityId[]
}

export interface SourceStructure {
  chapters: SourceChapterOption[]
  volumes: SourceVolumeOption[]
}

export interface ResolvedSourceScopeSelection {
  request: ScreenplaySourceScopeRequest | null
  chapters: SourceChapterOption[]
  summary: string
  error: string | null
}

export function buildSourceStructure(rows: Chapter[]): SourceStructure {
  const childrenByParent = new Map<EntityId, Chapter[]>()
  const parentIds = new Set<EntityId>()
  rows.forEach((row) => {
    if (!row.parent_id) return
    parentIds.add(row.parent_id)
    const siblings = childrenByParent.get(row.parent_id) ?? []
    siblings.push(row)
    childrenByParent.set(row.parent_id, siblings)
  })
  childrenByParent.forEach((children) => {
    children.sort((left, right) => left.sort - right.sort)
  })
  const topLevel = rows
    .filter((row) => !row.parent_id)
    .sort((left, right) => left.sort - right.sort)
  const chapters: SourceChapterOption[] = []
  const volumes: SourceVolumeOption[] = []
  topLevel.forEach((row) => {
    if (parentIds.has(row.id)) {
      const children = childrenByParent.get(row.id) ?? []
      const chapterIds: EntityId[] = []
      children.forEach((chapter) => {
        chapterIds.push(chapter.id)
        chapters.push({
          id: chapter.id,
          title: chapter.title,
          index: chapters.length + 1,
          volumeId: row.id,
          volumeTitle: row.title,
        })
      })
      if (chapterIds.length > 0) {
        volumes.push({ id: row.id, title: row.title, chapterIds })
      }
      return
    }
    chapters.push({
      id: row.id,
      title: row.title,
      index: chapters.length + 1,
      volumeId: null,
      volumeTitle: null,
    })
  })
  return { chapters, volumes }
}

export function resolveSourceScopeSelection({
  structure,
  mode,
  count,
  chapterIds,
  volumeIds,
}: {
  structure: SourceStructure
  mode: ScreenplaySourceScopeMode
  count: number
  chapterIds: EntityId[]
  volumeIds: EntityId[]
}): ResolvedSourceScopeSelection {
  const wholeBook = (): ResolvedSourceScopeSelection => ({
    request: { mode: 'whole_book' },
    chapters: structure.chapters,
    summary: structure.chapters.length > 0
      ? `整本作品 · 当前 ${structure.chapters.length} 章`
      : '整本作品 · 当前暂无正文章节',
    error: null,
  })
  if (mode === 'whole_book') {
    return structure.chapters.length > 0
      ? wholeBook()
      : failure('来源作品还没有可用于改编的正文章节')
  }

  let selected: SourceChapterOption[] = []
  let request: ScreenplaySourceScopeRequest
  let prefix = ''
  if (mode === 'first_chapters') {
    if (structure.chapters.length === 0) {
      return failure('来源作品还没有可用于改编的正文章节')
    }
    if (!Number.isInteger(count) || count < 1 || count > structure.chapters.length) {
      return failure(`请输入 1–${structure.chapters.length} 的章节数`)
    }
    selected = structure.chapters.slice(0, count)
    request = { mode, count }
    prefix = `前 ${count} 章`
  } else if (mode === 'first_volumes') {
    if (structure.volumes.length === 0) {
      return failure('这部作品没有非空的分卷结构')
    }
    if (!Number.isInteger(count) || count < 1 || count > structure.volumes.length) {
      return failure(`请输入 1–${structure.volumes.length} 的卷数`)
    }
    const selectedVolumes = structure.volumes.slice(0, count)
    const allowed = new Set(selectedVolumes.flatMap((volume) => volume.chapterIds))
    selected = structure.chapters.filter((chapter) => allowed.has(chapter.id))
    request = { mode, count }
    prefix = `前 ${count} 卷`
  } else if (mode === 'selected_chapters') {
    const allowed = new Set(chapterIds)
    selected = structure.chapters.filter((chapter) => allowed.has(chapter.id))
    if (selected.length === 0) return failure('请至少选择一个章节')
    request = { mode, chapterIds: selected.map((chapter) => chapter.id) }
    prefix = `指定 ${selected.length} 章`
  } else {
    const selectedVolumes = structure.volumes.filter(
      (volume) => volumeIds.includes(volume.id),
    )
    if (selectedVolumes.length === 0) return failure('请至少选择一个卷')
    const allowed = new Set(selectedVolumes.flatMap((volume) => volume.chapterIds))
    selected = structure.chapters.filter((chapter) => allowed.has(chapter.id))
    request = { mode, volumeIds: selectedVolumes.map((volume) => volume.id) }
    prefix = `指定 ${selectedVolumes.length} 卷`
  }

  return {
    request,
    chapters: selected,
    summary: `${prefix} · ${selected.length} 章 · ${chapterSpan(selected)}`,
    error: null,
  }
}

export function describePersistedSourceScope(scope: ScreenplaySourceScope): string {
  const count = scope.requestedCount
  if (scope.mode === 'whole_book') return '整本作品'
  if (scope.mode === 'first_chapters') return `前 ${count ?? scope.chapterIds.length} 章`
  if (scope.mode === 'first_volumes') return `前 ${count ?? scope.volumeIds.length} 卷`
  if (scope.mode === 'selected_chapters') return `指定 ${scope.chapterIds.length} 章`
  return `指定 ${scope.volumeIds.length} 卷`
}

function chapterSpan(chapters: SourceChapterOption[]): string {
  const first = chapters[0]
  const last = chapters.at(-1)
  if (!first || !last) return '暂无章节'
  if (first.id === last.id) return first.title
  return `${first.title}—${last.title}`
}

function failure(error: string): ResolvedSourceScopeSelection {
  return { request: null, chapters: [], summary: error, error }
}

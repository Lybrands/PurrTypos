import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSourceStructure,
  describePersistedSourceScope,
  resolveSourceScopeSelection,
} from './sourceScope.ts'

const structure = buildSourceStructure([
  {
    id: 'volume-2',
    outline_id: 'writing',
    title: '第二卷',
    level: 1,
    progress: 'todo',
    sort: 2,
    parent_id: null,
  },
  {
    id: 'chapter-3',
    outline_id: 'writing',
    title: '第三章',
    level: 2,
    progress: 'todo',
    sort: 1,
    parent_id: 'volume-2',
  },
  {
    id: 'volume-1',
    outline_id: 'writing',
    title: '第一卷',
    level: 1,
    progress: 'todo',
    sort: 1,
    parent_id: null,
  },
  {
    id: 'chapter-2',
    outline_id: 'writing',
    title: '第二章',
    level: 2,
    progress: 'todo',
    sort: 2,
    parent_id: 'volume-1',
  },
  {
    id: 'chapter-1',
    outline_id: 'writing',
    title: '第一章',
    level: 2,
    progress: 'todo',
    sort: 1,
    parent_id: 'volume-1',
  },
])

test('buildSourceStructure follows reading order and excludes volume nodes', () => {
  assert.deepEqual(
    structure.chapters.map((chapter) => chapter.id),
    ['chapter-1', 'chapter-2', 'chapter-3'],
  )
  assert.deepEqual(
    structure.volumes.map((volume) => volume.id),
    ['volume-1', 'volume-2'],
  )
})

test('first-volume selection emits the backend contract and chapter preview', () => {
  const resolved = resolveSourceScopeSelection({
    structure,
    mode: 'first_volumes',
    count: 1,
    chapterIds: [],
    volumeIds: [],
  })

  assert.deepEqual(resolved.request, {
    mode: 'first_volumes',
    count: 1,
  })
  assert.deepEqual(
    resolved.chapters.map((chapter) => chapter.id),
    ['chapter-1', 'chapter-2'],
  )
  assert.match(resolved.summary, /前 1 卷 · 2 章/)
})

test('selected chapters are normalized to book order', () => {
  const resolved = resolveSourceScopeSelection({
    structure,
    mode: 'selected_chapters',
    count: 1,
    chapterIds: ['chapter-3', 'chapter-1'],
    volumeIds: [],
  })

  assert.deepEqual(resolved.request, {
    mode: 'selected_chapters',
    chapterIds: ['chapter-1', 'chapter-3'],
  })
})

test('empty explicit selections are blocked before project creation', () => {
  const resolved = resolveSourceScopeSelection({
    structure,
    mode: 'selected_volumes',
    count: 1,
    chapterIds: [],
    volumeIds: [],
  })

  assert.equal(resolved.request, null)
  assert.equal(resolved.error, '请至少选择一个卷')
})

test('whole-book adaptation is blocked when the source has no chapters', () => {
  const resolved = resolveSourceScopeSelection({
    structure: buildSourceStructure([]),
    mode: 'whole_book',
    count: 1,
    chapterIds: [],
    volumeIds: [],
  })

  assert.equal(resolved.request, null)
  assert.equal(resolved.error, '来源作品还没有可用于改编的正文章节')
})

test('persisted scope has a compact project label', () => {
  assert.equal(describePersistedSourceScope({
    schemaVersion: 1,
    mode: 'selected_chapters',
    requestedCount: null,
    chapterIds: ['chapter-1', 'chapter-3'],
    volumeIds: [],
    chapters: [],
  }), '指定 2 章')
})

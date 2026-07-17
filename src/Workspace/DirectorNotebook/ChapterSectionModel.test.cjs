'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../scripts/load-typescript-module.cjs')

const {
  buildChapterSectionModel,
  expandSelectedChapterIds,
  getItemAndDescendantIds,
  numberedChapterTitle,
} = loadTypeScriptModule(path.join(__dirname, 'ChapterSectionModel.ts'))

const chapters = [
  { id: 'v1', title: 'Volume One', parent_id: null },
  { id: 'c1', title: 'Chapter One', parent_id: 'v1' },
  { id: 'c2', title: 'Chapter Two', parent_id: 'v1' },
  { id: 'v2', title: 'Volume Two', parent_id: null },
  { id: 'c3', title: 'Chapter Three', parent_id: 'v2' },
]

test('buildChapterSectionModel groups volumes and writable chapters', () => {
  const model = buildChapterSectionModel(chapters, true)

  assert.deepEqual(model.volumes.map((item) => item.id), ['v1', 'v2'])
  assert.deepEqual(model.chaptersByVolumeId.get('v1').map((item) => item.id), ['c1', 'c2'])
  assert.deepEqual(model.writableChapters.map((item) => item.id), ['c1', 'c2', 'c3'])
  assert.equal(model.chaptersById.get('c3').title, 'Chapter Three')
})

test('batch selection expands volumes and deduplicates selected children', () => {
  const model = buildChapterSectionModel(chapters, true)
  const expanded = expandSelectedChapterIds(
    new Set(['v1', 'c1']),
    true,
    model.chaptersById,
    model.chaptersByVolumeId,
  )

  assert.deepEqual(new Set(expanded), new Set(['v1', 'c1', 'c2']))
  assert.deepEqual(
    getItemAndDescendantIds(chapters[0], true, model.chaptersByVolumeId),
    ['v1', 'c1', 'c2'],
  )
})

test('numberedChapterTitle trims optional subtitles', () => {
  assert.equal(numberedChapterTitle('章', 3), '第3章')
  assert.equal(numberedChapterTitle('卷', 2, '  Turning Point  '), '第2卷 Turning Point')
})

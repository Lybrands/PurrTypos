import assert from 'node:assert/strict'
import { test } from 'node:test'
import { toolCallDisplayRow } from './toolCallLabels.ts'

const chapters = [
  { id: 'ch-1', title: '第一章 雨夜' },
  { id: 'ch-2', title: '第二章 旧宅' },
]
const outlines = [{ id: 'ol-9', title: '第一卷大纲' }] as never

const wideChapters = [
  { id: 'c1', title: '雨夜' },
  { id: 'c2', title: '旧宅' },
  { id: 'c3', title: '密谈' },
  { id: 'c4', title: '追凶' },
  { id: 'c5', title: '收网' },
]
const characters = [
  { id: 1, name: '林晚' },
  { id: 2, name: '陈默' },
  { id: 3, name: '赵四' },
]

test('writing read tools resolve chapter titles from the local catalog', () => {
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: ['ch-2'] }, chapters, outlines).label,
    '查看第2章《旧宅》章节内容',
  )
  assert.equal(
    toolCallDisplayRow('readWritingChapters', {}, chapters, outlines).label,
    '查看章节内容',
  )
})

test('contiguous chapter reads render a numeric range', () => {
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: ['c2', 'c3', 'c4'] }, wideChapters, outlines).label,
    '读取第2章《旧宅》到第4章《追凶》',
  )
  assert.equal(
    toolCallDisplayRow('batchGetChapterContents', { chapterIds: ['c1', 'c2'] }, wideChapters, outlines).label,
    '查看第1章《雨夜》到第2章《旧宅》',
  )
})

test('sparse chapter reads list each chapter individually', () => {
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: ['c2', 'c5'] }, wideChapters, outlines).label,
    '读取第2章《旧宅》、第5章《收网》',
  )
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: ['c1', 'c3', 'c5', 'c2'] }, wideChapters, outlines).label,
    '读取第1章《雨夜》、第2章《旧宅》、第3章《密谈》等4章',
  )
  // 目录未加载/无法解析时退回计数文案
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: ['c2', 'c9'] }, wideChapters, outlines).label,
    '读取 2 章正文',
  )
})

test('character reads resolve ids into names via the local catalog', () => {
  assert.equal(
    toolCallDisplayRow('getBookCharacters', { characterIds: [1, 3] }, [], [], undefined, characters).label,
    '读取人物详情（林晚、赵四）',
  )
  assert.equal(
    toolCallDisplayRow('getBookCharacters', { characterIds: [2] }, [], [], undefined, characters).label,
    '读取人物详情（陈默）',
  )
  // 目录缺失或 ID 无法全部解析时退回计数文案
  assert.equal(
    toolCallDisplayRow('getBookCharacters', { characterIds: [1, 9] }, [], [], undefined, characters).label,
    '读取 2 位人物详情',
  )
  assert.equal(
    toolCallDisplayRow('getBookCharacters', {}, [], [], undefined, characters).label,
    '查看人物信息',
  )
})

test('writing search tools surface the queried text', () => {
  assert.equal(
    toolCallDisplayRow('searchWritingMemories', { query: '主角的童年阴影' }, [], []).label,
    '检索写作记忆“主角的童年阴影”',
  )
  assert.equal(
    toolCallDisplayRow('searchNovelKnowledge', { query: 'x'.repeat(50) }, [], []).label.includes('…'),
    true,
  )
  assert.equal(
    toolCallDisplayRow('searchWritingMemories', {}, [], []).label,
    '检索写作记忆',
  )
})

test('writing memory reads summarize counts and kinds', () => {
  assert.equal(
    toolCallDisplayRow('readWritingMemories', {
      refs: [
        { kind: 'spark', id: 's1' },
        { kind: 'foreshadowing', id: 'f1' },
        { kind: 'foreshadowing', id: 'f2' },
      ],
    }, [], []).label,
    '读取 3 条写作记忆（灵感、伏笔）',
  )
})

test('entity filters and name filters render readable suffixes', () => {
  assert.equal(
    toolCallDisplayRow('listSettingEntities', { entityType: 'location' }, [], []).label,
    '查看设定目录（地点类）',
  )
  assert.equal(
    toolCallDisplayRow('getBookCharacters', { names: ['林晚', '陈默', '赵四', '王五'] }, [], []).label,
    '读取人物详情（林晚、陈默、赵四等）',
  )
  assert.equal(
    toolCallDisplayRow('readWritingOutlines', { outlineIds: ['ol-9'] }, chapters, outlines).label,
    '查看《第一卷大纲》大纲详情',
  )
})

test('chapter creation labels summarize the batch with kind', () => {
  assert.equal(
    toolCallDisplayRow('createWritingChapters', {
      chapters: [{ title: '第二章' }, { title: '第三章' }],
    }, [], []).label,
    '创建章节《第二章》《第三章》',
  )
  assert.equal(
    toolCallDisplayRow('createWritingChapters', {
      chapters: [{ title: '第一卷', isVolume: true }],
    }, [], []).label,
    '创建卷《第一卷》',
  )
  const five = Array.from({ length: 5 }, (_v, i) => ({ title: `第${i + 2}章` }))
  assert.equal(
    toolCallDisplayRow('createWritingChapters', { chapters: five }, [], []).label,
    '创建章节《第2章》《第3章》《第4章》等 5 项',
  )
  assert.equal(
    toolCallDisplayRow('createWritingChapters', {}, [], []).label,
    '创建章节/卷',
  )
})

test('chapter numbers prefer the numeric prefix in titles over catalog position', () => {
  // 复现线上案例：目录含卷节点导致位置序号与真实章节号错位（第 54 项标题是「第53章」）
  const offsetChapters = [
    { id: 'v1', title: '第一卷 江南案' },
    ...Array.from({ length: 54 }, (_v, i) => ({ id: `c${i + 1}`, title: `第${i + 1}章` })),
  ]
  const c53 = offsetChapters.find((c) => c.title === '第53章')!
  const c54 = offsetChapters.find((c) => c.title === '第54章')!
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: [c53.id, c54.id] }, offsetChapters, []).label,
    '读取第53章到第54章',
  )
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: [c54.id] }, offsetChapters, []).label,
    '查看第54章章节内容',
  )
  // 标题带编号且有剩余文字：编号取标题，展示剩余标题
  const titled = [
    { id: 'x1', title: '第1章 雨夜' },
    { id: 'x2', title: '第2章 旧宅' },
  ]
  assert.equal(
    toolCallDisplayRow('readWritingChapters', { chapterIds: ['x1', 'x2'] }, titled, []).label,
    '读取第1章《雨夜》到第2章《旧宅》',
  )
})

test('screenplay search tools surface the projected query', () => {
  assert.equal(
    toolCallDisplayRow('searchScreenplayDeliverables', {
      searchQuery: '第 3 集结局钩子',
    }, [], [], undefined).label,
    '检索剧本交付物“第 3 集结局钩子”',
  )
  assert.equal(
    toolCallDisplayRow('searchSourceText', {
      searchQuery: '长翅膀的人',
    }, [], [], undefined).label,
    '检索原文“长翅膀的人”',
  )
  assert.equal(
    toolCallDisplayRow('searchScreenplayDeliverables', {}, [], [], undefined).label,
    '检索剧本交付物',
  )
})

test('screenplay read targets list projected chapter titles', () => {
  assert.equal(
    toolCallDisplayRow('readSourceChapters', {
      readTargets: ['第1章 清河桥', '第2章 迷途'],
    }, [], [], undefined).label,
    '读取原文章节：第1章 清河桥、第2章 迷途',
  )
  const many = Array.from({ length: 5 }, (_v, i) => `第${i + 1}章`);
  assert.equal(
    toolCallDisplayRow('readScreenplayTaskDependencies', {
      readTargets: many,
    }, [], [], undefined).label,
    '读取剧本任务依赖：第1章、第2章、第3章等 5 项',
  )
})

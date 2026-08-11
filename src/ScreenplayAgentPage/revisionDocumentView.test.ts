import assert from 'node:assert/strict'
import test from 'node:test'
import type { ScreenplayV2RevisionPart } from '../types.ts'
import {
  revisionPartMarkdown,
  revisionPartTitle,
  structuredContentToMarkdown,
} from './revisionDocumentView.ts'

const episodePart: ScreenplayV2RevisionPart = {
  type: 'episode',
  key: '2',
  position: 2,
  payload: {
    episodeNumber: 2,
    episode_number: 2,
    number: 2,
    item_count: 1,
    title: '骤雨将至',
    version: 4,
    status: 'accepted',
    scenes: [{
      id: 'scene-2',
      title: '码头重逢',
      goal: '确认失踪者的身份',
      conflict: '目击者拒绝作证',
    }],
  },
  contentText: '',
  contentDigest: 'digest',
}

test('revision parts are presented as ordinary titled documents', () => {
  assert.equal(revisionPartTitle(episodePart, 1), '第 2 集 · 骤雨将至')
  assert.equal(
    revisionPartTitle({ ...episodePart, payload: { ...episodePart.payload, title: '第2集 骤雨将至' } }, 1),
    '第 2 集 · 骤雨将至',
  )
})

test('structured-only parts become readable Markdown lists instead of JSON', () => {
  const markdown = revisionPartMarkdown(episodePart)
  assert.match(markdown, /\*\*场景\*\*/)
  assert.match(markdown, /码头重逢/)
  assert.match(markdown, /确认失踪者的身份/)
  assert.doesNotMatch(markdown, /集数/)
  assert.doesNotMatch(markdown, /骤雨将至/)
  assert.doesNotMatch(markdown, /accepted/)
  assert.doesNotMatch(markdown, /版本|Version/)
  assert.doesNotMatch(markdown, /"scenes"\s*:/)
  assert.doesNotMatch(markdown, /scene-2/)
})

test('authored Markdown remains the source of truth when it exists', () => {
  assert.equal(
    revisionPartMarkdown({ ...episodePart, contentText: '## 第二集\n\n- 正文' }),
    '## 第二集\n\n- 正文',
  )
})

test('empty structured content has a readable empty state', () => {
  assert.equal(structuredContentToMarkdown({ schemaVersion: 1 }), '*暂无可阅读的正文内容*')
})

test('legacy host execution summaries are not presented as authored document text', () => {
  const markdown = structuredContentToMarkdown({
    executionSummary: '已按既定模板完成审阅。',
    scenes: [{
      title: '码头重逢',
      processSummary: '建立目标并推进冲突。',
      sceneText: '雨幕里，两人隔着码头对望。',
    }],
  })

  assert.doesNotMatch(markdown, /既定模板|建立目标并推进冲突/)
  assert.match(markdown, /雨幕里，两人隔着码头对望/)
})

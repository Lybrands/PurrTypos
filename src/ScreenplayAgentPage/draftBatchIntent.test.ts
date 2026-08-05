import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDraftBatchActions,
  inferDraftSceneCount,
  inferDraftScope,
} from './draftBatchIntent.ts'

test('draft scope keeps stable intent instead of embedding scene snapshots', () => {
  assert.equal(inferDraftScope('继续创作下一集'), 'next_episode')
  assert.equal(inferDraftScope('创作接下来三集'), 'next_3_episodes')
  assert.equal(inferDraftScope('创作全部剩余正文'), 'all_remaining')
  assert.equal(inferDraftScope('继续写十二场'), 'count')
})

test('draft batch actions expose only episode ranges', () => {
  assert.deepEqual(buildDraftBatchActions({
    pendingSceneCount: 19,
    pendingEpisodeCount: 6,
  }), [
    {
      key: 'next_3_episodes',
      label: '创作接下来 3 集',
      episodeCount: 3,
    },
    {
      key: 'next_5_episodes',
      label: '创作接下来 5 集',
      episodeCount: 5,
    },
    {
      key: 'all_remaining',
      label: '创作全部剩余 6 集',
      episodeCount: 6,
    },
  ])
})

test('draft batch actions stay hidden when only one episode remains', () => {
  assert.deepEqual(buildDraftBatchActions({
    pendingSceneCount: 4,
    pendingEpisodeCount: 1,
  }), [])
})

test('episode and all-remaining intents do not calculate scene totals', () => {
  assert.equal(inferDraftSceneCount('创作剩下所有的场景正文', {
    pendingSceneCount: 19,
  }), 1)
  assert.equal(inferDraftSceneCount('把本集剩余场景全部写完', {
    pendingSceneCount: 8,
  }), 1)
  assert.equal(inferDraftSceneCount('创作接下来三集', {
    pendingSceneCount: 12,
  }), 1)
})

test('explicit numeric scene count is recognized and bounded', () => {
  assert.equal(inferDraftSceneCount('请连续创作十二场正文', {
    pendingSceneCount: 18,
  }), 12)
  assert.equal(inferDraftSceneCount('请批量写30场', {
    pendingSceneCount: 30,
  }), 30)
})

test('ordinary draft chat keeps the single-scene default', () => {
  assert.equal(inferDraftSceneCount('下一场的冲突应该怎么处理？', {
    pendingSceneCount: 8,
  }), 1)
})

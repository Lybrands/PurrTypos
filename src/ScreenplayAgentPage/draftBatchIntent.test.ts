import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDraftBatchActions,
  draftEpisodeCountFromScope,
  draftScopeForEpisodeCount,
} from './draftBatchIntent.ts'

test('episode draft scopes support a bounded custom range', () => {
  assert.equal(draftScopeForEpisodeCount(1), 'next_episode')
  assert.equal(draftScopeForEpisodeCount(12), 'next_12_episodes')
  assert.equal(draftEpisodeCountFromScope('next_12_episodes'), 12)
  assert.equal(draftEpisodeCountFromScope('all_remaining'), null)
  assert.throws(() => draftScopeForEpisodeCount(2.5), RangeError)
  assert.throws(() => draftScopeForEpisodeCount(101), RangeError)
})

test('draft batch actions expose only episode ranges', () => {
  assert.deepEqual(buildDraftBatchActions({
    pendingSceneCount: 19,
    pendingEpisodeCount: 6,
  }), [
    {
      key: 'next_2_episodes',
      label: '连续创作 2 集',
      episodeCount: 2,
    },
    {
      key: 'next_3_episodes',
      label: '连续创作 3 集',
      episodeCount: 3,
    },
    {
      key: 'next_5_episodes',
      label: '连续创作 5 集',
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

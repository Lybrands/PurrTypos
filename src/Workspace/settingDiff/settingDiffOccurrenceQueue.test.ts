import assert from 'node:assert/strict'
import test from 'node:test'
import type { ProposedSettingDiff } from '../../types.ts'
import {
  createSettingDiffCommandLatch,
  createSettingDiffOccurrenceQueue,
} from './settingDiffOccurrenceQueue.ts'

const proposal = (proposalId: string): ProposedSettingDiff => ({
  proposalId,
  kind: 'character',
  bookId: 'book-1',
  characterId: 1,
  before: { name: '甲', tags: '', profileMd: `before-${proposalId}` },
  proposed: { name: '甲', tags: '', profileMd: `after-${proposalId}` },
})

test('same-entity occurrences queue by proposal identity and activate in order', () => {
  const queue = createSettingDiffOccurrenceQueue()
  assert.equal(queue.enqueue('character:1', proposal('proposal-2')), true)
  assert.equal(queue.enqueue('character:1', proposal('proposal-2')), true)
  assert.equal(queue.isQueued('proposal-2'), true)

  assert.equal(queue.shift('character:1')?.proposalId, 'proposal-2')
  assert.equal(queue.shift('character:1'), undefined)
  assert.equal(queue.isQueued('proposal-2'), false)
})

test('clearing a real owner removes every queued proposal', () => {
  const queue = createSettingDiffOccurrenceQueue()
  queue.enqueue('character:1', proposal('proposal-1'))
  queue.enqueue('background:book-1', {
    ...proposal('proposal-2'),
    kind: 'background',
    characterId: undefined,
    before: { content: '旧' },
    proposed: { content: '新' },
  })
  queue.clear()
  assert.equal(queue.isQueued('proposal-1'), false)
  assert.equal(queue.isQueued('proposal-2'), false)
})

test('deferred commit excludes exit and cannot delete a newer occurrence', async () => {
  let finishWrite!: () => void
  const write = new Promise<void>((resolve) => { finishWrite = resolve })
  const latch = createSettingDiffCommandLatch()
  let activeProposalId: string | undefined = 'proposal-1'
  const deleted: string[] = []

  const commit = async () => {
    if (!latch.tryBegin('character:1', 'proposal-1')) return false
    await write
    if (!latch.canComplete(
      'character:1',
      'proposal-1',
      activeProposalId,
    )) return false
    deleted.push(activeProposalId!)
    activeProposalId = undefined
    latch.release('character:1', 'proposal-1')
    return true
  }

  const pending = commit()
  assert.equal(latch.tryBegin('character:1', 'proposal-1'), false)
  assert.equal(latch.canMutate('character:1', 'proposal-1'), false)

  // A lifecycle reset/owner switch can replace the active occurrence while
  // the product write is in flight. The old completion must be a no-op.
  activeProposalId = 'proposal-2'
  finishWrite()
  assert.equal(await pending, false)
  assert.deepEqual(deleted, [])
  assert.equal(activeProposalId, 'proposal-2')
})

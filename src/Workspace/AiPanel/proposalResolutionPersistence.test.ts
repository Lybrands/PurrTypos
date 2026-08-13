import assert from 'node:assert/strict'
import test from 'node:test'
import { persistBookProposalResolution } from './proposalResolutionPersistence.ts'

test('resolution persists while its owner Run is active then retries idempotently', async () => {
  const sequence: string[] = []
  let saves = 0
  const result = await persistBookProposalResolution({
    wait: async () => {
      sequence.push('wait')
    },
    save: async () => {
      saves += 1
      sequence.push(`save:${saves}`)
      return saves === 1
        ? { success: false, error: 'temporary' }
        : { success: true }
    },
  })

  assert.equal(result, true)
  assert.deepEqual(sequence, ['save:1', 'wait', 'save:2'])
  assert.equal(saves, 2)
})

test('a resolution writes once without a terminal-state precondition', async () => {
  let saves = 0
  const result = await persistBookProposalResolution({
    wait: async () => { throw new Error('must not wait') },
    save: async () => {
      saves += 1
      return { success: true }
    },
  })
  assert.equal(result, true)
  assert.equal(saves, 1)
})

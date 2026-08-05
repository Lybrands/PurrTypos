import assert from 'node:assert/strict'
import test from 'node:test'

import { selectScreenplayAgentSession } from './sessionRestore.ts'

const sessions = [
  { id: 11, title: '故事结构讨论', scope: 'screenplay' as const },
  { id: 12, title: '空白新对话', scope: 'screenplay' as const },
]

test('re-entering a project restores the conversation that was active on exit', () => {
  assert.equal(selectScreenplayAgentSession(sessions, 11, 12)?.id, 11)
})

test('a removed or closed remembered conversation falls back to the backend session', () => {
  assert.equal(selectScreenplayAgentSession(sessions, 99, 12)?.id, 12)
})

test('an unavailable backend fallback uses the newest listed open conversation', () => {
  assert.equal(selectScreenplayAgentSession(sessions, null, 99)?.id, 12)
})

test('an empty project session list has no restoration target', () => {
  assert.equal(selectScreenplayAgentSession([], 11, 12), null)
})

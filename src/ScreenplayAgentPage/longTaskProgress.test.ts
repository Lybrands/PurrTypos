import test from 'node:test'
import assert from 'node:assert/strict'
import { parseLongTaskTime } from './longTaskProgress.ts'

test('naive ISO task timestamps are interpreted as SQLite UTC', () => {
  assert.equal(
    parseLongTaskTime('2026-08-04T11:04:00'),
    Date.parse('2026-08-04T11:04:00Z'),
  )
  assert.equal(
    parseLongTaskTime('2026-08-04 11:04:00'),
    Date.parse('2026-08-04T11:04:00Z'),
  )
  assert.equal(
    parseLongTaskTime('2026-08-04T19:04:00+08:00'),
    Date.parse('2026-08-04T19:04:00+08:00'),
  )
})

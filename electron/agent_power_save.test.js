'use strict'

const assert = require('node:assert/strict')
const test = require('node:test')
const { createAgentPowerSaveManager } = require('./agent_power_save')

function harness(states) {
  const starts = []
  const stops = []
  let nextId = 1
  const active = new Set()
  const manager = createAgentPowerSaveManager({
    backendUrl: 'http://backend.test',
    powerSaveBlocker: {
      start(mode) {
        starts.push(mode)
        const id = nextId++
        active.add(id)
        return id
      },
      isStarted: (id) => active.has(id),
      stop(id) {
        stops.push(id)
        active.delete(id)
      },
    },
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({ success: true, data: states.shift() }),
    }),
    setIntervalImpl: () => 1,
    clearIntervalImpl: () => undefined,
  })
  return { manager, starts, stops }
}

test('prevents app suspension only while enabled Agent runs are active', async () => {
  const { manager, starts, stops } = harness([
    { enabled: true, activeAgentRunCount: 1 },
    { enabled: true, activeAgentRunCount: 2 },
    { enabled: true, activeAgentRunCount: 0 },
  ])

  await manager.refresh()
  await manager.refresh()
  assert.deepEqual(starts, ['prevent-app-suspension'])
  await manager.refresh()
  assert.deepEqual(stops, [1])
})

test('does not prevent sleep when the preference is disabled', async () => {
  const { manager, starts } = harness([
    { enabled: false, activeAgentRunCount: 3 },
  ])
  await manager.refresh()
  assert.deepEqual(starts, [])
})

test('releases the blocker when the manager stops', async () => {
  const { manager, stops } = harness([
    { enabled: true, activeAgentRunCount: 1 },
  ])
  await manager.refresh()
  manager.stop()
  assert.deepEqual(stops, [1])
})

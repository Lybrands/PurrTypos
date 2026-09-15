'use strict'

const AGENT_POWER_STATE_PATH = '/api/runtime/agent-power-state'

function createAgentPowerSaveManager({
  powerSaveBlocker,
  backendUrl,
  fetchImpl = globalThis.fetch,
  setIntervalImpl = setInterval,
  clearIntervalImpl = clearInterval,
  pollIntervalMs = 3000,
  logger = console,
}) {
  let blockerId = null
  let timer = null
  let refreshPromise = null

  function release() {
    if (blockerId == null) return
    if (powerSaveBlocker.isStarted(blockerId)) powerSaveBlocker.stop(blockerId)
    blockerId = null
  }

  function applyState(state) {
    const shouldPreventSleep = state?.enabled === true
      && Number(state?.activeAgentRunCount || 0) > 0
    if (!shouldPreventSleep) {
      release()
      return
    }
    if (blockerId == null || !powerSaveBlocker.isStarted(blockerId)) {
      blockerId = powerSaveBlocker.start('prevent-app-suspension')
    }
  }

  async function refresh() {
    if (refreshPromise) return refreshPromise
    refreshPromise = (async () => {
      try {
        const response = await fetchImpl(`${backendUrl}${AGENT_POWER_STATE_PATH}`)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload = await response.json()
        applyState(payload?.data)
      } catch (error) {
        logger.warn('Unable to refresh Agent power state:', error)
      } finally {
        refreshPromise = null
      }
    })()
    return refreshPromise
  }

  async function start() {
    if (timer != null) return
    await refresh()
    timer = setIntervalImpl(() => void refresh(), pollIntervalMs)
  }

  function stop() {
    if (timer != null) clearIntervalImpl(timer)
    timer = null
    release()
  }

  return { start, stop, refresh }
}

module.exports = {
  AGENT_POWER_STATE_PATH,
  createAgentPowerSaveManager,
}

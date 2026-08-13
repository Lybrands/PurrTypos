import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

let vite
let SettingDiffProvider
let useSettingDiff

before(async () => {
  vite = await createServer({
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ SettingDiffProvider, useSettingDiff } = await vite.ssrLoadModule(
    '/src/Workspace/settingDiff/SettingDiffContext.tsx',
  ))
})

after(async () => {
  await vite?.close()
})

const proposal = (proposalId, bookId) => ({
  proposalId,
  kind: 'background',
  bookId,
  before: { content: '旧世界' },
  proposed: { content: '新世界' },
})

const ownedProposal = (proposalId, sessionId) => ({
  ...proposal(proposalId, 'book-b'),
  resolutionTarget: {
    sessionId,
    agentRunId: `run-${sessionId}`,
    prompt: `prompt-${sessionId}`,
  },
})

test('mounted provider rejects late A proposals in B and never revives resolved ids', async () => {
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    CustomEvent: globalThis.CustomEvent,
    Event: globalThis.Event,
  }
  Object.assign(globalThis, {
    window,
    document: window.document,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  let state
  function Probe() {
    state = useSettingDiff()
    return React.createElement('div', { 'data-probe': 'ready' })
  }
  const root = createRoot(window.document.getElementById('root'))
  try {
    await act(async () => {
      root.render(React.createElement(
        SettingDiffProvider,
        { bookId: 'book-b' },
        React.createElement(Probe),
      ))
    })

    await act(async () => {
      window.dispatchEvent(new window.CustomEvent('ai-propose-setting-diff', {
        detail: proposal('proposal-a', 'book-a'),
      }))
    })
    assert.deepEqual(Object.keys(state.sessions), [])

    await act(async () => {
      window.dispatchEvent(new window.CustomEvent('ai-propose-setting-diff', {
        detail: proposal('proposal-b', 'book-b'),
      }))
    })
    assert.equal(state.sessions['background:book-b'].proposalId, 'proposal-b')

    const resolution = {
      proposalId: 'proposal-b',
      sessionKey: 'background:book-b',
      kind: 'background',
      title: '故事背景',
      status: 'rejected',
      acceptedSegments: 0,
      rejectedSegments: 0,
    }
    await act(async () => {
      window.dispatchEvent(new window.CustomEvent(
        'setting-diff-resolution-hydrated',
        { detail: resolution },
      ))
      window.dispatchEvent(new window.CustomEvent('ai-propose-setting-diff', {
        detail: proposal('proposal-b', 'book-b'),
      }))
    })
    assert.deepEqual(Object.keys(state.sessions), [])
    assert.equal(state.getResolvedCard('proposal-b').status, 'rejected')
  } finally {
    await act(async () => root.unmount())
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})

test('mounted session eviction removes A and activates valid queued B', async () => {
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    CustomEvent: globalThis.CustomEvent,
    Event: globalThis.Event,
  }
  Object.assign(globalThis, {
    window,
    document: window.document,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  let state
  function Probe() {
    state = useSettingDiff()
    return React.createElement('div')
  }
  const root = createRoot(window.document.getElementById('root'))
  try {
    await act(async () => root.render(React.createElement(
      SettingDiffProvider,
      { bookId: 'book-b' },
      React.createElement(Probe),
    )))
    await act(async () => window.dispatchEvent(new window.CustomEvent(
      'setting-diff-resolution-hydrated',
      {
        detail: {
          proposalId: 'proposal-resolved-a',
          sessionKey: 'background:book-b',
          kind: 'background',
          title: '故事背景',
          status: 'rejected',
          acceptedSegments: 0,
          rejectedSegments: 1,
          ownerSessionId: 1,
        },
      },
    )))
    assert.equal(state.getResolvedCard('proposal-resolved-a').status, 'rejected')
    await act(async () => {
      window.dispatchEvent(new window.CustomEvent('ai-propose-setting-diff', {
        detail: ownedProposal('proposal-a', 1),
      }))
      window.dispatchEvent(new window.CustomEvent('ai-propose-setting-diff', {
        detail: ownedProposal('proposal-b', 2),
      }))
    })
    assert.equal(state.sessions['background:book-b'].proposalId, 'proposal-a')

    await act(async () => {
      window.dispatchEvent(new window.CustomEvent('setting-diff-owner-evicted', {
        detail: { bookId: 'book-b', sessionId: 1 },
      }))
      await Promise.resolve()
    })
    assert.equal(state.sessions['background:book-b'].proposalId, 'proposal-b')
    assert.equal(state.getResolvedCard('proposal-resolved-a'), undefined)

    await act(async () => {
      window.dispatchEvent(new window.CustomEvent('ai-propose-setting-diff', {
        detail: ownedProposal('proposal-a-late', 1),
      }))
    })
    assert.equal(state.sessions['background:book-b'].proposalId, 'proposal-b')
  } finally {
    await act(async () => root.unmount())
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})

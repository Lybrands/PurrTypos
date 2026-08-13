import assert from 'node:assert/strict'
import { test } from 'node:test'
import React from 'react'
import { act } from 'react-dom/test-utils'
import { createRoot } from 'react-dom/client'
import { parseHTML } from 'linkedom'
import { bindScrollFollowIntent } from './scrollFollowEvents.ts'

test('mounted DOM scroller handles Shift+Space and removes every listener on unmount', async () => {
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    Event: globalThis.Event,
    KeyboardEvent: globalThis.KeyboardEvent,
  }
  Object.assign(globalThis, {
    window,
    document: window.document,
    Event: window.Event,
    KeyboardEvent: window.KeyboardEvent,
    IS_REACT_ACT_ENVIRONMENT: true,
  })
  const calls = []
  function MountedScroller() {
    const ref = React.useRef(null)
    React.useEffect(() => bindScrollFollowIntent(
      ref.current,
      (atBottom) => calls.push(atBottom),
    ), [])
    return React.createElement('div', {
      ref,
      tabIndex: 0,
      'aria-label': 'mounted-scroller',
    })
  }
  const root = createRoot(window.document.getElementById('root'))
  try {
    await act(async () => root.render(React.createElement(MountedScroller)))
    const scroller = window.document.querySelector('[aria-label="mounted-scroller"]')
    Object.defineProperties(scroller, {
      scrollTop: { value: 100, writable: true },
      getBoundingClientRect: { value: () => ({ right: 100 }) },
    })
    const dispatch = (type, fields = {}) => {
      const event = new window.Event(type, { bubbles: true })
      Object.entries(fields).forEach(([key, value]) => {
        Object.defineProperty(event, key, { value })
      })
      scroller.dispatchEvent(event)
    }
    dispatch('keydown', { key: ' ', code: 'Space', shiftKey: false })
    assert.deepEqual(calls, [])
    dispatch('keydown', { key: ' ', code: 'Space', shiftKey: true })
    dispatch('wheel', { deltaY: -1 })
    dispatch('touchstart', { touches: [{ clientY: 10 }] })
    dispatch('touchmove', { touches: [{ clientY: 20 }] })
    dispatch('pointerdown', { clientX: 99 })
    scroller.scrollTop = 50
    dispatch('scroll')
    assert.deepEqual(calls, [undefined, undefined, false, false])

    await act(async () => root.unmount())
    dispatch('keydown', { key: 'ArrowUp' })
    dispatch('wheel', { deltaY: -1 })
    assert.deepEqual(calls, [undefined, undefined, false, false])
  } finally {
    Object.assign(globalThis, previous)
    delete globalThis.IS_REACT_ACT_ENVIRONMENT
  }
})

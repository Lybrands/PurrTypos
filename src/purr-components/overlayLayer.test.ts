import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getBlockingLayerStyles,
  getOverlayLayerStyle,
  isValidOverlayZIndex,
} from './overlayLayer.ts'

test('overlay z-index validation accepts only finite integers from 1000 through 1999', () => {
  assert.equal(isValidOverlayZIndex(1000), true)
  assert.equal(isValidOverlayZIndex(1999), true)
  assert.equal(isValidOverlayZIndex(999), false)
  assert.equal(isValidOverlayZIndex(2000), false)
  assert.equal(isValidOverlayZIndex(1200.5), false)
  assert.equal(isValidOverlayZIndex(Number.NaN), false)
})

test('overlay style preserves explicit values and leaves semantic defaults to CSS', () => {
  assert.equal(getOverlayLayerStyle('PurrTooltip'), undefined)
  assert.deepEqual(getOverlayLayerStyle('PurrTooltip', 1250), { zIndex: 1250 })
  assert.deepEqual(getOverlayLayerStyle('PurrTooltip', 0), { zIndex: 0 })
})

test('blocking layers place the backdrop ten below the requested surface', () => {
  assert.deepEqual(getBlockingLayerStyles('PurrDialog'), {})
  assert.deepEqual(getBlockingLayerStyles('PurrDialog', 1250), {
    backdrop: { zIndex: 1240 },
    surface: { zIndex: 1250 },
  })
})

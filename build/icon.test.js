'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const { PNG } = require('pngjs')
const {
  ICON_CANVAS_SIZE,
  ICON_CONTENT_SIZE,
  fitIconToCanvas,
} = require('./icon')

test('application icon keeps the macOS safe-area ratio', () => {
  assert.equal(ICON_CANVAS_SIZE, 1024)
  assert.equal(ICON_CONTENT_SIZE, 824)
  assert.equal((ICON_CANVAS_SIZE - ICON_CONTENT_SIZE) / 2, 100)
})

test('fitted icon has transparent padding around its content', () => {
  const source = new PNG({ width: 2, height: 2 })
  for (let index = 0; index < source.data.length; index += 4) {
    source.data[index] = 240
    source.data[index + 1] = 120
    source.data[index + 2] = 140
    source.data[index + 3] = 255
  }

  const icon = fitIconToCanvas(source, { canvasSize: 10, contentSize: 8 })
  const alphaAt = (x, y) => icon.data[(y * icon.width + x) * 4 + 3]

  assert.equal(alphaAt(0, 5), 0)
  assert.equal(alphaAt(9, 5), 0)
  assert.equal(alphaAt(5, 0), 0)
  assert.equal(alphaAt(5, 9), 0)
  assert.equal(alphaAt(1, 1), 255)
  assert.equal(alphaAt(8, 8), 255)
})

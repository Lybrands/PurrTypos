'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../scripts/load-typescript-module.cjs')

const { formatWords } = loadTypeScriptModule(
  path.join(__dirname, 'dashboardFormatters.ts'),
)

test('formatWords keeps small counts and abbreviates large counts', () => {
  assert.equal(formatWords(9999), '9999')
  assert.equal(formatWords(10000), '1.0 万')
  assert.equal(formatWords(125000), '12.5 万')
  assert.equal(formatWords(1000000), '100 万')
})

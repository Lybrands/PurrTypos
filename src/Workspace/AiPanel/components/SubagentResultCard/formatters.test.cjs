'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const { loadTypeScriptModule } = require('../../../../../scripts/load-typescript-module.cjs')

const {
  formatContinuationPlanText,
  formatPolishPayload,
  formatReviewIssues,
  stringifyItem,
  stringifyPayload,
} = loadTypeScriptModule(path.join(__dirname, 'formatters.ts'))

test('stringifyItem renders common object shapes without object coercion noise', () => {
  assert.equal(stringifyItem({ name: 'Clue', reason: 'Needed later' }), 'Clue：Needed later')
  assert.equal(stringifyItem({ title: 'Anchor' }), 'Anchor')
  assert.equal(stringifyItem({ custom: 'value' }), 'custom=value')
  assert.doesNotMatch(stringifyItem({ name: 'Clue' }), /\[object Object\]/)
})

test('formatContinuationPlanText accepts nested blueprints and object lists', () => {
  const text = formatContinuationPlanText({
    blueprint: {
      chapterGoal: 'Reveal the clue',
      beats: [{
        beatId: 2,
        type: 'turn',
        estimatedWords: 500,
        content: 'The door opens',
        keyPoints: [{ title: 'Key', description: 'Rusty' }],
      }],
      requiredMaterials: [{ name: 'Map', reason: 'Sets direction' }],
    },
  })

  assert.match(text, /【本章目标】\nReveal the clue/)
  assert.match(text, /#2 · turn · 约 500 字/)
  assert.match(text, /Key：Rusty/)
  assert.match(text, /Map：Sets direction/)
})

test('formatters tolerate malformed and circular payloads', () => {
  const circular = { label: 'root' }
  circular.self = circular

  assert.doesNotThrow(() => stringifyPayload(circular))
  assert.deepEqual(formatReviewIssues({ issues: [null, 4, { suggestion: 'Fix it' }] }), [
    { severity: '', issueType: '', span: '', suggestion: '', context: '', hasContext: false },
    { severity: '', issueType: '', span: '', suggestion: '', context: '', hasContext: false },
    { severity: '', issueType: '', span: '', suggestion: 'Fix it', context: '', hasContext: false },
  ])
  assert.deepEqual(formatPolishPayload({ finalText: 42, changeSummary: null }), {
    body: '',
    summary: '',
    rawDump: '{\n  "finalText": 42,\n  "changeSummary": null\n}',
  })
})

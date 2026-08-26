import assert from 'node:assert/strict'
import test from 'node:test'
import {
  boundWritingMethodChoices,
  cycleWritingMethodOverride,
} from './writingMethodOverrides.ts'

test('writing method choices expand only exact revisions bound to the book', () => {
  const choices = boundWritingMethodChoices([{
      id: 'binding-1', book_id: 'book-1', binding_type: 'scheme',
      method_revision_id: null, scheme_revision_id: 'scheme-v1', priority: 0,
      source: 'user', revision: {
        id: 'scheme-v1', scheme_id: 'scheme-1', version_no: 1,
        name: '方案', description: '', members_digest: 'digest', published_at: '',
        members: [{
          ordinal: 0, method_revision_id: 'method-v1', method_id: 'method-1',
          name: '方法', method_type: 'primary', version_no: 1, content_digest: 'digest',
        }],
      },
    }])
  assert.deepEqual(choices.map((item) => item.revisionId), ['method-v1'])
})

test('writing method override cycles without force/exclude overlap', () => {
  const empty = { forceRevisionIds: [], excludeRevisionIds: [] }
  const forced = cycleWritingMethodOverride(empty, 'revision-1')
  const excluded = cycleWritingMethodOverride(forced, 'revision-1')
  const cleared = cycleWritingMethodOverride(excluded, 'revision-1')
  assert.deepEqual(forced, { forceRevisionIds: ['revision-1'], excludeRevisionIds: [] })
  assert.deepEqual(excluded, { forceRevisionIds: [], excludeRevisionIds: ['revision-1'] })
  assert.deepEqual(cleared, empty)
})

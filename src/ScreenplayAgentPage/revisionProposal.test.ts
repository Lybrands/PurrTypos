import assert from 'node:assert/strict'
import test from 'node:test'
import type { ScreenplayV2RevisionDetail } from '../types.ts'
import {
  documentEpisodesFromRevision,
  documentFromRevision,
} from './revisionProposal.ts'

const revision = {
  id: 'revision-1',
  projectId: 'project-1',
  deliverableId: 'deliverable-1',
  role: 'sceneList',
  revisionNo: 2,
  parentRevisionId: null,
  contentDigest: 'digest',
  summary: {
    proposalKind: 'scene_list',
    title: '场景规划候选',
    derivedFromIds: ['structure-1'],
  },
  agentTaskId: 'task-1',
  finalizingRunId: 'run-persisted',
  createdAt: null,
  schemaVersion: 1,
  createdBy: 'agent',
  inputRevisions: { structure: 'structure-1' },
  parts: [{
    type: 'document',
    key: 'main',
    position: 0,
    payload: { scenes: [{ id: 'scene-1' }] },
    contentText: '第一场',
    contentDigest: 'part-digest',
  }],
  sources: [],
} as ScreenplayV2RevisionDetail

test('projects a document from immutable Revision content and input references', () => {
  const document = documentFromRevision(revision)
  assert.equal(document.id, revision.id)
  assert.equal(document.project_id, revision.projectId)
  assert.equal(document.kind, 'scene_list')
  assert.equal(document.title, '场景规划候选')
  assert.deepEqual(document.content_json, { scenes: [{ id: 'scene-1' }] })
  assert.equal(document.content_text, '第一场')
  assert.deepEqual(document.derived_from_ids, ['structure-1'])
})

test('document projection rejects missing content and unknown document kinds', () => {
  assert.throws(() => documentFromRevision({ ...revision, parts: [] }), /缺少主文档/)
  assert.throws(() => documentFromRevision({ ...revision, summary: {} }), /产物类型/)
})

test('derives scene-list episode item ids from native episode parts', () => {
  const episodes = documentEpisodesFromRevision({
    ...revision,
    parts: [
      revision.parts[0],
      {
        type: 'episode',
        key: '1',
        position: 1,
        payload: {
          episodeNumber: 1,
          scenes: [{ id: 'scene-1' }, { id: 'scene-2' }],
        },
        contentText: '',
        contentDigest: 'episode-digest',
      },
    ],
  })

  assert.equal(episodes.length, 1)
  assert.deepEqual(episodes[0].item_ids, ['scene-1', 'scene-2'])
})

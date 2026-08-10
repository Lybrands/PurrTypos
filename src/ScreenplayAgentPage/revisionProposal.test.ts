import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  ScreenplayRevisionRef,
  ScreenplayV2RevisionDetail,
} from '../types.ts'
import {
  documentEpisodesFromRevision,
  proposalFromRevision,
} from './revisionProposal.ts'

const reference: ScreenplayRevisionRef = {
  schemaVersion: 1,
  projectId: 'project-1',
  taskId: 'task-1',
  revisionId: 'revision-1',
  role: 'sceneList',
  revisionNo: 2,
  sourceRunId: 'run-live',
}

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
  inputRevisions: {},
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

test('hydrates a native proposal exclusively from immutable Revision content', () => {
  const proposal = proposalFromRevision(reference, revision)
  assert.equal(proposal.kind, 'scene_list')
  assert.equal(proposal.title, '场景规划候选')
  assert.deepEqual(proposal.contentJson, { scenes: [{ id: 'scene-1' }] })
  assert.deepEqual(proposal.derivedFromIds, ['structure-1'])
  assert.equal(proposal.sourceRunId, 'run-persisted')
})

test('rejects a Revision returned for a different reference', () => {
  assert.throws(
    () => proposalFromRevision(
      { ...reference, revisionId: 'revision-other' },
      revision,
    ),
    /引用与返回内容不一致/,
  )
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

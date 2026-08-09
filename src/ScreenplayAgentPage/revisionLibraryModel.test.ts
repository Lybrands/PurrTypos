import assert from 'node:assert/strict'
import test from 'node:test'
import type { ScreenplayV2WorkingCopy } from '../types.ts'
import {
  buildWorkingCopyContent,
  canApplyRevision,
  workingCopyEditorDraft,
} from './revisionLibraryModel.ts'

const workingCopy = {
  id: 'copy-1',
  projectId: 'project-1',
  deliverableId: 'deliverable-1',
  role: 'sceneList',
  baseRevisionId: 'revision-1',
  revision: 3,
  content: {
    schemaVersion: 1,
    role: 'sceneList',
    contentJson: { episodeCount: 2 },
    contentText: '主文档',
    parts: [
      {
        type: 'episode',
        key: '1',
        position: 1,
        payload: { scenes: [{ id: 'scene-1' }] },
        contentText: '第一集',
      },
      {
        type: 'episode',
        key: '2',
        position: 2,
        payload: { scenes: [{ id: 'scene-2' }] },
        contentText: '第二集',
      },
    ],
  },
} as ScreenplayV2WorkingCopy

test('working copy editor round-trip preserves part identity and edits content', () => {
  const draft = workingCopyEditorDraft(workingCopy)
  draft.mainText = '编辑后的主文档'
  draft.partText[1] = '编辑后的第二集'
  const content = buildWorkingCopyContent(workingCopy, draft)
  const parts = content.parts as Array<Record<string, unknown>>

  assert.equal(content.contentText, '编辑后的主文档')
  assert.equal(parts.length, 2)
  assert.equal(parts[0].key, '1')
  assert.equal(parts[0].position, 1)
  assert.equal(parts[1].contentText, '编辑后的第二集')
})

test('working copy editor rejects invalid structured content before saving', () => {
  const draft = workingCopyEditorDraft(workingCopy)
  draft.partJson[0] = '["not-an-object"]'
  assert.throws(
    () => buildWorkingCopyContent(workingCopy, draft),
    /必须是 JSON 对象/,
  )
})

test('only current-input non-head revisions can be applied directly', () => {
  assert.equal(canApplyRevision({ status: 'candidate', applicability: 'current' }), true)
  assert.equal(canApplyRevision({ status: 'historical', applicability: 'stale' }), false)
  assert.equal(canApplyRevision({ status: 'current', applicability: 'current' }), false)
})

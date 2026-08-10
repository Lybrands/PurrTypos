import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findWorkspaceRevision,
  deliverableRoleForProposal,
  projectFromWorkspace,
  screenplayFormatToV2,
  screenplaySourceToV2,
} from './screenplayProjectModel.ts'
import type { ScreenplayProject, ScreenplayV2Workspace } from '../types.ts'

test('projects native lifecycle and CAS revision from the workspace', () => {
  const project = {
    id: 'project-1',
    title: '旧标题',
    active_stage: 'brief',
    status: 'active',
  } as ScreenplayProject
  const workspace = {
    project: {
      id: 'project-1',
      title: '新标题',
      revision: 7,
      lifecycle: 'archived',
      format: 'featureFilm',
      source: { type: 'original' },
      brief: { approach: '人物优先', premise: '公开真相' },
      stage: 'structure',
    },
    workflow: { stage: 'structure', heads: {}, nextActions: [] },
    deliverables: [],
    candidates: [],
    workingCopies: [],
  } as ScreenplayV2Workspace

  assert.deepEqual(projectFromWorkspace(project, workspace), {
    id: 'project-1',
    title: '新标题',
    source_kind: 'original',
    source_book_id: null,
    source_scope: {
      schemaVersion: 1,
      mode: 'whole_book',
      requestedCount: null,
      chapterIds: [],
      volumeIds: [],
      chapters: [],
    },
    format: '电影',
    approach: '人物优先',
    premise: '公开真相',
    delivery_manifest: null,
    active_stage: 'structure',
    status: 'archived',
    revision: 7,
    create_time: undefined,
    update_time: undefined,
  })
})

test('maps project creation values to v2 wire enums', () => {
  assert.equal(screenplayFormatToV2('连续剧'), 'series')
  assert.deepEqual(
    screenplaySourceToV2('book', 'book-1', {
      mode: 'selected_chapters',
      chapterIds: ['chapter-1', 'chapter-2'],
    }),
    {
      type: 'book',
      bookId: 'book-1',
      scope: {
        mode: 'selectedChapters',
        chapterIds: ['chapter-1', 'chapter-2'],
      },
    },
  )
})

test('maps proposal kinds to stable deliverable roles', () => {
  assert.equal(deliverableRoleForProposal('episode_outline'), 'structure')
  assert.equal(deliverableRoleForProposal('scene_draft'), 'screenplayDraft')
})

test('reconciles a proposal to its Agent Task revision across candidate and head views', () => {
  const revision = {
    id: 'revision-1',
    deliverableId: 'deliverable-1',
    role: 'creativeBrief' as const,
    revisionNo: 1,
    parentRevisionId: null,
    contentDigest: 'digest',
    summary: {},
    agentTaskId: 'task-1',
    rootRunId: 'run-1',
    finalizingRunId: 'run-1',
  }
  const workspace = {
    candidates: [revision],
    workflow: { heads: {} },
  } as ScreenplayV2Workspace
  assert.equal(findWorkspaceRevision({
    workspace,
    role: 'creativeBrief',
    taskId: 'task-1',
  })?.id, 'revision-1')

  workspace.candidates = []
  workspace.workflow.heads.creativeBrief = revision
  assert.equal(findWorkspaceRevision({
    workspace,
    role: 'creativeBrief',
    finalizingRunId: 'run-1',
  })?.id, 'revision-1')
})

test('resolves a native result by its canonical Revision reference first', () => {
  const referenced = {
    id: 'revision-ref',
    deliverableId: 'deliverable-scenes',
    role: 'sceneList' as const,
    revisionNo: 2,
    parentRevisionId: null,
    contentDigest: 'digest-ref',
    summary: {},
    agentTaskId: 'task-ref',
    finalizingRunId: 'run-ref',
  }
  const revision = findWorkspaceRevision({
    workspace: {
      candidates: [referenced],
      workflow: { heads: {} },
    } as ScreenplayV2Workspace,
    role: 'sceneList',
    revisionId: 'revision-ref',
    taskId: 'stale-task',
  })

  assert.equal(revision?.id, 'revision-ref')
})

test('prefers finalizing run provenance over stale Task state', () => {
  const workspace = {
    candidates: [
      {
        id: 'stale-revision',
        deliverableId: 'deliverable-1',
        role: 'creativeBrief',
        revisionNo: 1,
        parentRevisionId: null,
        contentDigest: 'stale',
        summary: {},
        agentTaskId: 'stale-task',
        finalizingRunId: 'stale-run',
      },
      {
        id: 'current-revision',
        deliverableId: 'deliverable-1',
        role: 'creativeBrief',
        revisionNo: 2,
        parentRevisionId: 'stale-revision',
        contentDigest: 'current',
        summary: {},
        agentTaskId: 'current-task',
        finalizingRunId: 'current-run',
      },
    ],
    workflow: { heads: {} },
  } as ScreenplayV2Workspace

  assert.equal(findWorkspaceRevision({
    workspace,
    role: 'creativeBrief',
    taskId: 'stale-task',
    finalizingRunId: 'current-run',
  })?.id, 'current-revision')
})

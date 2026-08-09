import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findWorkspaceRevision,
  operationIntentForTask,
  operationRoleForProposal,
  operationTargetForStage,
  projectFromWorkspace,
  screenplayFormatToV2,
  screenplaySourceToV2,
  taskInputFromResumedOperation,
} from './operationWorkflow.ts'
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
    activeOperations: [],
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

test('maps workflow stages and proposal kinds to stable deliverable roles', () => {
  assert.equal(operationTargetForStage('orientation', 'book'), 'sourceAnalysis')
  assert.equal(operationTargetForStage('orientation', 'original'), 'creativeBrief')
  assert.equal(operationTargetForStage('draft', 'original'), 'screenplayDraft')
  assert.equal(operationTargetForStage('completed', 'original'), null)
  assert.equal(operationRoleForProposal('episode_outline'), 'structure')
  assert.equal(operationRoleForProposal('scene_draft'), 'screenplayDraft')
})

test('captures draft continuation scope in the operation intent', () => {
  assert.deepEqual(operationIntentForTask({
    stage: 'draft',
    prompt: '继续写下一集',
    draftScope: 'next_episode',
    draftSceneCount: 4,
  }), {
    type: 'continue',
    scope: { mode: 'next_episode', sceneCount: 4 },
    instruction: '继续写下一集',
  })

  assert.deepEqual(operationIntentForTask({
    stage: 'draft',
    prompt: '连续创作接下来十二集',
    draftScope: 'next_12_episodes',
    draftSceneCount: 1,
  }), {
    type: 'continue',
    scope: { mode: 'next_12_episodes', sceneCount: 1 },
    instruction: '连续创作接下来十二集',
  })
})

test('resumed operation restores its persisted multi-episode task input', () => {
  assert.deepEqual(taskInputFromResumedOperation({
    intent: {
      instruction: '连续创作接下来 3 集',
      scope: { mode: 'next_3_episodes', sceneCount: 1 },
    },
    fallbackPrompt: '创作下一集',
    fallbackDraftScope: 'next_episode',
    fallbackDraftSceneCount: 1,
  }), {
    prompt: '连续创作接下来 3 集',
    draftScope: 'next_3_episodes',
    draftSceneCount: 1,
  })
})

test('resumed operation ignores malformed persisted task input', () => {
  assert.deepEqual(taskInputFromResumedOperation({
    intent: {
      instruction: '   ',
      scope: { mode: 'next_invalid_episodes', sceneCount: 0 },
    },
    fallbackPrompt: '创作下一集',
    fallbackDraftScope: 'next_episode',
    fallbackDraftSceneCount: 1,
  }), {
    prompt: '创作下一集',
    draftScope: 'next_episode',
    draftSceneCount: 1,
  })
})

test('reconciles a proposal to its operation revision across candidate and head views', () => {
  const revision = {
    id: 'revision-1',
    deliverableId: 'deliverable-1',
    role: 'creativeBrief' as const,
    revisionNo: 1,
    parentRevisionId: null,
    contentDigest: 'digest',
    summary: {},
    operationId: 'operation-1',
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
    operationId: 'operation-1',
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
    operationId: 'operation-ref',
    finalizingRunId: 'run-ref',
  }
  const revision = findWorkspaceRevision({
    workspace: {
      candidates: [referenced],
      workflow: { heads: {} },
    } as ScreenplayV2Workspace,
    role: 'sceneList',
    revisionId: 'revision-ref',
    operationId: 'stale-operation',
  })

  assert.equal(revision?.id, 'revision-ref')
})

test('prefers finalizing run provenance over stale operation state', () => {
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
        operationId: 'stale-operation',
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
        operationId: 'current-operation',
        finalizingRunId: 'current-run',
      },
    ],
    workflow: { heads: {} },
  } as ScreenplayV2Workspace

  assert.equal(findWorkspaceRevision({
    workspace,
    role: 'creativeBrief',
    operationId: 'stale-operation',
    finalizingRunId: 'current-run',
  })?.id, 'current-revision')
})

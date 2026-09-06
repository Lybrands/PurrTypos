import assert from 'node:assert/strict'
import test from 'node:test'

import {
  projectFromWorkspace,
  revisionLibraryTarget,
  screenplayFormatToV2,
  screenplaySourceToV2,
} from './screenplayProjectModel.ts'
import type { ScreenplayProject, ScreenplayV2Workspace } from '../types.ts'
import {
  parseScreenplayRoute,
  screenplayNewPath,
  screenplayProjectPath,
} from './screenplayRoutes.ts'

test('screenplay routes distinguish project-level pages and reject child pages', () => {
  assert.deepEqual(parseScreenplayRoute('/screenplay'), { kind: 'projects' })
  assert.deepEqual(parseScreenplayRoute('/screenplay/new'), {
    kind: 'new',
    bookId: null,
  })
  assert.deepEqual(parseScreenplayRoute('/screenplay/new', '?bookId=book%201'), {
    kind: 'new',
    bookId: 'book 1',
  })
  assert.deepEqual(parseScreenplayRoute('/screenplay/projects/project%201'), {
    kind: 'project',
    projectId: 'project 1',
  })
  assert.deepEqual(parseScreenplayRoute('/screenplay/projects/project-1/documents'), {
    kind: 'invalid',
  })
  assert.equal(
    screenplayNewPath('book 1'),
    '/screenplay/new?bookId=book+1',
  )
  assert.equal(
    screenplayProjectPath('project 1'),
    '/screenplay/projects/project%201',
  )
})

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
    workflow: {
      stage: 'structure',
      heads: {},
      nextActions: [],
      review: {
        phase: 'awaitingReview',
        draftRevisionId: null,
        reviewRevisionId: null,
        recommendation: null,
        findings: [],
        counts: {
          total: 0,
          pending: 0,
          planned: 0,
          resolved: 0,
          dismissed: 0,
          riskAccepted: 0,
        },
        hardChecks: [],
        canFinalize: false,
        completionSource: null,
        nextAction: null,
      },
    },
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

test('project-document navigation keeps the clicked artifact role and Revision together', () => {
  assert.deepEqual(revisionLibraryTarget({
    role: 'sceneList',
    revisionId: 'revision-scenes-turn-3',
  }), {
    role: 'sceneList',
    revisionId: 'revision-scenes-turn-3',
  })
  assert.equal(revisionLibraryTarget(null), null)
})

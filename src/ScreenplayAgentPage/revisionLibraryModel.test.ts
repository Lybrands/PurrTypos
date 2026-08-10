import assert from 'node:assert/strict'
import test from 'node:test'
import type {
  ScreenplayV2RevisionDetail,
  ScreenplayV2RevisionPart,
  ScreenplayV2RevisionSummary,
  ScreenplayV2WorkingCopy,
  ScreenplayV2Workspace,
} from '../types.ts'
import {
  buildWorkingCopyContent,
  canApplyRevision,
  mergeRequestedRevision,
  revisionLibraryNavigationDecision,
  revisionLibraryWorkspacePresentation,
  reviewComparisonForPart,
  resolveHistoryRevisionId,
  resolveRevisionLibrarySelection,
  shouldShowRevisionDirectory,
  workingCopyDraftEquals,
  workingCopyEditorDraft,
} from './revisionLibraryModel.ts'

const episode7Part: ScreenplayV2RevisionPart = {
  type: 'episode',
  key: '7',
  position: 7,
  payload: { episodeNumber: 7 },
  contentText: '第 7 集正文',
  contentDigest: 'draft-part-digest',
}

function revisionDetail(
  id: string,
  role: 'screenplayDraft' | 'review',
  parts: ScreenplayV2RevisionPart[],
): ScreenplayV2RevisionDetail {
  return {
    id,
    projectId: 'project-1',
    deliverableId: `deliverable-${role}`,
    role,
    revisionNo: role === 'screenplayDraft' ? 6 : 4,
    parentRevisionId: null,
    contentDigest: `${id}-digest`,
    summary: { title: id },
    agentTaskId: null,
    schemaVersion: 2,
    createdBy: 'agent',
    inputRevisions: role === 'review' ? { screenplayDraft: 'draft-v6' } : {},
    parts,
    sources: [],
  }
}

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
  const originalParts = workingCopy.content.parts as Array<Record<string, unknown>>

  assert.equal(content.contentText, '编辑后的主文档')
  assert.equal(parts.length, 2)
  assert.equal(parts[0].key, '1')
  assert.equal(parts[0].position, 1)
  assert.equal(parts[1].contentText, '编辑后的第二集')
  assert.deepEqual(content.contentJson, workingCopy.content.contentJson)
  assert.deepEqual(parts[1].payload, originalParts[1].payload)
})

test('structured-only working copy content becomes editable Markdown', () => {
  const draft = workingCopyEditorDraft({
    ...workingCopy,
    content: {
      ...workingCopy.content,
      contentText: '',
      contentJson: {
        episodeCount: 2,
        premise: '一次意外唤醒了沉睡的能力',
      },
      parts: [{
        type: 'episode',
        key: '1',
        position: 1,
        payload: {
          episodeNumber: 1,
          scenes: [{
            id: 'scene-1',
            title: '清河桥异变',
            conflict: '林月无法辨认回家的路',
          }],
        },
        contentText: '',
      }],
    },
  } as ScreenplayV2WorkingCopy)

  assert.match(draft.mainText, /核心设想.*一次意外唤醒了沉睡的能力/)
  assert.doesNotMatch(draft.mainText, /集数/)
  assert.match(draft.partText[0], /清河桥异变/)
  assert.match(draft.partText[0], /林月无法辨认回家的路/)
  assert.doesNotMatch(draft.partText[0], /scene-1/)
})

test('review comparison requires the exact draft and reports four user states', () => {
  const mainPart: ScreenplayV2RevisionPart = {
    type: 'document',
    key: 'main',
    position: 0,
    payload: {},
    contentText: '文档概览',
    contentDigest: 'main-digest',
  }
  const draft = revisionDetail('draft-v6', 'screenplayDraft', [episode7Part, mainPart])
  const reviewPart = (payload: Record<string, unknown>): ScreenplayV2RevisionPart => ({
    type: 'episode',
    key: '7',
    position: 7,
    payload: { episodeNumber: 7, ...payload },
    contentText: '',
    contentDigest: 'review-part-digest',
  })
  const review = (
    reviewedDraftId: string,
    payload: Record<string, unknown>,
  ) => revisionDetail('review-v4', 'review', [
    reviewPart(payload),
    {
      ...mainPart,
      payload: { reviewedDraftId, inputContractVersion: 2 },
      contentText: '# 审阅概览',
    },
  ])

  assert.equal(shouldShowRevisionDirectory([mainPart]), false)
  assert.equal(shouldShowRevisionDirectory([episode7Part, mainPart]), true)
  assert.equal(reviewComparisonForPart(
    draft,
    review('draft-v5', { reviewStatus: 'completed', issues: [] }),
    episode7Part,
  ).kind, 'unavailable')
  assert.equal(reviewComparisonForPart(
    draft,
    review('draft-v6', {
      reviewStatus: 'failed',
      issues: [],
      failure: { episodeNumber: 7, message: '第 7 集审阅失败' },
    }),
    episode7Part,
  ).kind, 'failed')
  assert.equal(reviewComparisonForPart(
    draft,
    review('draft-v6', { reviewStatus: 'completed', issues: [] }),
    episode7Part,
  ).kind, 'clean')
  const issues = reviewComparisonForPart(
    draft,
    review('draft-v6', {
      reviewStatus: 'completed',
      issues: [{
        id: 'episode-7:pace',
        severity: 'major',
        description: '结尾转折需要更清晰。',
        sceneIds: ['ep07_s04'],
      }],
    }),
    episode7Part,
  )
  assert.equal(issues.kind, 'issues')
  assert.match('markdown' in issues ? issues.markdown : '', /结尾转折需要更清晰/)
  assert.doesNotMatch('markdown' in issues ? issues.markdown : '', /episode-7:pace/)
})

test('unverified legacy review is never exposed as a content finding', () => {
  const draft = revisionDetail('draft-v6', 'screenplayDraft', [episode7Part])
  const review = revisionDetail('review-v4', 'review', [
    {
      type: 'episode',
      key: '7',
      position: 7,
      payload: {
        episodeNumber: 7,
        reviewStatus: 'completed',
        issues: [{
          id: 'missing-tool',
          severity: 'critical',
          description: '当前环境未提供正文读取工具',
        }],
      },
      contentText: '当前环境未提供正文读取工具',
      contentDigest: 'legacy-review-part',
    },
    {
      type: 'document',
      key: 'main',
      position: 0,
      payload: { reviewedDraftId: 'draft-v6' },
      contentText: '# 审阅报告',
      contentDigest: 'legacy-review-main',
    },
  ])

  assert.deepEqual(reviewComparisonForPart(draft, review, episode7Part), {
    kind: 'failed',
    episodeNumber: 7,
    message: '当前审阅报告没有可验证的正文输入',
  })
})

test('inline editing derives dirty navigation and workspace presentation', () => {
  const saved = {
    mainText: '主文档',
    partText: ['第一集', '第二集'],
  }
  const changedEpisode = {
    mainText: '主文档',
    partText: ['第一集', '编辑后的第二集'],
  }

  assert.equal(workingCopyDraftEquals(saved, saved), true)
  assert.equal(workingCopyDraftEquals(saved, changedEpisode), false)
  assert.equal(revisionLibraryNavigationDecision({
    editing: true,
    dirty: true,
    busy: false,
  }), 'confirm')
  assert.equal(revisionLibraryNavigationDecision({
    editing: true,
    dirty: false,
    busy: false,
  }), 'continue')
  assert.equal(revisionLibraryNavigationDecision({
    editing: true,
    dirty: true,
    busy: true,
  }), 'block')
  assert.deepEqual(revisionLibraryWorkspacePresentation(false), {
    body: 'reader',
    actions: 'revision',
  })
  assert.deepEqual(revisionLibraryWorkspacePresentation(true), {
    body: 'inlineEditor',
    actions: 'workingCopy',
  })
})

test('only current-input non-head revisions can be applied directly', () => {
  assert.equal(canApplyRevision({ status: 'candidate', applicability: 'current' }), true)
  assert.equal(canApplyRevision({ status: 'historical', applicability: 'stale' }), false)
  assert.equal(canApplyRevision({ status: 'current', applicability: 'current' }), false)
})

test('an explicit artifact target overrides the project-document defaults', () => {
  const currentDraft = {
    id: 'revision-latest-draft',
    deliverableId: 'deliverable-draft',
    role: 'screenplayDraft' as const,
    revisionNo: 4,
    parentRevisionId: null,
    contentDigest: 'digest-draft',
    summary: { proposalKind: 'scene_draft', title: '当前剧本正文' },
    agentTaskId: 'task-draft',
    status: 'current' as const,
  }
  const workspace: ScreenplayV2Workspace = {
    project: {
      id: 'project-1',
      revision: 6,
      title: '定位测试项目',
      format: 'series',
      source: { type: 'original' },
      brief: { approach: '人物驱动', premise: '重新相遇' },
      lifecycle: 'active',
      stage: 'draft',
    },
    deliverables: [
      { id: 'deliverable-brief', role: 'creativeBrief', headRevisionId: null },
      { id: 'deliverable-scenes', role: 'sceneList', headRevisionId: null },
      {
        id: 'deliverable-draft',
        role: 'screenplayDraft',
        headRevisionId: currentDraft.id,
      },
    ],
    workflow: {
      stage: 'draft',
      heads: {
        screenplayDraft: currentDraft,
      },
      nextActions: [],
      review: {
        phase: 'awaitingReview',
        draftRevisionId: currentDraft.id,
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
    candidates: [],
    workingCopies: [],
  }

  assert.deepEqual(resolveRevisionLibrarySelection(workspace, {
    role: 'sceneList',
    revisionId: 'revision-scenes-turn-2',
  }), {
    role: 'sceneList',
    revisionId: 'revision-scenes-turn-2',
  })
  assert.deepEqual(resolveRevisionLibrarySelection(workspace, null), {
    role: 'screenplayDraft',
    revisionId: null,
  })
})

test('an explicit Revision remains selected when it is outside the first history page', () => {
  const firstPage = [{
    id: 'revision-scenes-12',
    deliverableId: 'deliverable-scenes',
    role: 'sceneList',
    revisionNo: 12,
    parentRevisionId: 'revision-scenes-11',
    contentDigest: 'digest-12',
    summary: { proposalKind: 'scene_list', title: '第 12 版场景规划' },
    agentTaskId: 'task-12',
    status: 'candidate',
  }] as ScreenplayV2RevisionSummary[]
  const requested = {
    id: 'revision-scenes-2',
    deliverableId: 'deliverable-scenes',
    role: 'sceneList',
    revisionNo: 2,
    parentRevisionId: 'revision-scenes-1',
    contentDigest: 'digest-2',
    summary: { proposalKind: 'scene_list', title: '第 2 版场景规划' },
    agentTaskId: 'task-2',
    status: 'historical',
  } as ScreenplayV2RevisionSummary

  assert.equal(resolveHistoryRevisionId(
    firstPage,
    'revision-scenes-12',
    requested.id,
  ), requested.id)
  assert.deepEqual(
    mergeRequestedRevision(firstPage, requested).map((item) => item.id),
    ['revision-scenes-2', 'revision-scenes-12'],
  )
  assert.deepEqual(
    mergeRequestedRevision(
      mergeRequestedRevision(firstPage, requested),
      requested,
    ).map((item) => item.id),
    ['revision-scenes-2', 'revision-scenes-12'],
  )
})

import type {
  ScreenplayV2DeliverableRole,
  ScreenplayV2RevisionDetail,
  ScreenplayV2RevisionPart,
  ScreenplayV2RevisionSummary,
  ScreenplayV2WorkingCopy,
  ScreenplayV2Workspace,
} from '../types'
import type { RevisionLibraryTarget } from './screenplayProjectModel.ts'
import { structuredContentToMarkdown } from './revisionDocumentView.ts'

export interface WorkingCopyEditorDraft {
  mainText: string
  partText: string[]
}

export function workingCopyDraftEquals(
  left: WorkingCopyEditorDraft,
  right: WorkingCopyEditorDraft,
): boolean {
  return left.mainText === right.mainText
    && left.partText.length === right.partText.length
    && left.partText.every((value, index) => value === right.partText[index])
}

export function revisionLibraryNavigationDecision(input: {
  editing: boolean
  dirty: boolean
  busy: boolean
}): 'continue' | 'confirm' | 'block' {
  if (input.busy) return 'block'
  return input.editing && input.dirty ? 'confirm' : 'continue'
}

export function revisionLibraryWorkspacePresentation(editing: boolean): {
  body: 'reader' | 'inlineEditor'
  actions: 'revision' | 'workingCopy'
} {
  return editing
    ? { body: 'inlineEditor', actions: 'workingCopy' }
    : { body: 'reader', actions: 'revision' }
}

export type ReviewComparison =
  | { kind: 'unavailable'; message: string }
  | { kind: 'failed'; message: string; episodeNumber: number }
  | { kind: 'clean'; markdown: string; reviewRevisionId: string }
  | { kind: 'issues'; markdown: string; reviewRevisionId: string }

export function shouldShowRevisionDirectory(
  parts: ReadonlyArray<ScreenplayV2RevisionPart>,
): boolean {
  return parts.length > 1
}

export function reviewComparisonForPart(
  draft: ScreenplayV2RevisionDetail,
  review: ScreenplayV2RevisionDetail | null,
  selectedPart: ScreenplayV2RevisionPart,
): ReviewComparison {
  if (!review) {
    return { kind: 'unavailable', message: '该版本尚无对应审阅' }
  }
  const reviewMain = review.parts.find(
    (part) => part.type === 'document' && part.key === 'main',
  )
  const reviewedDraftId = String(
    reviewMain?.payload.reviewedDraftId
    || review.inputRevisions.screenplayDraft
    || '',
  )
  if (reviewedDraftId !== draft.id) {
    return { kind: 'unavailable', message: '该版本尚无对应审阅' }
  }
  if (Number(reviewMain?.payload.inputContractVersion || 0) < 2) {
    return {
      kind: 'failed',
      episodeNumber: selectedPart.type === 'episode'
        ? Number(selectedPart.payload.episodeNumber || selectedPart.key)
        : 0,
      message: '当前审阅报告没有可验证的正文输入',
    }
  }
  if (selectedPart.type === 'document' && selectedPart.key === 'main') {
    const markdown = String(reviewMain?.contentText || '').trim()
    return markdown
      ? { kind: 'issues', markdown, reviewRevisionId: review.id }
      : {
          kind: 'clean',
          markdown: '本次审阅未发现需要处理的问题。',
          reviewRevisionId: review.id,
        }
  }
  const episodeNumber = Number(selectedPart.payload.episodeNumber || selectedPart.key)
  const reviewPart = review.parts.find((part) => (
    part.type === 'episode'
    && Number(part.payload.episodeNumber || part.key) === episodeNumber
  ))
  if (!reviewPart) {
    return { kind: 'unavailable', message: '该版本尚无对应审阅' }
  }
  if (reviewPart.payload.reviewStatus === 'failed') {
    const failure = isRecord(reviewPart.payload.failure)
      ? reviewPart.payload.failure
      : {}
    return {
      kind: 'failed',
      episodeNumber,
      message: String(failure.message || `第 ${episodeNumber} 集审阅失败`),
    }
  }
  const rawIssues = Array.isArray(reviewPart.payload.issues)
    ? reviewPart.payload.issues
    : []
  const issues = rawIssues.filter(isRecord)
  if (issues.length === 0) {
    return {
      kind: 'clean',
      markdown: '本集未发现需要处理的问题。',
      reviewRevisionId: review.id,
    }
  }
  const severityLabel: Record<string, string> = {
    critical: '严重',
    major: '主要',
    minor: '次要',
  }
  return {
    kind: 'issues',
    reviewRevisionId: review.id,
    markdown: issues.map((issue) => {
      const severity = severityLabel[String(issue.severity || '')] || '审阅意见'
      const description = String(issue.description || '').trim()
      const sceneIds = Array.isArray(issue.sceneIds)
        ? issue.sceneIds.map(String).filter(Boolean)
        : []
      return [
        `### ${severity}`,
        description,
        sceneIds.length > 0 ? `涉及场景：${sceneIds.join('、')}` : '',
      ].filter(Boolean).join('\n\n')
    }).join('\n\n'),
  }
}

export function defaultRevisionLibraryRole(
  workspace: ScreenplayV2Workspace,
): ScreenplayV2DeliverableRole {
  const roles = workspace.deliverables.map((item) => item.role)
  return [...roles].reverse().find((role) => (
    workspace.workflow.heads[role]
    || workspace.candidates.some((candidate) => candidate.role === role)
    || workspace.workingCopies.some((copy) => copy.role === role)
  )) ?? roles[0] ?? 'creativeBrief'
}

export function resolveRevisionLibrarySelection(
  workspace: ScreenplayV2Workspace,
  target: RevisionLibraryTarget | null,
): RevisionLibraryTarget | {
  role: ScreenplayV2DeliverableRole
  revisionId: null
} {
  return target ?? {
    role: defaultRevisionLibraryRole(workspace),
    revisionId: null,
  }
}

export function resolveHistoryRevisionId(
  items: ScreenplayV2RevisionSummary[],
  currentRevisionId: string | null,
  preferredRevisionId?: string | null,
): string | null {
  if (preferredRevisionId) return preferredRevisionId
  return items.some((item) => item.id === currentRevisionId)
    ? currentRevisionId
    : items[0]?.id ?? null
}

export function mergeRequestedRevision(
  items: ScreenplayV2RevisionSummary[],
  requested: ScreenplayV2RevisionSummary,
): ScreenplayV2RevisionSummary[] {
  return items.some((item) => item.id === requested.id)
    ? items
    : [requested, ...items]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function editableMarkdown(
  contentText: unknown,
  payload: Record<string, unknown>,
): string {
  const authoredText = String(contentText || '')
  return authoredText.trim()
    ? authoredText
    : structuredContentToMarkdown(payload)
}

export function workingCopyEditorDraft(
  workingCopy: ScreenplayV2WorkingCopy,
): WorkingCopyEditorDraft {
  const contentJson = isRecord(workingCopy.content.contentJson)
    ? workingCopy.content.contentJson
    : {}
  const parts = Array.isArray(workingCopy.content.parts)
    ? workingCopy.content.parts.filter(isRecord)
    : []
  return {
    mainText: editableMarkdown(workingCopy.content.contentText, contentJson),
    partText: parts.map((part) => editableMarkdown(
      part.contentText,
      isRecord(part.payload) ? part.payload : {},
    )),
  }
}

export function buildWorkingCopyContent(
  workingCopy: ScreenplayV2WorkingCopy,
  draft: WorkingCopyEditorDraft,
): Record<string, unknown> {
  const parts = Array.isArray(workingCopy.content.parts)
    ? workingCopy.content.parts.filter(isRecord)
    : []
  return {
    ...workingCopy.content,
    contentJson: isRecord(workingCopy.content.contentJson)
      ? workingCopy.content.contentJson
      : {},
    contentText: draft.mainText,
    parts: parts.map((part, index) => ({
      ...part,
      payload: isRecord(part.payload) ? part.payload : {},
      contentText: draft.partText[index] || '',
    })),
  }
}

export function canApplyRevision(
  revision: Pick<ScreenplayV2RevisionSummary, 'status' | 'applicability'>,
): boolean {
  return revision.status !== 'current' && revision.applicability !== 'stale'
}

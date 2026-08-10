import type {
  ScreenplayV2RevisionSummary,
  ScreenplayV2WorkingCopy,
} from '../types'
import { structuredContentToMarkdown } from './revisionDocumentView.ts'

export interface WorkingCopyEditorDraft {
  mainText: string
  partText: string[]
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

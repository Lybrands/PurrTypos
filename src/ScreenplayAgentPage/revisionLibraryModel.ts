import type {
  ScreenplayV2RevisionSummary,
  ScreenplayV2WorkingCopy,
} from '../types'

export interface WorkingCopyEditorDraft {
  mainJson: string
  mainText: string
  partJson: string[]
  partText: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function parseObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(value || '{}')
  } catch {
    throw new Error(`${label}的结构化内容不是有效 JSON`)
  }
  if (!isRecord(parsed)) {
    throw new Error(`${label}的结构化内容必须是 JSON 对象`)
  }
  return parsed
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
    mainJson: JSON.stringify(contentJson, null, 2),
    mainText: String(workingCopy.content.contentText || ''),
    partJson: parts.map((part) => JSON.stringify(
      isRecord(part.payload) ? part.payload : {},
      null,
      2,
    )),
    partText: parts.map((part) => String(part.contentText || '')),
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
    contentJson: parseObject(draft.mainJson, '主文档'),
    contentText: draft.mainText,
    parts: parts.map((part, index) => ({
      ...part,
      payload: parseObject(
        draft.partJson[index] || '{}',
        `Part ${String(part.key || index + 1)}`,
      ),
      contentText: draft.partText[index] || '',
    })),
  }
}

export function canApplyRevision(
  revision: Pick<ScreenplayV2RevisionSummary, 'status' | 'applicability'>,
): boolean {
  return revision.status !== 'current' && revision.applicability !== 'stale'
}

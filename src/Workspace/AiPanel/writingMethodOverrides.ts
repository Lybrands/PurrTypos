import type {
  BookWritingMethodBinding,
  WritingMethodOverrides,
  WritingMethodType,
} from '../../types'

export interface BoundWritingMethodChoice {
  revisionId: string
  name: string
  versionNo: number
  methodType: WritingMethodType
  source: 'method' | 'scheme'
}

export function boundWritingMethodChoices(
  bindings: BookWritingMethodBinding[],
): BoundWritingMethodChoice[] {
  const result: BoundWritingMethodChoice[] = []
  const seen = new Set<string>()
  for (const binding of [...bindings].sort((a, b) => a.priority - b.priority)) {
    const revision = binding.revision
    const members = 'members' in revision ? revision.members : [revision]
    for (const member of members) {
      const revisionId = 'method_revision_id' in member
        ? member.method_revision_id
        : member.id
      if (seen.has(revisionId)) continue
      seen.add(revisionId)
      result.push({
        revisionId,
        name: member.name,
        versionNo: member.version_no,
        methodType: member.method_type,
        source: binding.binding_type,
      })
    }
  }
  return result
}

export function cycleWritingMethodOverride(
  current: WritingMethodOverrides,
  revisionId: string,
): WritingMethodOverrides {
  const forced = new Set(current.forceRevisionIds)
  const excluded = new Set(current.excludeRevisionIds)
  if (forced.delete(revisionId)) {
    excluded.add(revisionId)
  } else if (excluded.delete(revisionId)) {
    // excluded -> default
  } else {
    forced.add(revisionId)
  }
  return {
    forceRevisionIds: [...forced],
    excludeRevisionIds: [...excluded],
  }
}

export function writingMethodOverrideMode(
  current: WritingMethodOverrides,
  revisionId: string,
): 'default' | 'force' | 'exclude' {
  if (current.forceRevisionIds.includes(revisionId)) return 'force'
  if (current.excludeRevisionIds.includes(revisionId)) return 'exclude'
  return 'default'
}

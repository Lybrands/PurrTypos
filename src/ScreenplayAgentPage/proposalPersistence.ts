import type {
  ScreenplayDocument,
  ScreenplayDocumentProposal,
} from '../types'

function proposalIdentity(
  content: Record<string, unknown>,
): { key: 'longTaskId' | 'artifactRef'; value: string } | null {
  for (const key of ['longTaskId', 'artifactRef'] as const) {
    const value = content[key]
    if (typeof value === 'string' && value.trim()) {
      return { key, value: value.trim() }
    }
  }
  return null
}

/**
 * Resolve the persisted document behind a proposal.
 *
 * The backend normalizes titles before storing them, while durable screenplay
 * tasks also carry their own stable identity. Matching only the raw title and
 * text made a successfully saved proposal appear unsaved after session/task
 * hydration replaced the local optimistic state.
 */
export function findPersistedProposalDocument(
  documents: ScreenplayDocument[],
  proposal: ScreenplayDocumentProposal | null | undefined,
): ScreenplayDocument | null {
  if (!proposal) return null
  const identity = proposalIdentity(proposal.contentJson)
  return [...documents].reverse().find((document) => {
    if (document.kind !== proposal.kind) return false
    if (identity) {
      const storedValue = document.content_json[identity.key]
      if (
        typeof storedValue === 'string'
        && storedValue.trim() === identity.value
      ) {
        return true
      }
    }
    return document.title.trim() === proposal.title.trim()
      && document.content_text === proposal.contentText
  }) ?? null
}

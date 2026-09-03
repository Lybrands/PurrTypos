import type {
  ScreenplayDocumentKind,
  ScreenplayDocument,
  ScreenplayDocumentEpisode,
  ScreenplayDraftEpisode,
  ScreenplayV2RevisionDetail,
} from '../types'

const PROPOSAL_KINDS = new Set<ScreenplayDocumentKind>([
  'source_analysis',
  'creative_brief',
  'beat_sheet',
  'episode_outline',
  'scene_list',
  'scene_draft',
  'review',
])

function revisionKind(revision: ScreenplayV2RevisionDetail): ScreenplayDocumentKind {
  const kind = String(revision.summary.proposalKind || '') as ScreenplayDocumentKind
  if (!PROPOSAL_KINDS.has(kind)) {
    throw new Error('Revision 缺少可识别的剧本产物类型')
  }
  return kind
}

function revisionTitle(revision: ScreenplayV2RevisionDetail): string {
  return String(
    revision.summary.title || `${revision.role} v${revision.revisionNo}`,
  )
}

export function documentFromRevision(
  revision: ScreenplayV2RevisionDetail,
): ScreenplayDocument {
  const main = revision.parts.find(
    (part) => part.type === 'document' && part.key === 'main',
  )
  if (!main) throw new Error('Revision 缺少主文档 Part')
  return {
    id: revision.id,
    project_id: revision.projectId,
    kind: revisionKind(revision),
    title: revisionTitle(revision),
    content_json: main.payload,
    content_text: main.contentText,
    version: revision.revisionNo,
    status: 'accepted',
    derived_from_ids: Object.values(revision.inputRevisions).filter(
      (id): id is string => typeof id === 'string',
    ),
    create_time: revision.createdAt || undefined,
    update_time: revision.createdAt || undefined,
  }
}

function positiveNumber(value: unknown, fallback: number): number {
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : fallback
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

function episodeItemIds(payload: Record<string, unknown>): string[] {
  const explicitItemIds = stringArray(payload.itemIds)
  if (explicitItemIds.length > 0) return explicitItemIds
  if (!Array.isArray(payload.scenes)) return []
  return payload.scenes.flatMap((scene) => (
    typeof scene === 'object'
    && scene != null
    && typeof (scene as Record<string, unknown>).id === 'string'
    && String((scene as Record<string, unknown>).id).trim()
      ? [String((scene as Record<string, unknown>).id)]
      : []
  ))
}

export function documentEpisodesFromRevision(
  revision: ScreenplayV2RevisionDetail,
): ScreenplayDocumentEpisode[] {
  const kind = revisionKind(revision)
  if (!['episode_outline', 'scene_list', 'review'].includes(kind)) return []
  return revision.parts
    .filter((part) => part.type === 'episode')
    .map((part, index) => {
      const episodeNumber = positiveNumber(
        part.payload.episodeNumber ?? part.payload.number ?? part.key,
        index + 1,
      )
      const itemIds = episodeItemIds(part.payload)
      return {
        id: `${revision.id}:episode:${episodeNumber}`,
        project_id: revision.projectId,
        document_id: revision.id,
        document_kind: kind as ScreenplayDocumentEpisode['document_kind'],
        episode_number: episodeNumber,
        title: String(part.payload.title || `第 ${episodeNumber} 集`),
        item_ids: itemIds,
        item_count: itemIds.length,
        content_json: part.payload,
        content_text: part.contentText,
        version: revision.revisionNo,
        status: 'accepted',
        storage_mode: 'revision_part',
        create_time: revision.createdAt || undefined,
        update_time: revision.createdAt || undefined,
      }
    })
}

export function draftEpisodesFromRevision(
  revision: ScreenplayV2RevisionDetail,
): ScreenplayDraftEpisode[] {
  if (revisionKind(revision) !== 'scene_draft') return []
  const main = documentFromRevision(revision)
  return revision.parts
    .filter((part) => part.type === 'episode')
    .map((part, index) => {
      const episodeNumber = positiveNumber(
        part.payload.episodeNumber ?? part.key,
        index + 1,
      )
      const sceneIds = stringArray(part.payload.sceneIds)
      const sceneExecutions = Array.isArray(part.payload.sceneExecutions)
        ? part.payload.sceneExecutions.filter(
            (item): item is Record<string, unknown> => typeof item === 'object' && item != null,
          )
        : []
      const sceneTexts = Array.isArray(part.payload.sceneTexts)
        ? part.payload.sceneTexts.filter(
            (item): item is { sceneId: string; contentText: string } => (
              typeof item === 'object'
              && item != null
              && typeof (item as Record<string, unknown>).sceneId === 'string'
              && typeof (item as Record<string, unknown>).contentText === 'string'
            ),
          )
        : []
      const continuity = String(part.payload.continuitySummary || '')
      return {
        id: `${revision.id}:episode:${episodeNumber}`,
        project_id: revision.projectId,
        draft_document_id: revision.id,
        scene_list_document_id: String(main.content_json.sceneListId || ''),
        episode_number: episodeNumber,
        title: String(part.payload.title || `第 ${episodeNumber} 集`),
        scene_ids: sceneIds,
        scene_count: sceneIds.length,
        scene_executions: sceneExecutions,
        scene_texts: sceneTexts,
        content_text: String(part.payload.contentText || part.contentText || ''),
        continuity_excerpt: continuity.slice(0, 240),
        continuity_summary: continuity,
        version: revision.revisionNo,
        status: 'accepted',
        storage_mode: 'revision_part',
        create_time: revision.createdAt || undefined,
        update_time: revision.createdAt || undefined,
      }
    })
}

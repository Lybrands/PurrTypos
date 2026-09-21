import { apiGet, apiPost, apiPut } from './httpClient'

export interface KnowledgeScope {
  purpose: 'prose' | 'discussion' | 'character'
  chapterId?: string
  characterId?: string
}
export interface KnowledgeStatus {
  sharedStorage?: boolean
  id?: string
  state: string
  directory?: string
  generation?: number
  version: number
  scanned_at?: string
  scope?: KnowledgeScope
  scan?: { markdown: number; skipped: Record<string, number>; states: Record<string, number> }
  semantic: { state: string; provider?: string; model?: string; jobs?: Array<{ state: string; count: number; calls: number; tokens: number | null }> }
}
export interface KnowledgeDocument {
  id: string
  path: string
  title: string
  revision: string | null
  state: string
  metadata: Record<string, unknown>
  diagnostics: string[]
}
export interface KnowledgeSource extends KnowledgeDocument {
  body: string
  usedRevision: string
  usedMetadata: Record<string, unknown>
  currentMatches: boolean
  links: Array<{ target: string; anchor: string; state: string }>
}
export interface KnowledgeLocator { heading?: string; line?: number; blocks?: string[] }
export interface KnowledgeSearch {
  state: string
  items: Array<{ id: string; documentId: string; title: string; revision: string; content: string; reasons: string[]; locator?: KnowledgeLocator }>
  excluded: Record<string, number>
  deferred: number
}
export interface KnowledgePreview {
  directory: string
  documents: Array<{ path: string; title: string; status: string }>
  skipped: Record<string, number>
}
const path = (book: string) => `/books/${encodeURIComponent(book)}/knowledge`
export interface KnowledgeUsage { eventId: number; runId: string; recordedAt: string; documentId: string; revision: string; title: string; scope: Record<string, unknown>; evidenceId: string; locator?: KnowledgeLocator }
export interface KnowledgeMaterialsStatus { mode: 'database' | 'markdown'; directory?: string; deleted?: Array<{ id: string; name: string }> }
export const novelKnowledge = {
  usage: (book: string) => apiGet<KnowledgeUsage[]>(`${path(book)}/usage`),
  status: (book: string) => apiGet<KnowledgeStatus>(`${path(book)}/binding`),
  preview: (book: string, selectionToken: string) => apiPost<KnowledgePreview>(`${path(book)}/binding/preview`, { selectionToken }),
  bind: (book: string, selectionToken: string, expectedVersion: number) => apiPost(`${path(book)}/binding`, { selectionToken, expectedVersion, commandId: crypto.randomUUID() }),
  configure: (book: string, expectedVersion: number, patch: { scope?: KnowledgeScope; semantic?: boolean; unbind?: boolean }) => apiPut(`${path(book)}/binding`, { ...patch, expectedVersion, commandId: crypto.randomUUID() }),
  refresh: (book: string) => apiPost<KnowledgeStatus>(`${path(book)}/index`, {}),
  semanticIndex: (book: string) => apiPost(`${path(book)}/index/semantic`, {}),
  documents: (book: string) => apiGet<KnowledgeDocument[]>(`${path(book)}/documents`),
  source: (book: string, document: string, revision?: string) => apiGet<KnowledgeSource>(`${path(book)}/documents/${encodeURIComponent(document)}${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`),
  search: (book: string, query: string, mode: 'fulltext' | 'hybrid') => apiPost<KnowledgeSearch>(`${path(book)}/search`, { query, mode }),
  materialsStatus: (book: string) => apiGet<KnowledgeMaterialsStatus>(`${path(book)}/materials`),
  restoreMaterial: (book: string, materialId: string) => apiPost<KnowledgeMaterialsStatus>(`${path(book)}/materials/trash/${encodeURIComponent(materialId)}/restore`, {}),
}

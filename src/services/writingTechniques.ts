import { apiDelete, apiGet, apiPost, apiPut, backendBaseUrl, requestJson } from './httpClient'

export type TechniqueKind = 'technique' | 'scheme'
export interface TechniqueRef { kind: TechniqueKind; id: string; versionId: string }
export interface WritingTechniqueSelection { mode: 'manual' | 'auto'; refs: TechniqueRef[] }
export interface WritingTechniqueChoice { ref: TechniqueRef; name: string; description: string; uploaded?: boolean }
export interface WritingTechniqueGrant { grantId: string; generation: number; ref: TechniqueRef; available?: boolean; metadata: {name: string; description: string} }
export interface TechniqueManifest {
  versionId: string
  files: Array<{ path: string; size: number; sha256: string }>
  metadata: { name: string; description: string; tags?: string[] } | null
}
export interface SchemeContent {
  schemaVersion: 1; name: string; description: string; composition: string; members: TechniqueRef[]
}
export interface SchemeBundle { format: string; scheme: SchemeContent; techniques: Array<{ref: TechniqueRef; files: Record<string, string>}> }
export interface TechniqueDraft {
  techniqueId?: string; schemeId?: string; draftId: string; draftRevision: number
  state: 'editing' | 'sealed' | 'cancelled'; treeDigest: string
  manifest?: TechniqueManifest; content?: SchemeContent; sealedRef?: TechniqueRef
  owner?: {analysisId?: string; sourceRevisionId?: string}
}
export interface TechniqueObject {
  kind: TechniqueKind; id: string; status: 'active' | 'archived'; publishedHead: string | null
  publishedVersions?: string[]; draftHead: string; draftIds: string[]
  metadata?: { name: string; description: string }; draft?: TechniqueDraft
}
export interface TechniqueFile { path: string; content: string; sha256: string; versionId: string }
export interface TechniqueDeletionPreview {
  kind: TechniqueKind; id: string; archived: boolean; canDelete: boolean
  draftCount: number; versionCount: number; revisionToken: string
  references: Array<{id: string; name: string; sources: string[]}>
}
export interface TechniqueFileChange { action: 'put' | 'delete' | 'move'; path: string; content?: string; target?: string }
export interface TechniqueUsage { ref: TechniqueRef; name: string; source: 'manual' | 'auto'; files: Array<{ref: TechniqueRef; path: string; sha256: string}> }

export const techniqueOperationId = () => crypto.randomUUID()
const base = '/writing-techniques'
const objectPath = (kind: TechniqueKind, id: string) => `${base}/objects/${kind}/${encodeURIComponent(id)}`

export const writingTechniques = {
  analysisLibrary: (analysisId: string) => apiGet<Array<{id: string; status: 'active' | 'archived'; name: string}>>(`${base}/analyses/${analysisId}/library`),
  deletionPreview: (object: TechniqueObject) => apiGet<TechniqueDeletionPreview>(`${objectPath(object.kind, object.id)}/deletion-preview`),
  deleteObject: (object: TechniqueObject, revisionToken: string, operationId: string) => apiPost<{deleted: boolean}>(`${objectPath(object.kind, object.id)}/delete`, {revisionToken, operationId}),
  getSelection: (kind: 'book' | 'session', id: string) => apiGet<TechniqueRef[]>(`${base}/selections/${kind}/${id}`),
  setSelection: (kind: 'book' | 'session', id: string, refs: TechniqueRef[]) => apiPut<TechniqueRef[]>(`${base}/selections/${kind}/${id}`, {refs}),
  analysisResults: (analysisId: string) => apiGet<Array<{ref: TechniqueRef; stage: string; available: boolean; name?: string; reason?: string}>>(`${base}/analyses/${analysisId}/results`),
  runUsage: (runId: string) => apiGet<TechniqueUsage[]>(`${base}/runs/${encodeURIComponent(runId)}/usage`),
  previewScheme: (file: File) => requestJson<SchemeBundle>(`/api${base}/scheme-import-preview`, {method: 'POST', body: file, headers: {'Content-Type': 'application/json'}}, 0),
  importScheme: (bundle: SchemeBundle, operationId: string) => apiPost<TechniqueDraft>(`${base}/scheme-import`, {bundle, operationId}),
  schemeVersion: (id: string, version: string) => apiGet<SchemeContent>(`${base}/schemes/${id}/versions/${version}`),
  exportSchemeUrl: (id: string, version: string) => `${backendBaseUrl}/api${base}/schemes/${id}/versions/${version}/export`,
  saveAnalysis: (analysisId: string, operationId: string) => apiPost<TechniqueDraft>(`${base}/analyses/${analysisId}/save`, {operationId}),
  upload: (bookId: string, sessionId: string, files: Record<string, string>, operationId: string) => apiPost<WritingTechniqueChoice>(`${base}/uploads`, { bookId, sessionId, files, operationId }),
  grants: (bookId: string) => apiGet<WritingTechniqueGrant[]>(`${base}/books/${bookId}/grants`),
  grant: (bookId: string, ref: TechniqueRef) => apiPost<WritingTechniqueGrant>(`${base}/books/${bookId}/grants`, ref),
  revoke: (bookId: string, grantId: string) => apiDelete<void>(`${base}/books/${bookId}/grants/${grantId}`),
  reserveInput: (bookId: string, sessionId: string, selection: WritingTechniqueSelection, operationId: string) => apiPost<{inputId: string; mode: 'manual' | 'auto'}>(`${base}/request-inputs`, { bookId, sessionId, mode: selection.mode, manual: selection.refs, operationId }),
  list: (kind: TechniqueKind, includeArchived = false) => apiGet<TechniqueObject[]>(`${base}/objects/${kind}?includeArchived=${includeArchived}`),
  get: (kind: TechniqueKind, id: string) => apiGet<TechniqueObject>(objectPath(kind, id)),
  createDraft: (operationId: string, techniqueId?: string, fromVersion?: TechniqueRef) =>
    apiPost<TechniqueDraft>(`${base}/drafts`, { operationId, techniqueId, fromVersion }),
  readDraftFile: (id: string, draft: TechniqueDraft, path: string) =>
    apiGet<TechniqueFile>(`${base}/${id}/drafts/${draft.draftId}/file?revision=${draft.draftRevision}&path=${encodeURIComponent(path)}`),
  readVersionFile: (id: string, version: string, path: string) => apiGet<TechniqueFile>(`${base}/${id}/versions/${version}/file?path=${encodeURIComponent(path)}`),
  versionManifest: (id: string, version: string) => apiGet<TechniqueManifest>(`${base}/${id}/versions/${version}/manifest`),
  changeFiles: (id: string, draft: TechniqueDraft, changes: TechniqueFileChange[], operationId: string) =>
    apiPut<TechniqueDraft>(`${base}/${id}/drafts/${draft.draftId}`, { operationId, expectedDraftRevision: draft.draftRevision, changes }),
  seal: (kind: TechniqueKind, id: string, draft: TechniqueDraft, operationId: string) =>
    apiPost<TechniqueDraft>(`${objectPath(kind, id)}/drafts/${draft.draftId}/seal`, { operationId, expectedDraftRevision: draft.draftRevision, expectedTreeDigest: draft.treeDigest }),
  publish: (object: TechniqueObject, ref: TechniqueRef, operationId: string) =>
    apiPost<TechniqueObject>(`${objectPath(object.kind, object.id)}/publish`, { operationId, ref, expectedPublishedHead: object.publishedHead }),
  setStatus: (object: TechniqueObject, status: 'active' | 'archived', operationId: string) =>
    apiPut<TechniqueObject>(`${objectPath(object.kind, object.id)}/status`, { operationId, status }),
  createScheme: (content: SchemeContent, operationId: string, schemeId?: string) =>
    apiPost<TechniqueDraft>(`${base}/scheme-drafts`, { operationId, schemeId, content }),
  updateScheme: (id: string, draft: TechniqueDraft, content: SchemeContent, operationId: string) =>
    apiPut<TechniqueDraft>(`${base}/schemes/${id}/drafts/${draft.draftId}`, { operationId, expectedDraftRevision: draft.draftRevision, content }),
  importFiles: (files: Record<string, string>, operationId: string, techniqueId?: string) =>
    apiPost<TechniqueDraft>(`${base}/import`, { files, operationId, techniqueId }),
  previewUpload: (file: File) => requestJson<{ files: Record<string, string>; entryRenamed: boolean }>(
    `/api${base}/import-preview?filename=${encodeURIComponent(file.name)}`,
    { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } }, 0),
  exportUrl: (id: string, version: string, format: 'zip' | 'markdown' = 'zip') =>
    `${backendBaseUrl}/api${base}/${id}/versions/${version}/export?format=${format}`,
  getMode: (kind: 'book' | 'session', id: string) => apiGet<'manual' | 'auto'>(`${base}/modes/${kind}/${id}`),
  setMode: (kind: 'book' | 'session', id: string, mode: 'manual' | 'auto') => apiPut<{mode: 'manual' | 'auto'}>(`${base}/modes/${kind}/${id}`, { mode }),
}

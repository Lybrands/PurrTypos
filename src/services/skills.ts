import { apiGet, apiPost, apiPut, backendBaseUrl, requestJson } from './httpClient'

export interface SkillRef { kind: 'skill'; id: string; versionId: string }
export interface SkillMetadata {
  name: string
  description: string
  tags?: string[]
  autoUse?: boolean
  retrieval?: Record<string, string[]>
}
export interface SkillObject {
  kind: 'skill'; id: string; status: 'active' | 'archived'; publishedHead: string | null
  origin?: 'builtin'
  publishedVersions?: string[]; draftHead: string; draftIds: string[]
  metadata?: SkillMetadata
}
export interface SkillManifest {
  versionId: string
  files: Array<{ path: string; size: number; sha256: string }>
  metadata: SkillMetadata | null
}
export interface SkillFile { path: string; content: string; sha256: string; versionId: string }
export interface SkillDeletionPreview {
  id: string; archived: boolean; canDelete: boolean
  versionCount: number; revisionToken: string
}

export const skillOperationId = () => crypto.randomUUID()
const base = '/skills'
const objectPath = (id: string) => `${base}/objects/${encodeURIComponent(id)}`

export const skills = {
  list: (includeArchived = false) => apiGet<SkillObject[]>(`${base}/objects?includeArchived=${includeArchived}`),
  get: (id: string) => apiGet<SkillObject>(objectPath(id)),
  setStatus: (id: string, status: 'active' | 'archived', operationId: string) =>
    apiPut<SkillObject>(`${objectPath(id)}/status`, { operationId, status }),
  deletionPreview: (id: string) => apiGet<SkillDeletionPreview>(`${objectPath(id)}/deletion-preview`),
  deleteObject: (id: string, revisionToken: string, operationId: string) =>
    apiPost<{ deleted: boolean }>(`${objectPath(id)}/delete`, { revisionToken, operationId }),
  versionManifest: (id: string, version: string) => apiGet<SkillManifest>(`${base}/${id}/versions/${version}/manifest`),
  readVersionFile: (id: string, version: string, path: string) =>
    apiGet<SkillFile>(`${base}/${id}/versions/${version}/file?path=${encodeURIComponent(path)}`),
  previewInstall: (file: File) => requestJson<{ files: Record<string, string>; entryRenamed: boolean }>(
    `/api${base}/import-preview?filename=${encodeURIComponent(file.name)}`,
    { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } }, 0),
  install: (files: Record<string, string>, operationId: string) =>
    apiPost<{ skillId: string; ref: SkillRef; metadata: SkillMetadata; autoUse: boolean }>(`${base}/import`, { files, operationId }),
  exportUrl: (id: string, version: string, format: 'zip' | 'markdown' = 'zip') =>
    `${backendBaseUrl}/api${base}/${id}/versions/${version}/export?format=${format}`,
}

import { apiDelete, apiGet, apiPost, apiPatch } from './httpClient'
export interface AnalysisSession { id: string; title: string; closed?: boolean; createdAt?: string }
const path = (revision: string) => `/novel-source-revisions/${encodeURIComponent(revision)}/conversations`
export const analysisSessions = {
  list: (revision: string) => apiGet<AnalysisSession[]>(path(revision)),
  create: (revision: string) => apiPost<AnalysisSession>(path(revision), {}),
  update: (revision: string, id: string, patch: {title?: string; closed?: boolean}) => apiPatch(`${path(revision)}/${encodeURIComponent(id)}`, patch),
  delete: (revision: string, id: string) => apiDelete<void>(`${path(revision)}/${encodeURIComponent(id)}`),
}

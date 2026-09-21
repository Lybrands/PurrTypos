import { apiGet } from './httpClient'
export interface SourceSectionNode {
  nodeKind: 'source_section'; sectionId: string; title: string; ordinal: number; sectionType: string
  readOnly: true; locator: { volumeId?: string; volumeTitle?: string }
}
export const continuationHistory = {
  list: (bookId: string, offset = 0) => apiGet<{items: SourceSectionNode[]; total: number; chapterCount: number; nextOffset: number | null}>(`/continuations/${bookId}/sections?offset=${offset}`),
  read: (bookId: string, sectionId: string) => apiGet<{title: string; text: string}>(`/continuations/${bookId}/sections/${sectionId}`),
  export: (bookId: string, includeHistory: boolean) => apiGet<{entries: Array<{title: string; text: string}>}>(`/continuations/${bookId}/export?includeHistory=${includeHistory}`),
}

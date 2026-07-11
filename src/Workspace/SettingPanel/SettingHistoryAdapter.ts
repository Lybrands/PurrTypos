import type {
  ApiResult,
  CharacterSettingHistory,
  EntityId,
  SettingEntityHistory,
  StoryBackgroundSettingHistory,
} from '../../types'

export type HistoryKind = 'character' | 'background' | 'entity'
export type HistoryStorageKey = 'profile' | 'background'

export interface HistoryTarget {
  characterId?: number | null
  settingEntityId?: number | null
  bookId?: EntityId | null
}

export interface HistoryViewModel {
  id: number
  source: string
  acceptedSegments: number
  rejectedSegments: number
  createTime?: string
  beforeContent: string
  previewContent: string
}

interface HistoryListResult {
  success: boolean
  data: HistoryViewModel[]
}

export interface HistoryActionResult {
  success: boolean
  error?: string
}

export interface SettingHistoryAdapter {
  storageKey: HistoryStorageKey
  title: string
  loadErrorMessage: string
  restoreTooltip?: string
  list: (target: HistoryTarget) => Promise<HistoryListResult | null>
  getDetail: (historyId: number) => Promise<HistoryViewModel | null>
  rollback: (historyId: number) => Promise<HistoryActionResult>
}

type ProfileHistory = CharacterSettingHistory | SettingEntityHistory

const HISTORY_LIMIT = 100

function profileHistoryViewModel(item: ProfileHistory): HistoryViewModel {
  return {
    id: item.id,
    source: item.source,
    acceptedSegments: item.accepted_segments,
    rejectedSegments: item.rejected_segments,
    createTime: item.create_time,
    beforeContent: item.before_profile_md,
    previewContent: item.before_profile_md || item.after_profile_md,
  }
}

function backgroundHistoryViewModel(item: StoryBackgroundSettingHistory): HistoryViewModel {
  return {
    id: item.id,
    source: item.source,
    acceptedSegments: item.accepted_segments,
    rejectedSegments: item.rejected_segments,
    createTime: item.create_time,
    beforeContent: item.before_content,
    previewContent: item.before_content || item.after_content,
  }
}

function mapHistoryList<T>(
  result: ApiResult<T[]> | null | undefined,
  toViewModel: (item: T) => HistoryViewModel,
): HistoryListResult {
  return {
    success: !!result?.success,
    data: result?.success ? (result.data ?? []).map(toViewModel) : [],
  }
}

function mapHistoryDetail<T>(
  result: ApiResult<T | null> | null | undefined,
  toViewModel: (item: T) => HistoryViewModel,
): HistoryViewModel | null {
  return result?.success && result.data ? toViewModel(result.data) : null
}

function mapActionResult(result: ApiResult<unknown> | null | undefined): HistoryActionResult {
  return { success: !!result?.success, error: result?.error }
}

const ADAPTERS: Record<HistoryKind, SettingHistoryAdapter> = {
  character: {
    storageKey: 'profile',
    title: '人物历史',
    loadErrorMessage: '加载人物历史失败',
    restoreTooltip: '把当前设定替换为此版本',
    async list(target) {
      if (target.characterId == null) return null
      const result = await window.electronAPI.listCharacterSettingHistory({
        characterId: target.characterId,
        limit: HISTORY_LIMIT,
      })
      return mapHistoryList(result, profileHistoryViewModel)
    },
    async getDetail(historyId) {
      const result = await window.electronAPI.getCharacterSettingHistory({ historyId })
      return mapHistoryDetail(result, profileHistoryViewModel)
    },
    async rollback(historyId) {
      const result = await window.electronAPI.rollbackCharacterSettingHistory({ historyId })
      return mapActionResult(result)
    },
  },
  entity: {
    storageKey: 'profile',
    title: '设定历史',
    loadErrorMessage: '加载设定历史失败',
    restoreTooltip: '把当前设定替换为此版本',
    async list(target) {
      if (target.settingEntityId == null) return null
      const result = await window.electronAPI.listEntitySettingHistory({
        entityId: target.settingEntityId,
        limit: HISTORY_LIMIT,
      })
      return mapHistoryList(result, profileHistoryViewModel)
    },
    async getDetail(historyId) {
      const result = await window.electronAPI.getEntitySettingHistory({ historyId })
      return mapHistoryDetail(result, profileHistoryViewModel)
    },
    async rollback(historyId) {
      const result = await window.electronAPI.rollbackEntitySettingHistory({ historyId })
      return mapActionResult(result)
    },
  },
  background: {
    storageKey: 'background',
    title: '背景历史',
    loadErrorMessage: '加载背景历史失败',
    async list(target) {
      if (target.bookId == null) return null
      const result = await window.electronAPI.listBackgroundSettingHistory({
        bookId: target.bookId,
        limit: HISTORY_LIMIT,
      })
      return mapHistoryList(result, backgroundHistoryViewModel)
    },
    async getDetail(historyId) {
      const result = await window.electronAPI.getBackgroundSettingHistory({ historyId })
      return mapHistoryDetail(result, backgroundHistoryViewModel)
    },
    async rollback(historyId) {
      const result = await window.electronAPI.rollbackBackgroundSettingHistory({ historyId })
      return mapActionResult(result)
    },
  },
}

export function getSettingHistoryAdapter(kind: HistoryKind): SettingHistoryAdapter {
  return ADAPTERS[kind]
}

import type {
  ElectronAPI,
  ScreenplayCancelOperationReceipt,
  ScreenplayAgentChunkPage,
  ScreenplayConversationEvent,
  ScreenplayConversationTurn,
} from '../types'
import {
  stateFromScreenplayConversationSnapshot,
  type ScreenplayConversationState,
} from './conversationState.ts'

type NativeConversationApi = Pick<ElectronAPI,
  | 'submitScreenplayConversationTurn'
  | 'getScreenplayConversationSnapshot'
  | 'listScreenplayConversationEvents'
  | 'watchScreenplayConversationEvents'
  | 'cancelScreenplayConversationTurn'
  | 'truncateScreenplayConversationFromTurn'
>

type SubmitInput = Parameters<
  ElectronAPI['submitScreenplayConversationTurn']
>[0]

function dataOrThrow<T>(
  result: { success: boolean; data?: T; error?: string },
  fallback: string,
): T {
  if (!result.success || result.data == null) {
    throw new Error(result.error || fallback)
  }
  return result.data
}

/**
 * Product-owned client for the native screenplay Conversation API.
 *
 * Cursor events are invalidation notices; the persisted Snapshot is always the
 * canonical UI source. This client deliberately has no dependency on the
 * Writing chat stream, chunk reducer, or renderer-local conversation store.
 */
export class ScreenplayConversationClient {
  private readonly api: NativeConversationApi

  constructor(api: NativeConversationApi) {
    this.api = api
  }

  async load(projectId: string, sessionId: number): Promise<ScreenplayConversationState> {
    const result = await this.api.getScreenplayConversationSnapshot({
      projectId,
      sessionId,
    })
    return stateFromScreenplayConversationSnapshot(dataOrThrow(
      result,
      '读取剧本对话失败',
    ))
  }

  async submit(input: SubmitInput): Promise<ScreenplayConversationTurn> {
    return dataOrThrow(
      await this.api.submitScreenplayConversationTurn(input),
      '提交剧本对话失败',
    )
  }

  async refresh(
    state: ScreenplayConversationState,
  ): Promise<ScreenplayConversationState> {
    let after = state.cursor
    while (true) {
      const page = dataOrThrow(
        await this.api.listScreenplayConversationEvents({
          projectId: state.projectId,
          sessionId: state.sessionId,
          after,
          limit: 100,
        }),
        '读取剧本对话事件失败',
      )
      if (page.hasMore && page.nextCursor <= after) {
        throw new Error('剧本对话事件游标没有前进')
      }
      after = page.nextCursor
      if (!page.hasMore) break
    }
    return after > state.cursor
      ? this.load(state.projectId, state.sessionId)
      : state
  }

  watch(
    state: ScreenplayConversationState,
    options: {
      chunkAfter: number
      onInvalidate: (event: ScreenplayConversationEvent) => void
      onChunks: (page: ScreenplayAgentChunkPage) => void
    },
  ): () => void {
    let cursor = state.cursor
    let chunkCursor = Math.max(0, options.chunkAfter)
    return this.api.watchScreenplayConversationEvents({
      projectId: state.projectId,
      sessionId: state.sessionId,
      after: cursor,
      chunkAfter: chunkCursor,
      onEvent: (event) => {
        if ('kind' in event) {
          if (event.nextCursor <= chunkCursor) return
          chunkCursor = event.nextCursor
          options.onChunks(event)
          return
        }
        if (event.cursor <= cursor) return
        cursor = event.cursor
        options.onInvalidate(event)
      },
    })
  }

  async cancel(commandId: string, turnId: string): Promise<ScreenplayCancelOperationReceipt> {
    return dataOrThrow(
      await this.api.cancelScreenplayConversationTurn({ commandId, turnId }),
      '终止剧本对话失败',
    )
  }

  async truncateFromTurn(turnId: string): Promise<void> {
    dataOrThrow(
      await this.api.truncateScreenplayConversationFromTurn({ turnId }),
      '更新剧本对话历史失败',
    )
  }
}

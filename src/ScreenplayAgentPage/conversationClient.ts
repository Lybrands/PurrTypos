import type {
  ElectronAPI,
  ScreenplayConversationRuntimeInput,
  ScreenplayConversationTurn,
} from '../types'
import {
  conversationPageRequiresSnapshot,
  stateFromScreenplayConversationSnapshot,
  type ScreenplayConversationState,
} from './conversationState.ts'

type NativeConversationApi = Pick<ElectronAPI,
  | 'submitScreenplayConversationTurn'
  | 'getScreenplayConversationSnapshot'
  | 'listScreenplayConversationEvents'
  | 'cancelScreenplayConversationTurn'
  | 'resumeScreenplayConversationTurn'
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
    let changed = false
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
      changed ||= conversationPageRequiresSnapshot(state, page)
      if (page.hasMore && page.nextCursor <= after) {
        throw new Error('剧本对话事件游标没有前进')
      }
      after = page.nextCursor
      if (!page.hasMore) break
    }
    return changed ? this.load(state.projectId, state.sessionId) : state
  }

  async cancel(commandId: string, turnId: string): Promise<ScreenplayConversationTurn> {
    return dataOrThrow(
      await this.api.cancelScreenplayConversationTurn({ commandId, turnId }),
      '终止剧本对话失败',
    )
  }

  async resume(
    commandId: string,
    turnId: string,
    runtime: ScreenplayConversationRuntimeInput,
  ): Promise<ScreenplayConversationTurn> {
    return dataOrThrow(
      await this.api.resumeScreenplayConversationTurn({
        commandId,
        turnId,
        runtime,
      }),
      '恢复剧本对话失败',
    )
  }
}

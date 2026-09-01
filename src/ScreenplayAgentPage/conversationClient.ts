import type {
  ElectronAPI,
  ScreenplayCancelOperationReceipt,
  ScreenplayAgentChunkPage,
  ScreenplayConversationTurn,
  ScreenplayConversationRuntimeInput,
  ScreenplayResumeOperationReceipt,
} from '../types'
import {
  stateFromScreenplayConversationSnapshot,
  type ScreenplayConversationState,
} from './conversationState.ts'

type NativeConversationApi = Pick<ElectronAPI,
  | 'submitScreenplayConversationTurn'
  | 'getScreenplayConversationSnapshot'
  | 'watchScreenplayConversationEvents'
  | 'cancelScreenplayConversationTurn'
  | 'resumeScreenplayConversationOperation'
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
 * Canonical chunks update the conversation incrementally. Only a changed
 * product projection fingerprint invalidates the business Snapshot. No dependency on the
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
    return this.load(state.projectId, state.sessionId)
  }

  watch(
    state: ScreenplayConversationState,
    options: {
      chunkAfter: number
      onInvalidate: () => void
      onChunks: (page: ScreenplayAgentChunkPage) => void
      onError?: (error: Error) => void
    },
  ): () => void {
    let chunkCursor = Math.max(0, options.chunkAfter)
    let receivedPage = false
    let projectionVersion: string | undefined
    return this.api.watchScreenplayConversationEvents({
      projectId: state.projectId,
      sessionId: state.sessionId,
      chunkAfter: chunkCursor,
      onError: options.onError,
      onEvent: (event) => {
        const changed = event.projectionVersion !== projectionVersion
        if (receivedPage && event.nextCursor <= chunkCursor && !changed) return
        receivedPage = true
        chunkCursor = event.nextCursor
        options.onChunks(event)
        projectionVersion = event.projectionVersion
        if (changed) options.onInvalidate()
      },
    })
  }

  async cancel(commandId: string, turnId: string): Promise<ScreenplayCancelOperationReceipt> {
    return dataOrThrow(
      await this.api.cancelScreenplayConversationTurn({ commandId, turnId }),
      '终止剧本对话失败',
    )
  }

  async resume(
    commandId: string,
    operationId: string,
    expectedOperationRevision: number,
    runtime: ScreenplayConversationRuntimeInput,
  ): Promise<ScreenplayResumeOperationReceipt> {
    return dataOrThrow(
      await this.api.resumeScreenplayConversationOperation({
        commandId,
        operationId,
        expectedOperationRevision,
        runtime,
      }),
      '继续执行剧本任务失败',
    )
  }

  async truncateFromTurn(turnId: string): Promise<void> {
    dataOrThrow(
      await this.api.truncateScreenplayConversationFromTurn({ turnId }),
      '更新剧本对话历史失败',
    )
  }
}

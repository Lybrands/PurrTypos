import type { AiApiProvider, AiModelConfig, ApiResult } from '../../types'
import { buildStreamOptions, type StreamRequestOptions } from '../../agent-runtime/streamOptions'
import { normalizeApiProvider } from '../../modelCatalog'

export interface SessionTitleRequest {
  apiKey: string
  baseURL?: string
  prompt: string
  apiProvider?: AiApiProvider
  model?: string
  options?: StreamRequestOptions
}

/**
 * 会话是否尚未命名：标题为空或仍是默认的「新对话」。
 * 各对话面板（写作/剧本/来源分析）共用的待命名判定。
 */
export function isUntitledSessionTitle(title?: string | null): boolean {
  const normalized = (title ?? '').trim()
  return normalized === '' || normalized === '新对话'
}

/**
 * 对话业务组件共用的会话自动命名：按首条输入消息调用标题生成接口
 * （生成 10 字内短标题），成功后先持久化再回调更新本地列表。
 * 不抛异常：失败只记日志，会话保持「新对话」不影响使用。
 * 标题生成与持久化接口由各面板注入，本组件不直接依赖服务层。
 */
export function generateSessionTitleFromText(params: {
  /** 命名依据的输入文本（首条用户消息或等效任务描述） */
  userText: string
  model: AiModelConfig
  /** 标题生成接口（如 services.ai.generateSessionTitle） */
  requestTitle: (request: SessionTitleRequest) => Promise<ApiResult<string>>
  /** 持久化标题（写入后端）；成功后才会回调 onTitle */
  persistTitle: (title: string) => Promise<unknown>
  /** 持久化成功后更新本地会话列表 */
  onTitle: (title: string) => void
  /** 日志前缀，如「剧本 Agent」「来源分析」 */
  logLabel?: string
}): void {
  const titleSource = params.userText.trim()
  if (!params.model.apiKey?.trim() || !titleSource) return
  const { options, apiModelName } = buildStreamOptions({
    cfg: params.model,
    selectedModel: params.model.id,
  })
  void params.requestTitle({
    apiKey: params.model.apiKey,
    baseURL: params.model.baseUrl || undefined,
    prompt: titleSource,
    apiProvider: normalizeApiProvider(params.model.apiProvider),
    model: apiModelName,
    options,
  }).then(async (result) => {
    if (!result.success || !result.data?.trim()) return
    const title = result.data.trim()
    await params.persistTitle(title)
    params.onTitle(title)
  }).catch((error: unknown) => {
    console.warn(`[${params.logLabel ?? '会话命名'}] 标题生成请求异常：`, error)
  })
}

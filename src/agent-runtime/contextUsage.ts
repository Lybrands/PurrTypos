import type { AiContextBudgetState } from '../types.ts'
import type { AgentConversationMessage } from './contracts.ts'

export type ContextUsageSource = 'provider' | 'estimate'

export interface ContextUsage {
  usedTokens: number;
  windowTokens: number;
  inputCapacityTokens: number;
  outputReserveTokens: number;
  ratio: number;
  /** provider 表示估算基线已由最近一次供应商输入量校准。 */
  source: ContextUsageSource;
}

/**
 * 上下文用量的单一口径：
 *
 * 1. 锚点 = 最近一条带预算快照的消息，基线取供应商实测输入量
 *    （actualInputTokens），否则取本轮编译输入的本地估算
 *    （estimatedInputTokens + toolSchemaTokens，与供应商实测同口径）。
 * 2. 锚点之后的新消息用本地薄估算外推，下一轮运行会重新锚定。
 * 3. 只有估算基线、且更早轮次出现过同对话的实测时，用实测/估算比
 *    校准本轮估算（比例落在信任区间 [0.5, 2] 内才采用，区间外的
 *    样本视为异常直接丢弃、退回原始估算），使相邻两轮的数字可比、
 *    不随估算口径来回跳。
 * 4. 首轮运行之前没有任何快照，才退回全程薄估算。
 *
 * 曾经的 max(全程薄估算, 快照基线) 已移除：那是两种不可比口径的
 * 混合，会让指示器在「用户看到的对话长度」与「模型实际收到的输入」
 * 两个口径间来回切换。快照口径与预算/压缩/溢出判断同源，是唯一
 * 与「会不会超窗」一致的数字。
 */

/** 实测/估算比落在该区间内才视为可信校准样本；区间外丢弃。 */
const CALIBRATION_FACTOR_MIN = 0.5
const CALIBRATION_FACTOR_MAX = 2.0

function nonNegativeInteger(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? Math.round(number) : null
}

function normalizeIdentity(value: unknown): string {
  return String(value ?? '').trim().toLowerCase()
}

function matchesSelectedModel(params: {
  message: AgentConversationMessage;
  budget: AiContextBudgetState;
  modelConfigId?: string;
  modelName?: string;
}): boolean {
  const expectedConfigId = normalizeIdentity(params.modelConfigId)
  const budgetConfigId = normalizeIdentity(params.budget.modelConfigId)
  if (expectedConfigId && budgetConfigId && expectedConfigId !== budgetConfigId) return false
  const expectedModelName = normalizeIdentity(params.modelName)
  const budgetModelName = normalizeIdentity(params.budget.modelName || params.message.model)
  if (expectedModelName && budgetModelName && expectedModelName !== budgetModelName) return false
  if (expectedConfigId && !budgetConfigId && !budgetModelName) return false
  if (expectedModelName && !budgetModelName && !budgetConfigId) return false
  return true
}

function estimateUnits(value: string, asciiDivisor: number): number {
  let asciiCount = 0
  let nonAsciiCount = 0
  for (const character of value) {
    if (character.codePointAt(0)! < 128) asciiCount += 1
    else nonAsciiCount += 1
  }
  return nonAsciiCount + Math.ceil(asciiCount / Math.max(1, asciiDivisor))
}

function estimateMessageTokens(
  message: Pick<AgentConversationMessage, 'role' | 'content' | 'streamingContent'>,
): number {
  const content = String(message.content || message.streamingContent || '').trim()
  if (!content) return 0
  return estimateUnits(JSON.stringify({ role: message.role, content }), 2) + 4
}

function estimateConversationTokens(messages: AgentConversationMessage[]): number {
  return messages.reduce((total, message) => total + estimateMessageTokens(message), 0)
}

interface BudgetAnchor {
  index: number;
  message: AgentConversationMessage;
  budget: AiContextBudgetState;
  /** 实测输入量；无实测时为 null。 */
  actualTokens: number | null;
  /** 估算输入量 + 工具 schema（与实测同口径）；无估算时为 null。 */
  estimatedBaseTokens: number | null;
}

/**
 * 倒序扫描：锚点 = 最近一个可用的预算快照；校准源 = 锚点之前最近一个
 * 同时带实测与估算的快照（同一次运行的实测/估算比即估算器偏差）。
 */
function scanBudgetSnapshots(messages: AgentConversationMessage[]): {
  anchor: BudgetAnchor | null;
  calibration: { actualTokens: number; estimatedBaseTokens: number } | null;
} {
  let anchor: (BudgetAnchor & { message: AgentConversationMessage; budget: AiContextBudgetState }) | null = null
  let calibration: { actualTokens: number; estimatedBaseTokens: number } | null = null
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    const budget = message.contextBudget
    if (!budget) continue
    const actualTokens = nonNegativeInteger(budget.actualInputTokens)
    const estimatedInputTokens = nonNegativeInteger(budget.estimatedInputTokens)
    const toolSchemaTokens = nonNegativeInteger(budget.toolSchemaTokens) ?? 0
    const estimatedBaseTokens = estimatedInputTokens === null
      ? null
      : estimatedInputTokens + toolSchemaTokens
    if (anchor === null) {
      if (actualTokens === null && estimatedBaseTokens === null) continue
      anchor = { index, message, budget, actualTokens, estimatedBaseTokens }
      if (actualTokens !== null) break // 实测锚点无需校准
      continue
    }
    if (calibration === null && actualTokens !== null && estimatedBaseTokens !== null && estimatedBaseTokens > 0) {
      calibration = { actualTokens, estimatedBaseTokens }
      break
    }
  }
  return { anchor, calibration }
}

export function calculateContextUsage(params: {
  messages: AgentConversationMessage[];
  windowTokens: number;
  modelConfigId?: string;
  modelName?: string;
}): ContextUsage {
  const windowTokens = Math.max(1, Math.round(params.windowTokens))
  const { anchor, calibration } = scanBudgetSnapshots(params.messages)
  const selectedModelMatchesSnapshot = anchor !== null && matchesSelectedModel({
    message: anchor.message,
    budget: anchor.budget,
    modelConfigId: params.modelConfigId,
    modelName: params.modelName,
  })
  const snapshotWindowTokens = anchor ? nonNegativeInteger(anchor.budget.windowTokens) : null
  const canReuseOutputReserve = selectedModelMatchesSnapshot && snapshotWindowTokens === windowTokens
  const outputReserveTokens = canReuseOutputReserve
    ? Math.min(windowTokens - 1, nonNegativeInteger(anchor?.budget.outputReserveTokens) ?? 0)
    : 0
  const inputCapacityTokens = Math.max(1, windowTokens - outputReserveTokens)

  let usedTokens: number
  let providerCalibrated: boolean
  if (anchor === null) {
    // 首轮运行之前：唯一的可用口径是本地薄估算。
    usedTokens = estimateConversationTokens(params.messages)
    providerCalibrated = false
  } else {
    providerCalibrated = anchor.actualTokens !== null && selectedModelMatchesSnapshot
    if (anchor.actualTokens !== null) {
      usedTokens = anchor.actualTokens
    } else {
      const estimatedBase = anchor.estimatedBaseTokens ?? 0
      const calibrationFactor = calibration
        ? calibration.actualTokens / calibration.estimatedBaseTokens
        : null
      const trustedCalibration = calibrationFactor !== null
        && calibrationFactor >= CALIBRATION_FACTOR_MIN
        && calibrationFactor <= CALIBRATION_FACTOR_MAX
      // 历史实测/估算比可信时用于校准本轮估算，避免估算与实测两种
      // 基线在相邻轮次间来回切换；比例异常的样本直接丢弃。
      usedTokens = trustedCalibration && calibrationFactor !== null
        ? Math.round(estimatedBase * calibrationFactor)
        : estimatedBase
    }
    usedTokens += estimateConversationTokens(params.messages.slice(anchor.index))
  }
  return {
    usedTokens,
    windowTokens,
    inputCapacityTokens,
    outputReserveTokens,
    ratio: usedTokens / windowTokens,
    source: providerCalibrated ? 'provider' : 'estimate',
  }
}

export function formatContextTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}K`
  return String(Math.max(0, Math.round(value)))
}

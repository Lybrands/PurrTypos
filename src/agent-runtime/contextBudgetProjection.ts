import type { AiContextBudgetState } from '../types'

export function projectContextBudget(
  current: AiContextBudgetState | undefined,
  update: Partial<AiContextBudgetState>,
  model?: { configId?: string; name?: string },
): AiContextBudgetState | undefined {
  const startsPreparedRequest = update.windowTokens !== undefined
  if (!current && !startsPreparedRequest) return undefined
  return {
    ...(startsPreparedRequest ? {} : current ?? {}),
    ...update,
    ...(model?.configId ? { modelConfigId: model.configId } : {}),
    ...(model?.name ? { modelName: model.name } : {}),
  } as AiContextBudgetState
}

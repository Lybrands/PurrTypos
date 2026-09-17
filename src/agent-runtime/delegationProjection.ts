import type { AiAgentDelegation, AiAgentRelatedRun } from '../types.ts'

/**
 * 子 Agent 委派的通用投影：relatedRuns（根 Run 快照 / 页面投影下发的
 * 子 Run 列表）→ 消息上的 delegations 视图。小说分析与小说写作共用；
 * 状态以投影为准（服务端子 Run 状态是权威），运行时消息字段做补充。
 */

export function delegationStatus(status: string): AiAgentDelegation['status'] {
  if (status === 'pending' || status === 'queued') return 'queued'
  if (status === 'claimed') return 'claimed'
  if (status === 'running' || status === 'waiting') return 'running'
  if (status === 'done' || status === 'completed') return 'done'
  if (status === 'canceled') return 'canceled'
  return 'failed'
}

export function relatedRunsToDelegations(
  relatedRuns: readonly AiAgentRelatedRun[] | undefined,
): AiAgentDelegation[] {
  return (relatedRuns ?? [])
    .filter((item) => item.role === 'child')
    .map((item) => ({
      delegationId: `run:${item.runId}`,
      runId: item.runId,
      ...(item.agentId ? { agentId: item.agentId } : {}),
      ...(item.previousRunId ? { previousRunId: item.previousRunId } : {}),
      agentName: item.agentName || item.agentTitle || '子 Agent',
      agentTitle: item.agentTitle || null,
      objective: item.objective || '',
      ...(item.createTime ? { startedAt: item.createTime } : {}),
      ...(item.unitId ? { unitId: item.unitId } : {}),
      ...(item.attempt != null ? { attempt: item.attempt } : {}),
      status: delegationStatus(item.status),
      required: true,
      priority: 0,
    }))
}

export function mergeDelegations(
  runtime: AiAgentDelegation[] | undefined,
  projected: AiAgentDelegation[],
): AiAgentDelegation[] | undefined {
  if (!runtime?.length) return projected.length ? projected : undefined
  const byRunId = new Map(runtime.map((item) => [item.runId, item]))
  for (const item of projected) {
    const current = byRunId.get(item.runId)
    byRunId.set(item.runId, current ? { ...item, ...current, status: item.status } : item)
  }
  return [...byRunId.values()]
}

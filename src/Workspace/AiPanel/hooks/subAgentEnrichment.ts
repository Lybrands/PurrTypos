import type { AgentConversationMessage } from '../../../agent-runtime/contracts.ts'
import { relatedRunsToDelegations } from '../../../agent-runtime/delegationProjection.ts'
import type { AiAgentRunSnapshot } from '../../../types.ts'

interface SubAgentEnrichmentDependencies {
  getRunSnapshot(input: {
    runId: string
    after?: number
    limit?: number
  }): Promise<{
    success: boolean
    data?: AiAgentRunSnapshot
    error?: string
  }>
}

/**
 * 回合结算后补齐子 Agent 委派视图。
 *
 * 直播流只转发根 Run 的 canonical 事件，子 Run 的委派信息要等根 Run
 * 快照的 relatedRuns 才有。为避免给普通回合增加请求，仅在根 Run 的
 * canonical 操作里确实调用过 delegateToAgents、且消息上还没有委派时
 * 拉取一次快照。返回更新后的消息列表；无需更新时返回 null。
 */
export async function enrichSettledSubAgents(
  snapshot: { agentRunId?: string },
  messages: readonly AgentConversationMessage[] | undefined,
  dependencies: SubAgentEnrichmentDependencies,
): Promise<AgentConversationMessage[] | null> {
  const runId = String(snapshot.agentRunId ?? '').trim()
  if (!runId || !messages?.length) return null
  const assistant = [...messages].reverse().find(
    (message) => message.role === 'assistant' && message.agentRunId === runId,
  )
  if (!assistant || assistant.delegations?.length) return null
  const operations = assistant.canonicalOutput?.operations
  const delegated = Object.values(operations ?? {}).some(
    (operation) => operation.toolName === 'delegateToAgents',
  )
  if (!delegated) return null
  const result = await dependencies.getRunSnapshot({ runId, limit: 1 })
  const delegations = relatedRunsToDelegations(result.data?.run?.relatedRuns)
  if (!delegations.length) return null
  return messages.map((message) => (
    message.role === 'assistant' && message.agentRunId === runId
      ? { ...message, delegations }
      : message
  ))
}

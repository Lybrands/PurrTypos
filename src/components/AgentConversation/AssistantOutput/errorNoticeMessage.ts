import type { AgentConversationMessage } from '../../../agent-runtime/contracts.ts'

/** 上游思考配置不匹配的典型关键词；命中时引导用户回设置页调整声明。 */
const THINKING_MISMATCH_PATTERN = /think(ing)?|reasoning/i

export function getErrorNoticeMessage(
  message: Pick<AgentConversationMessage, 'content' | 'error'>,
): string {
  const base = message.error?.trim()
    || message.content.trim()
    || '本轮执行失败'
  if (THINKING_MISMATCH_PATTERN.test(base)) {
    return `${base}\n该模型可能不支持当前思考配置，请到 设置 → 模型配置 调整「模型支持思考」。`
  }
  return base
}

export function hasRenderableErrorMessage(
  message: Pick<AgentConversationMessage, 'content' | 'error' | 'isError'>,
): boolean {
  return message.isError === true || Boolean(message.error?.trim())
}

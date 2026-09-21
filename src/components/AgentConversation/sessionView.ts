import type { AgentConversationSession } from './controller.ts'

export function toAgentConversationSession<
  TSession extends Pick<AgentConversationSession, 'id' | 'title'>,
>(
  session: TSession,
  createdAt?: string,
): AgentConversationSession & { id: TSession['id'] } {
  // 输入可能是 AiSession（后端行，sort_order）或会话视图对象（sortOrder）
  const ordering = session as {
    pinned?: number | boolean
    sortOrder?: number | null
    sort_order?: number | null
  }
  return {
    id: session.id,
    title: session.title,
    createdAt,
    pinned: Boolean(ordering.pinned),
    sortOrder: ordering.sortOrder ?? ordering.sort_order ?? null,
  }
}

export function sortConversationSessionsNewestFirst<
  TSession extends Pick<AgentConversationSession, 'createdAt'>,
>(sessions: readonly TSession[]): TSession[] {
  return sessions
    .map((session, index) => ({ session, index, createdAt: session.createdAt
      ? Date.parse(session.createdAt.replace(' ', 'T') + (/[zZ]|[+-]\d\d(?::?\d\d)?$/.test(session.createdAt) ? '' : 'Z'))
      : Number.NaN }))
    .sort((left, right) => {
      const leftValid = Number.isFinite(left.createdAt)
      const rightValid = Number.isFinite(right.createdAt)
      if (leftValid && rightValid && left.createdAt !== right.createdAt) {
        return right.createdAt - left.createdAt
      }
      if (leftValid !== rightValid) return leftValid ? -1 : 1
      return left.index - right.index
    })
    .map(({ session }) => session)
}

/**
 * 会话列表（非历史）展示顺序：
 *
 * 1. 置顶会话分组在最前；
 * 2. 组内未参与手动排序（sortOrder == null）的会话按时间倒序排在前部，
 *    使新会话照常出现在顶部；
 * 3. 组内手动排序过的会话按 sortOrder 升序排在后部。
 */
export function sortConversationSessionsForDisplay<
  TSession extends Pick<AgentConversationSession, 'createdAt' | 'pinned' | 'sortOrder'>,
>(sessions: readonly TSession[]): TSession[] {
  const sortGroup = (group: readonly TSession[]): TSession[] => {
    const ranked = group
      .filter((session) => session.sortOrder != null)
      .sort((left, right) => Number(left.sortOrder) - Number(right.sortOrder))
    const unranked = sortConversationSessionsNewestFirst(
      group.filter((session) => session.sortOrder == null),
    )
    return [...unranked, ...ranked]
  }
  return [
    ...sortGroup(sessions.filter((session) => session.pinned)),
    ...sortGroup(sessions.filter((session) => !session.pinned)),
  ]
}

/** 拖拽落点：目标会话与其上/下沿 */
export interface SessionDropTarget {
  id: AgentConversationSession['id']
  before: boolean
}

/**
 * 把拖拽会话移动到落点位置，返回新的展示顺序（全部会话 ID）；不构成
 * 有效移动时返回 null。只允许在同一置顶分组内移动（置顶组与普通组
 * 之间用置顶按钮切换）。
 */
export function computeSessionReorder<
  TSession extends AgentConversationSession,
>(
  sessions: readonly TSession[],
  draggedId: AgentConversationSession['id'],
  target: SessionDropTarget,
): Array<TSession['id']> | null {
  const draggedIndex = sessions.findIndex((session) => session.id === draggedId)
  const targetIndex = sessions.findIndex((session) => session.id === target.id)
  if (draggedIndex < 0 || targetIndex < 0) return null
  const pinned = Boolean(sessions[draggedIndex].pinned)
  if (Boolean(sessions[targetIndex].pinned) !== pinned) return null
  const groupStart = sessions.findIndex(
    (session) => Boolean(session.pinned) === pinned,
  )
  let groupEnd = groupStart
  for (let index = sessions.length - 1; index >= 0; index -= 1) {
    if (Boolean(sessions[index].pinned) === pinned) {
      groupEnd = index
      break
    }
  }
  let insertAt = target.before ? targetIndex : targetIndex + 1
  if (draggedIndex < insertAt) insertAt -= 1
  insertAt = Math.max(groupStart, Math.min(groupEnd, insertAt))
  if (insertAt === draggedIndex) return null
  const next = [...sessions]
  const [dragged] = next.splice(draggedIndex, 1)
  next.splice(insertAt, 0, dragged)
  return next.map((session) => session.id)
}

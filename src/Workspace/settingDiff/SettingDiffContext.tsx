import { services } from '@/services'
import React from 'react'
import { usePurrToast } from '@/purr-components'
import type {
  CharacterSettingSnapshot,
  EntityId,
  ProposedSettingDiff,
  SettingDiffCardState,
} from '../../types'
import {
  composeResult,
  countByStatus,
  diffParagraphs,
  diffParagraphsAsync,
  type DiffOp,
  type DiffOpStatus,
} from '../diff/paragraphDiff'
import {
  createSettingDiffCommandLatch,
  createSettingDiffOccurrenceQueue,
} from './settingDiffOccurrenceQueue'

const DIFF_ASYNC_THRESHOLD = 8000

export type SettingKind = 'character' | 'background' | 'entity'

export interface MetaDiffOp {
  index: number
  field: 'name' | 'tags'
  before: string
  after: string
  status: DiffOpStatus
}

export interface SettingDiffSession {
  proposalId: string
  sessionKey: string
  kind: SettingKind
  bookId: EntityId
  characterId?: number
  characterName?: string
  /** 世界设定实体（kind='entity'）专用 */
  entityId?: number
  entityName?: string
  before: CharacterSettingSnapshot | { content: string }
  proposed: CharacterSettingSnapshot | { content: string }
  profileOps: DiffOp[]
  metaOps: MetaDiffOp[]
  computing?: boolean
  committing?: boolean
  source: string
  startedAt: number
  resolutionTarget?: ProposedSettingDiff['resolutionTarget']
}

export function settingSessionKey(kind: SettingKind, id: number | EntityId): string {
  return `${kind}:${id}`
}

function buildMetaOps(
  before: CharacterSettingSnapshot,
  proposed: CharacterSettingSnapshot,
): MetaDiffOp[] {
  const ops: MetaDiffOp[] = []
  let idx = 0
  if (before.name !== proposed.name) {
    ops.push({
      index: idx++,
      field: 'name',
      before: before.name,
      after: proposed.name,
      status: 'pending',
    })
  }
  if (before.tags !== proposed.tags) {
    ops.push({
      index: idx++,
      field: 'tags',
      before: before.tags,
      after: proposed.tags,
      status: 'pending',
    })
  }
  return ops
}

function composeCharacterFields(session: SettingDiffSession): CharacterSettingSnapshot {
  const before = session.before as CharacterSettingSnapshot
  let name = before.name
  let tags = before.tags
  for (const op of session.metaOps) {
    const useAfter = op.status === 'accepted'
    if (op.field === 'name') name = useAfter ? op.after : op.before
    else tags = useAfter ? op.after : op.before
  }
  const profileMd = composeResult(session.profileOps)
  return { name, tags, profileMd }
}

interface SettingDiffContextValue {
  sessions: Record<string, SettingDiffSession>
  resolvedCards: Record<string, SettingDiffCardState>
  hasSession: (sessionKey: string) => boolean
  getSession: (sessionKey: string) => SettingDiffSession | undefined
  getResolvedCard: (proposalId: string) => SettingDiffCardState | undefined
  setOpStatus: (sessionKey: string, opIndex: number, status: DiffOpStatus, rejectReason?: string) => void
  setMetaOpStatus: (sessionKey: string, metaIndex: number, status: DiffOpStatus) => void
  acceptAllPending: (sessionKey: string) => void
  rejectAllPending: (sessionKey: string) => void
  exitDiff: (sessionKey: string) => Promise<void>
  commit: (sessionKey: string) => Promise<void>
  openPanelForSession: (
    sessionKey: string,
    sessionHint?: Pick<SettingDiffSession, 'kind' | 'characterId' | 'entityId'>,
  ) => void
}

const SettingDiffContext = React.createContext<SettingDiffContextValue | null>(null)

export function useSettingDiff(): SettingDiffContextValue {
  const ctx = React.useContext(SettingDiffContext)
  if (!ctx) throw new Error('useSettingDiff must be used within <SettingDiffProvider>')
  return ctx
}

export function SettingDiffProvider({
  children,
  bookId,
}: {
  children: React.ReactNode
  bookId?: EntityId | null
}) {
  const appMessage = usePurrToast()
  const [sessions, setSessions] = React.useState<Record<string, SettingDiffSession>>({})
  const [resolvedCards, setResolvedCards] = React.useState<Record<string, SettingDiffCardState>>({})
  const resolvedCardsRef = React.useRef(resolvedCards)
  const sessionsRef = React.useRef(sessions)
  const occurrenceQueueRef = React.useRef(createSettingDiffOccurrenceQueue())
  const commandLatchRef = React.useRef(createSettingDiffCommandLatch())
  React.useEffect(() => { sessionsRef.current = sessions }, [sessions])
  React.useEffect(() => { resolvedCardsRef.current = resolvedCards }, [resolvedCards])
  const updateSessions = React.useCallback((
    updater: (current: Record<string, SettingDiffSession>) => Record<string, SettingDiffSession>,
  ) => {
    const next = updater(sessionsRef.current)
    sessionsRef.current = next
    setSessions(next)
  }, [])

  const hasSession = React.useCallback((sessionKey: string) => Boolean(sessions[sessionKey]), [sessions])
  const getSession = React.useCallback((sessionKey: string) => sessions[sessionKey], [sessions])
  const getResolvedCard = React.useCallback((proposalId: string) => resolvedCards[proposalId], [resolvedCards])

  const openPanelForSession = React.useCallback((
    sessionKey: string,
    /** startDiff 中 setSessions 还没刷新 ref，可直接传入会话定位信息 */
    sessionHint?: Pick<SettingDiffSession, 'kind' | 'characterId' | 'entityId'>,
  ) => {
    const session = sessionHint ?? sessionsRef.current[sessionKey]
    if (!session) return
    window.dispatchEvent(new CustomEvent('workspace-open-panel', {
      detail: {
        panel: 'setting',
        open: true,
        setting: {
          tab: session.kind === 'character'
            ? 'characters'
            : session.kind === 'entity'
              ? 'entities'
              : 'background',
          characterId: session.characterId ?? null,
          entityId: session.entityId ?? null,
        },
      },
    }))
  }, [])

  const startDiff = React.useCallback((proposal: ProposedSettingDiff & { restoreOnly?: boolean }) => {
    const proposalId = String(proposal.proposalId || '').trim()
    if (!proposalId) return
    if (String(proposal.bookId) !== String(bookId ?? '')) return
    if (resolvedCardsRef.current[proposalId]) return
    const kind = proposal.kind
    const sessionKey = kind === 'character'
      ? settingSessionKey('character', proposal.characterId!)
      : kind === 'entity'
        ? settingSessionKey('entity', proposal.entityId!)
        : settingSessionKey('background', proposal.bookId)

    if (sessionsRef.current[sessionKey]?.proposalId === proposalId) return
    if (sessionsRef.current[sessionKey]) {
      occurrenceQueueRef.current.enqueue(sessionKey, proposal)
      if (!proposal.restoreOnly) {
        appMessage.info('该设定的新一轮修改已排队，将在当前审阅结束后继续')
      }
      return
    }

    const startedAt = Date.now()
    const source = proposal.source || 'ai_tool_edit'

    // 人物与世界设定实体共用 name/tags/profileMd 快照结构
    if (kind === 'character' || kind === 'entity') {
      const before = proposal.before as CharacterSettingSnapshot
      const proposed = proposal.proposed as CharacterSettingSnapshot
      const metaOps = buildMetaOps(before, proposed)
      const totalLen = (before.profileMd?.length ?? 0) + (proposed.profileMd?.length ?? 0)
      const displayName = (kind === 'character' ? proposal.characterName : proposal.entityName)
        || proposed.name || before.name

      const baseSession = {
        proposalId,
        sessionKey,
        kind,
        bookId: proposal.bookId,
        characterId: kind === 'character' ? proposal.characterId : undefined,
        characterName: kind === 'character' ? displayName : undefined,
        entityId: kind === 'entity' ? proposal.entityId : undefined,
        entityName: kind === 'entity' ? displayName : undefined,
        before,
        proposed,
        metaOps,
        source,
        startedAt,
        resolutionTarget: proposal.resolutionTarget,
      }

      if (totalLen < DIFF_ASYNC_THRESHOLD) {
        updateSessions((prev) => ({
          ...prev,
          [sessionKey]: {
            ...baseSession,
            profileOps: diffParagraphs(before.profileMd || '', proposed.profileMd || ''),
          },
        }))
        if (!proposal.restoreOnly) openPanelForSession(sessionKey, baseSession)
      } else {
        updateSessions((prev) => ({
          ...prev,
          [sessionKey]: { ...baseSession, profileOps: [], computing: true },
        }))
        diffParagraphsAsync(before.profileMd || '', proposed.profileMd || '').then((ops) => {
          updateSessions((prev) => {
            const cur = prev[sessionKey]
            if (!cur || cur.proposalId !== proposalId) return prev
            return { ...prev, [sessionKey]: { ...cur, profileOps: ops, computing: false } }
          })
          if (!proposal.restoreOnly) openPanelForSession(sessionKey)
        })
      }
      return
    }

    const beforeContent = (proposal.before as { content: string }).content || ''
    const proposedContent = (proposal.proposed as { content: string }).content || ''
    const totalLen = beforeContent.length + proposedContent.length

    const finishBg = (profileOps: DiffOp[]) => {
      updateSessions((prev) => ({
        ...prev,
        [sessionKey]: {
          proposalId,
          sessionKey,
          kind: 'background',
          bookId: proposal.bookId,
          before: { content: beforeContent },
          proposed: { content: proposedContent },
          profileOps,
          metaOps: [],
          source,
          startedAt,
          resolutionTarget: proposal.resolutionTarget,
        },
      }))
      if (!proposal.restoreOnly) {
        openPanelForSession(sessionKey, { kind: 'background' })
      }
    }

    if (totalLen < DIFF_ASYNC_THRESHOLD) {
      finishBg(diffParagraphs(beforeContent, proposedContent))
    } else {
      updateSessions((prev) => ({
        ...prev,
        [sessionKey]: {
          proposalId,
          sessionKey,
          kind: 'background',
          bookId: proposal.bookId,
          before: { content: beforeContent },
          proposed: { content: proposedContent },
          profileOps: [],
          metaOps: [],
          computing: true,
          source,
          startedAt,
          resolutionTarget: proposal.resolutionTarget,
        },
      }))
      diffParagraphsAsync(beforeContent, proposedContent).then((ops) => {
        updateSessions((prev) => {
          const cur = prev[sessionKey]
          if (!cur || cur.proposalId !== proposalId) return prev
          return { ...prev, [sessionKey]: { ...cur, profileOps: ops, computing: false } }
        })
        if (!proposal.restoreOnly) openPanelForSession(sessionKey)
      })
    }
  }, [appMessage, bookId, openPanelForSession, updateSessions])

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<ProposedSettingDiff>).detail
      if (!detail?.kind) return
      startDiff(detail)
    }
    window.addEventListener('ai-propose-setting-diff', handler as EventListener)
    return () => window.removeEventListener('ai-propose-setting-diff', handler as EventListener)
  }, [startDiff])

  React.useEffect(() => {
    const handler = (event: Event) => {
      const card = (event as CustomEvent<SettingDiffCardState>).detail
      if (!card?.proposalId) return
      resolvedCardsRef.current = {
        ...resolvedCardsRef.current,
        [card.proposalId]: card,
      }
      setResolvedCards(resolvedCardsRef.current)
      const session = sessionsRef.current[card.sessionKey]
      if (session?.proposalId === card.proposalId) {
        updateSessions((current) => {
          if (current[card.sessionKey]?.proposalId !== card.proposalId) return current
          const next = { ...current }
          delete next[card.sessionKey]
          return next
        })
      }
    }
    window.addEventListener('setting-diff-resolution-hydrated', handler)
    return () => window.removeEventListener('setting-diff-resolution-hydrated', handler)
  }, [updateSessions])

  const setOpStatus = React.useCallback<SettingDiffContextValue['setOpStatus']>(
    (sessionKey, opIndex, status, rejectReason) => {
      updateSessions((prev) => {
        const cur = prev[sessionKey]
        if (!cur || cur.committing) return prev
        const profileOps = cur.profileOps.map((op) => {
          if (op.index !== opIndex || op.kind === 'equal') return op
          const next = { ...op, status }
          if (status === 'rejected' && rejectReason !== undefined) {
            next.rejectReason = rejectReason
          } else if (status !== 'rejected') {
            next.rejectReason = undefined
          }
          return next
        })
        return { ...prev, [sessionKey]: { ...cur, profileOps } }
      })
    },
    [updateSessions],
  )

  const setMetaOpStatus = React.useCallback<SettingDiffContextValue['setMetaOpStatus']>(
    (sessionKey, metaIndex, status) => {
      updateSessions((prev) => {
        const cur = prev[sessionKey]
        if (!cur || cur.committing) return prev
        const metaOps = cur.metaOps.map((op) =>
          op.index === metaIndex ? { ...op, status } : op,
        )
        return { ...prev, [sessionKey]: { ...cur, metaOps } }
      })
    },
    [updateSessions],
  )

  const setAllPending = React.useCallback((sessionKey: string, status: DiffOpStatus) => {
    updateSessions((prev) => {
      const cur = prev[sessionKey]
      if (
        !cur
        || !commandLatchRef.current.canMutate(sessionKey, cur.proposalId)
      ) return prev
      const profileOps = cur.profileOps.map((op) =>
        op.kind !== 'equal' && op.status === 'pending' ? { ...op, status } : op,
      )
      const metaOps = cur.metaOps.map((op) =>
        op.status === 'pending' ? { ...op, status } : op,
      )
      return { ...prev, [sessionKey]: { ...cur, profileOps, metaOps } }
    })
  }, [updateSessions])

  const acceptAllPending = React.useCallback((sessionKey: string) => {
    setAllPending(sessionKey, 'accepted')
  }, [setAllPending])

  const rejectAllPending = React.useCallback((sessionKey: string) => {
    setAllPending(sessionKey, 'rejected')
  }, [setAllPending])

  const resolutionCard = React.useCallback((
    session: SettingDiffSession,
    status: SettingDiffCardState['status'],
    stats: { accepted: number; rejected: number },
  ) => {
    const title = session.kind === 'character'
      ? `人物「${session.characterName || (session.before as CharacterSettingSnapshot).name}」`
      : session.kind === 'entity'
        ? `设定「${session.entityName || (session.before as CharacterSettingSnapshot).name}」`
        : '故事背景'
    return {
      proposalId: session.proposalId,
      sessionKey: session.sessionKey,
      kind: session.kind,
      title,
      status,
      acceptedSegments: stats.accepted,
      rejectedSegments: stats.rejected,
    } satisfies SettingDiffCardState
  }, [])

  const markResolved = React.useCallback((card: SettingDiffCardState) => {
    resolvedCardsRef.current = {
      ...resolvedCardsRef.current,
      [card.proposalId]: card,
    }
    setResolvedCards(resolvedCardsRef.current)
    window.dispatchEvent(new CustomEvent('setting-diff-resolved', { detail: card }))
  }, [])

  const activateNext = React.useCallback((sessionKey: string) => {
    const next = occurrenceQueueRef.current.shift(sessionKey)
    if (next) queueMicrotask(() => startDiff(next))
  }, [startDiff])

  const exitDiff = React.useCallback<SettingDiffContextValue['exitDiff']>(async (sessionKey) => {
    const cur = sessionsRef.current[sessionKey]
    if (!cur || !commandLatchRef.current.tryBegin(sessionKey, cur.proposalId)) return
    updateSessions((prev) => {
      const current = prev[sessionKey]
      return current?.proposalId === cur.proposalId
        ? { ...prev, [sessionKey]: { ...current, committing: true } }
        : prev
    })
    const card = resolutionCard(cur, 'rejected', { accepted: 0, rejected: 0 })
    try {
      const target = cur.resolutionTarget
      if (!target) throw new Error('设定提议缺少持久化归属')
      const saved = await services.conversations.saveConversation({
        sessionId: target.sessionId,
        prompt: target.prompt,
        response: '',
        agentRunId: target.agentRunId,
        agentProcess: {
          settingDiff: {
            version: 1,
            resolutions: { [card.proposalId]: card },
          },
        },
      })
      if (!saved.success) throw new Error(saved.error || '设定审阅状态保存失败')
      if (!commandLatchRef.current.canComplete(
        sessionKey,
        cur.proposalId,
        sessionsRef.current[sessionKey]?.proposalId,
      )) return
      markResolved(card)
      updateSessions((prev) => {
        if (prev[sessionKey]?.proposalId !== cur.proposalId) return prev
        const next = { ...prev }
        delete next[sessionKey]
        return next
      })
      commandLatchRef.current.release(sessionKey, cur.proposalId)
      activateNext(sessionKey)
    } catch (error) {
      updateSessions((prev) => {
        const current = prev[sessionKey]
        return current?.proposalId === cur.proposalId
          ? { ...prev, [sessionKey]: { ...current, committing: false } }
          : prev
      })
      commandLatchRef.current.release(sessionKey, cur.proposalId)
      appMessage.error(error instanceof Error ? error.message : '设定审阅状态保存失败')
      throw error
    }
  }, [activateNext, appMessage, markResolved, resolutionCard, updateSessions])

  const commit = React.useCallback<SettingDiffContextValue['commit']>(async (sessionKey) => {
    const cur = sessionsRef.current[sessionKey]
    if (!cur) throw new Error('当前设定没有活跃的 diff 会话')
    if (!commandLatchRef.current.tryBegin(sessionKey, cur.proposalId)) return
    updateSessions((prev) => {
      const current = prev[sessionKey]
      if (!current || current.proposalId !== cur.proposalId || current.committing) {
        return prev
      }
      return {
        ...prev,
        [sessionKey]: { ...current, committing: true },
      }
    })

    const profileStats = countByStatus(cur.profileOps)
    const metaStats = countByStatus(cur.metaOps.map((op) => ({
      index: op.index,
      kind: 'replace' as const,
      before: op.before,
      after: op.after,
      status: op.status,
    })))
    const accepted = profileStats.accepted + metaStats.accepted
    const rejected = profileStats.rejected + metaStats.rejected
    const card = resolutionCard(cur, 'committed', { accepted, rejected })

    try {
      const target = cur.resolutionTarget
      if (!target) throw new Error('设定提议缺少持久化归属')
      const resolution = {
        ...card,
        status: 'committed' as const,
        sessionId: target.sessionId,
        agentRunId: target.agentRunId,
      }
      if (cur.kind === 'character') {
      const finalSnap = composeCharacterFields(cur)
      const before = cur.before as CharacterSettingSnapshot
      const proposed = cur.proposed as CharacterSettingSnapshot
      const res = await services.history.commitCharacterSettingDiff({
        characterId: cur.characterId!,
        name: finalSnap.name,
        tags: finalSnap.tags,
        profileMd: finalSnap.profileMd,
        before,
        after: proposed,
        source: cur.source,
        acceptedSegments: accepted,
        rejectedSegments: rejected,
        resolution,
      })
      if (!res?.success) throw new Error('提交设定 diff 失败')
      window.dispatchEvent(new CustomEvent('setting-updated', {
        detail: { kind: 'character', action: 'update', id: cur.characterId, name: finalSnap.name },
      }))
      } else if (cur.kind === 'entity') {
      const finalSnap = composeCharacterFields(cur)
      const before = cur.before as CharacterSettingSnapshot
      const proposed = cur.proposed as CharacterSettingSnapshot
      const res = await services.history.commitEntitySettingDiff({
        entityId: cur.entityId!,
        name: finalSnap.name,
        tags: finalSnap.tags,
        profileMd: finalSnap.profileMd,
        before,
        after: proposed,
        source: cur.source,
        acceptedSegments: accepted,
        rejectedSegments: rejected,
        resolution,
      })
      if (!res?.success) throw new Error('提交设定 diff 失败')
      window.dispatchEvent(new CustomEvent('setting-updated', {
        detail: { kind: 'entity', action: 'update', id: cur.entityId, name: finalSnap.name },
      }))
      } else {
      const beforeContent = (cur.before as { content: string }).content || ''
      const proposedContent = (cur.proposed as { content: string }).content || ''
      const finalContent = composeResult(cur.profileOps)
      const res = await services.history.commitBackgroundSettingDiff({
        bookId: cur.bookId,
        content: finalContent,
        beforeContent,
        afterContent: proposedContent,
        source: cur.source,
        acceptedSegments: accepted,
        rejectedSegments: rejected,
        resolution,
      })
      if (!res?.success) throw new Error('提交设定 diff 失败')
      window.dispatchEvent(new CustomEvent('setting-updated', {
        detail: { kind: 'background', action: 'update' },
      }))
      }

      if (!commandLatchRef.current.canComplete(
        sessionKey,
        cur.proposalId,
        sessionsRef.current[sessionKey]?.proposalId,
      )) {
        commandLatchRef.current.release(sessionKey, cur.proposalId)
        return
      }

      appMessage.success(
      `已应用：接受 ${accepted} 段，拒绝 ${rejected} 段` +
      (profileStats.pending + metaStats.pending > 0
        ? `，未处理 ${profileStats.pending + metaStats.pending} 段（保留原文）`
        : ''),
    )

      markResolved(card)
      updateSessions((prev) => {
        if (prev[sessionKey]?.proposalId !== cur.proposalId) return prev
        const next = { ...prev }
        delete next[sessionKey]
        return next
      })
      commandLatchRef.current.release(sessionKey, cur.proposalId)
      activateNext(sessionKey)
    } catch (error) {
      updateSessions((prev) => {
        const current = prev[sessionKey]
        if (!current || current.proposalId !== cur.proposalId) return prev
        return { ...prev, [sessionKey]: { ...current, committing: false } }
      })
      commandLatchRef.current.release(sessionKey, cur.proposalId)
      throw error
    }
  }, [activateNext, appMessage, markResolved, resolutionCard, updateSessions])

  const value = React.useMemo<SettingDiffContextValue>(() => ({
    sessions,
    resolvedCards,
    hasSession,
    getSession,
    getResolvedCard,
    setOpStatus,
    setMetaOpStatus,
    acceptAllPending,
    rejectAllPending,
    exitDiff,
    commit,
    openPanelForSession,
  }), [
    sessions,
    resolvedCards,
    hasSession,
    getSession,
    getResolvedCard,
    setOpStatus,
    setMetaOpStatus,
    acceptAllPending,
    rejectAllPending,
    exitDiff,
    commit,
    openPanelForSession,
  ])

  return (
    <SettingDiffContext.Provider value={value}>
      {children}
    </SettingDiffContext.Provider>
  )
}

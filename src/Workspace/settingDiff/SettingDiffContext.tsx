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
  source: string
  startedAt: number
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
  getResolvedCard: (sessionKey: string) => SettingDiffCardState | undefined
  setOpStatus: (sessionKey: string, opIndex: number, status: DiffOpStatus, rejectReason?: string) => void
  setMetaOpStatus: (sessionKey: string, metaIndex: number, status: DiffOpStatus) => void
  acceptAllPending: (sessionKey: string) => void
  rejectAllPending: (sessionKey: string) => void
  exitDiff: (sessionKey: string) => void
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

export function SettingDiffProvider({ children }: { children: React.ReactNode }) {
  const appMessage = usePurrToast()
  const [sessions, setSessions] = React.useState<Record<string, SettingDiffSession>>({})
  const [resolvedCards, setResolvedCards] = React.useState<Record<string, SettingDiffCardState>>({})
  const sessionsRef = React.useRef(sessions)
  React.useEffect(() => { sessionsRef.current = sessions }, [sessions])

  const hasSession = React.useCallback((sessionKey: string) => Boolean(sessions[sessionKey]), [sessions])
  const getSession = React.useCallback((sessionKey: string) => sessions[sessionKey], [sessions])
  const getResolvedCard = React.useCallback((sessionKey: string) => resolvedCards[sessionKey], [resolvedCards])

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

  const startDiff = React.useCallback((proposal: ProposedSettingDiff) => {
    const kind = proposal.kind
    const sessionKey = kind === 'character'
      ? settingSessionKey('character', proposal.characterId!)
      : kind === 'entity'
        ? settingSessionKey('entity', proposal.entityId!)
        : settingSessionKey('background', proposal.bookId)

    if (sessionsRef.current[sessionKey]) {
      appMessage.warning('该设定已有未完成的差异，请先在设定面板接受/拒绝后再继续')
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
      }

      if (totalLen < DIFF_ASYNC_THRESHOLD) {
        setSessions((prev) => ({
          ...prev,
          [sessionKey]: {
            ...baseSession,
            profileOps: diffParagraphs(before.profileMd || '', proposed.profileMd || ''),
          },
        }))
        openPanelForSession(sessionKey, baseSession)
      } else {
        setSessions((prev) => ({
          ...prev,
          [sessionKey]: { ...baseSession, profileOps: [], computing: true },
        }))
        diffParagraphsAsync(before.profileMd || '', proposed.profileMd || '').then((ops) => {
          setSessions((prev) => {
            const cur = prev[sessionKey]
            if (!cur || cur.startedAt !== startedAt) return prev
            return { ...prev, [sessionKey]: { ...cur, profileOps: ops, computing: false } }
          })
          openPanelForSession(sessionKey)
        })
      }
      return
    }

    const beforeContent = (proposal.before as { content: string }).content || ''
    const proposedContent = (proposal.proposed as { content: string }).content || ''
    const totalLen = beforeContent.length + proposedContent.length

    const finishBg = (profileOps: DiffOp[]) => {
      setSessions((prev) => ({
        ...prev,
        [sessionKey]: {
          sessionKey,
          kind: 'background',
          bookId: proposal.bookId,
          before: { content: beforeContent },
          proposed: { content: proposedContent },
          profileOps,
          metaOps: [],
          source,
          startedAt,
        },
      }))
      openPanelForSession(sessionKey, { kind: 'background' })
    }

    if (totalLen < DIFF_ASYNC_THRESHOLD) {
      finishBg(diffParagraphs(beforeContent, proposedContent))
    } else {
      setSessions((prev) => ({
        ...prev,
        [sessionKey]: {
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
        },
      }))
      diffParagraphsAsync(beforeContent, proposedContent).then((ops) => {
        setSessions((prev) => {
          const cur = prev[sessionKey]
          if (!cur || cur.startedAt !== startedAt) return prev
          return { ...prev, [sessionKey]: { ...cur, profileOps: ops, computing: false } }
        })
        openPanelForSession(sessionKey)
      })
    }
  }, [appMessage, openPanelForSession])

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<ProposedSettingDiff>).detail
      if (!detail?.kind) return
      startDiff(detail)
    }
    window.addEventListener('ai-propose-setting-diff', handler as EventListener)
    return () => window.removeEventListener('ai-propose-setting-diff', handler as EventListener)
  }, [startDiff])

  const setOpStatus = React.useCallback<SettingDiffContextValue['setOpStatus']>(
    (sessionKey, opIndex, status, rejectReason) => {
      setSessions((prev) => {
        const cur = prev[sessionKey]
        if (!cur) return prev
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
    [],
  )

  const setMetaOpStatus = React.useCallback<SettingDiffContextValue['setMetaOpStatus']>(
    (sessionKey, metaIndex, status) => {
      setSessions((prev) => {
        const cur = prev[sessionKey]
        if (!cur) return prev
        const metaOps = cur.metaOps.map((op) =>
          op.index === metaIndex ? { ...op, status } : op,
        )
        return { ...prev, [sessionKey]: { ...cur, metaOps } }
      })
    },
    [],
  )

  const setAllPending = React.useCallback((sessionKey: string, status: DiffOpStatus) => {
    setSessions((prev) => {
      const cur = prev[sessionKey]
      if (!cur) return prev
      const profileOps = cur.profileOps.map((op) =>
        op.kind !== 'equal' && op.status === 'pending' ? { ...op, status } : op,
      )
      const metaOps = cur.metaOps.map((op) =>
        op.status === 'pending' ? { ...op, status } : op,
      )
      return { ...prev, [sessionKey]: { ...cur, profileOps, metaOps } }
    })
  }, [])

  const acceptAllPending = React.useCallback((sessionKey: string) => {
    setAllPending(sessionKey, 'accepted')
  }, [setAllPending])

  const rejectAllPending = React.useCallback((sessionKey: string) => {
    setAllPending(sessionKey, 'rejected')
  }, [setAllPending])

  const markResolved = React.useCallback((
    session: SettingDiffSession,
    status: SettingDiffCardState['status'],
    stats: { accepted: number; rejected: number },
  ) => {
    const title = session.kind === 'character'
      ? `人物「${session.characterName || (session.before as CharacterSettingSnapshot).name}」`
      : session.kind === 'entity'
        ? `设定「${session.entityName || (session.before as CharacterSettingSnapshot).name}」`
        : '故事背景'
    const card: SettingDiffCardState = {
      sessionKey: session.sessionKey,
      kind: session.kind,
      title,
      status,
      acceptedSegments: stats.accepted,
      rejectedSegments: stats.rejected,
    }
    setResolvedCards((prev) => ({ ...prev, [session.sessionKey]: card }))
    window.dispatchEvent(new CustomEvent('setting-diff-resolved', { detail: card }))
  }, [])

  const exitDiff = React.useCallback<SettingDiffContextValue['exitDiff']>((sessionKey) => {
    setSessions((prev) => {
      const cur = prev[sessionKey]
      if (!cur) return prev
      markResolved(cur, 'rejected', { accepted: 0, rejected: 0 })
      const next = { ...prev }
      delete next[sessionKey]
      return next
    })
  }, [markResolved])

  const commit = React.useCallback<SettingDiffContextValue['commit']>(async (sessionKey) => {
    const cur = sessionsRef.current[sessionKey]
    if (!cur) throw new Error('当前设定没有活跃的 diff 会话')

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
      })
      if (!res?.success) throw new Error('提交设定 diff 失败')
      window.dispatchEvent(new CustomEvent('setting-updated', {
        detail: { kind: 'background', action: 'update' },
      }))
    }

    appMessage.success(
      `已应用：接受 ${accepted} 段，拒绝 ${rejected} 段` +
      (profileStats.pending + metaStats.pending > 0
        ? `，未处理 ${profileStats.pending + metaStats.pending} 段（保留原文）`
        : ''),
    )

    markResolved(cur, 'committed', { accepted, rejected })
    setSessions((prev) => {
      const next = { ...prev }
      delete next[sessionKey]
      return next
    })
  }, [appMessage, markResolved])

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

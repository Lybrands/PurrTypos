import React from 'react'
import { useToast } from '../../ui'
import type { EntityId } from '../../types'
import {
  composeResult,
  countByStatus,
  diffParagraphs,
  diffParagraphsAsync,
  type DiffOp,
  type DiffOpStatus,
} from './paragraphDiff'

/** 超过此长度走 Web Worker；与 paragraphDiff 阈值一致 */
const DIFF_ASYNC_THRESHOLD = 8000

export interface DiffSession {
  chapterId: EntityId
  /** 启动 diff 时的章节正文（用作"原文"基准与历史 before_text） */
  beforeText: string
  /** AI 给出的目标文本（用作历史 after_text；ops 已基于这个生成） */
  proposedText: string
  ops: DiffOp[]
  /** 长文本走 Worker 时，后台计算期间为 true；UI 应显示 loading 骨架 */
  computing?: boolean
  source: string
  startedAt: number
}

interface DiffContextValue {
  /** 按 chapterId 检索当前活跃会话；同一时刻一个 chapter 只允许一个 session */
  sessions: Record<string, DiffSession>
  hasSession: (chapterId: EntityId) => boolean
  getSession: (chapterId: EntityId) => DiffSession | undefined
  /**
   * 启动一个新的 diff 会话：
   * - 同 chapter 已有 session 会被覆盖（UI 应在覆盖前弹确认）
   * - beforeText 是当前 articles.content，proposedText 是 AI 输出
   */
  startDiff: (params: {
    chapterId: EntityId
    beforeText: string
    proposedText: string
    source?: string
  }) => void
  /** 修改单段 op 的 status；可选附带 rejectReason（仅 status='rejected' 时生效） */
  setOpStatus: (
    chapterId: EntityId,
    opIndex: number,
    status: DiffOpStatus,
    rejectReason?: string,
  ) => void
  /** 把所有 pending 段一次性置为 accepted */
  acceptAllPending: (chapterId: EntityId) => void
  /** 把所有 pending 段一次性置为 rejected */
  rejectAllPending: (chapterId: EntityId) => void
  /** 直接放弃整个会话，不写盘 */
  exitDiff: (chapterId: EntityId) => void
  /**
   * 提交：根据当前 ops 状态合成最终文本，写回 articles 并落 history。
   * 返回最终文本；失败抛错（外层提示）。
   */
  commit: (chapterId: EntityId) => Promise<string>
}

const DiffContext = React.createContext<DiffContextValue | null>(null)

export function useDiff(): DiffContextValue {
  const ctx = React.useContext(DiffContext)
  if (!ctx) throw new Error('useDiff must be used within <DiffProvider>')
  return ctx
}

export function DiffProvider({ children }: { children: React.ReactNode }) {
  const appMessage = useToast()
  const [sessions, setSessions] = React.useState<Record<string, DiffSession>>({})
  // 用 ref 存当前 sessions 给事件监听器读，避免依赖闭包
  const sessionsRef = React.useRef(sessions)
  React.useEffect(() => { sessionsRef.current = sessions }, [sessions])

  const key = (id: EntityId) => String(id)

  const hasSession = React.useCallback((chapterId: EntityId) => {
    return Boolean(sessions[key(chapterId)])
  }, [sessions])

  const getSession = React.useCallback((chapterId: EntityId) => {
    return sessions[key(chapterId)]
  }, [sessions])

  const startDiff = React.useCallback<DiffContextValue['startDiff']>(({ chapterId, beforeText, proposedText, source = 'ai_rewrite' }) => {
    const totalLen = (beforeText?.length ?? 0) + (proposedText?.length ?? 0)
    const startedAt = Date.now()
    const k = key(chapterId)

    if (totalLen < DIFF_ASYNC_THRESHOLD) {
      // 短文本：主线程同步计算，立刻渲染
      const ops = diffParagraphs(beforeText, proposedText)
      setSessions((prev) => ({
        ...prev,
        [k]: { chapterId, beforeText, proposedText, ops, source, startedAt },
      }))
      return
    }

    // 长文本：先放一个 computing 会话，worker 算完再补 ops
    setSessions((prev) => ({
      ...prev,
      [k]: {
        chapterId,
        beforeText,
        proposedText,
        ops: [],
        computing: true,
        source,
        startedAt,
      },
    }))
    diffParagraphsAsync(beforeText, proposedText).then((ops) => {
      setSessions((prev) => {
        const cur = prev[k]
        // 用户可能已经 exitDiff / 新 startDiff 覆盖，用 startedAt 作 fingerprint
        if (!cur || cur.startedAt !== startedAt) return prev
        return { ...prev, [k]: { ...cur, ops, computing: false } }
      })
    })
  }, [])

  const setOpStatus = React.useCallback<DiffContextValue['setOpStatus']>((chapterId, opIndex, status, rejectReason) => {
    setSessions((prev) => {
      const cur = prev[key(chapterId)]
      if (!cur) return prev
      const ops = cur.ops.map((op) => {
        if (op.index !== opIndex || op.kind === 'equal') return op
        const next: typeof op = { ...op, status }
        if (status === 'rejected') {
          // 仅当传入 rejectReason 时覆盖；否则保留旧值（多次操作不丢理由）
          if (rejectReason !== undefined) next.rejectReason = rejectReason
        } else {
          // 切换到 accepted/pending 时清掉理由
          next.rejectReason = undefined
        }
        return next
      })
      return { ...prev, [key(chapterId)]: { ...cur, ops } }
    })
  }, [])

  const setAllPending = React.useCallback((chapterId: EntityId, status: DiffOpStatus) => {
    setSessions((prev) => {
      const cur = prev[key(chapterId)]
      if (!cur) return prev
      const ops = cur.ops.map((op) => (
        op.kind !== 'equal' && op.status === 'pending'
          ? { ...op, status }
          : op
      ))
      return { ...prev, [key(chapterId)]: { ...cur, ops } }
    })
  }, [])

  const acceptAllPending = React.useCallback((chapterId: EntityId) => {
    setAllPending(chapterId, 'accepted')
  }, [setAllPending])

  const rejectAllPending = React.useCallback((chapterId: EntityId) => {
    setAllPending(chapterId, 'rejected')
  }, [setAllPending])

  const exitDiff = React.useCallback<DiffContextValue['exitDiff']>((chapterId) => {
    setSessions((prev) => {
      if (!(key(chapterId) in prev)) return prev
      const next = { ...prev }
      delete next[key(chapterId)]
      return next
    })
  }, [])

  const commit = React.useCallback<DiffContextValue['commit']>(async (chapterId) => {
    const cur = sessions[key(chapterId)]
    if (!cur) throw new Error('当前章节没有活跃的 diff 会话')
    const finalText = composeResult(cur.ops)
    const stats = countByStatus(cur.ops)

    const res = await window.electronAPI.commitChapterDiff({
      chapterId,
      content: finalText,
      beforeText: cur.beforeText,
      afterText: cur.proposedText,
      source: cur.source,
      acceptedSegments: stats.accepted,
      rejectedSegments: stats.rejected,
    })
    if (!res?.success) {
      throw new Error('提交 diff 失败')
    }

    // 通知 EditorPanel 重新拉一次正文
    window.dispatchEvent(new CustomEvent('chapter-content-updated', {
      detail: { chapterId },
    }))

    appMessage.success(
      `已应用：接受 ${stats.accepted} 段，拒绝 ${stats.rejected} 段` +
      (stats.pending > 0 ? `，未处理 ${stats.pending} 段（保留原文）` : '')
    )

    setSessions((prev) => {
      const next = { ...prev }
      delete next[key(chapterId)]
      return next
    })

    return finalText
  }, [sessions, appMessage])

  // 监听 AI 工具 editChapterContent 推送的 diff 提议：
  // 来源是 useChatSubmit 收到 chunk.proposedChapterDiff 后 dispatch 的全局事件
  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as
        | {
            chapterId: EntityId
            beforeText: string
            proposedText: string
            source?: string
          }
        | undefined
      if (!detail || detail.chapterId == null) return

      const k = key(detail.chapterId)
      if (sessionsRef.current[k]) {
        // 已有未完成会话：保留旧会话，提示用户先处理。
        // 这是常见场景：协作模式下 AI 会连续多次 editChapterContent。
        appMessage.warning('当前章节已有未完成的差异，请先在编辑区接受/拒绝后再继续与 AI 交互')
        return
      }
      startDiff({
        chapterId: detail.chapterId,
        beforeText: detail.beforeText || '',
        proposedText: detail.proposedText || '',
        source: detail.source || 'ai_tool_edit',
      })
    }
    window.addEventListener('ai-propose-chapter-diff', handler as EventListener)
    return () => window.removeEventListener('ai-propose-chapter-diff', handler as EventListener)
  }, [startDiff, appMessage])

  const value = React.useMemo<DiffContextValue>(() => ({
    sessions,
    hasSession,
    getSession,
    startDiff,
    setOpStatus,
    acceptAllPending,
    rejectAllPending,
    exitDiff,
    commit,
  }), [sessions, hasSession, getSession, startDiff, setOpStatus, acceptAllPending, rejectAllPending, exitDiff, commit])

  return (
    <DiffContext.Provider value={value}>
      {children}
    </DiffContext.Provider>
  )
}

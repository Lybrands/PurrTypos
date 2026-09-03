/**
 * 段落级 diff：把两段纯文本按 `\n` 拆成段落数组，用 LCS 算法生成 ops。
 *
 * 后处理会把相邻的 delete + insert 合并为 replace，便于 UI 上对单段做"原文 vs 改写"的视觉对比。
 */

export type DiffOpKind = 'equal' | 'replace' | 'insert' | 'delete'

export type DiffOpStatus = 'pending' | 'accepted' | 'rejected'

export interface DiffOp {
  /** 在 ops 数组里的稳定索引（生成时即固定，UI 用作 key） */
  index: number
  kind: DiffOpKind
  /** equal/replace/delete 时的原段；insert 时为空串 */
  before: string
  /** equal/replace/insert 时的新段；delete 时为空串 */
  after: string
  /**
   * - equal：固定 'accepted'（不可操作，原样保留）
   * - 其他：默认 'pending'，用户决定 accept 或 reject
   */
  status: DiffOpStatus
  /**
   * 拒绝时附带的原因（可选）。
   * - 用户在拒绝下拉里选「说明理由」后填入
   * - 提交时会拼到 history.source 备注或后续 AI 重生成时作为反馈
   */
  rejectReason?: string
}

/**
 * 把段落数组用 LCS 转成原始 ops（仅 equal/insert/delete）。
 * 然后 mergeReplaces 把相邻的 delete+insert 合并为 replace。
 */
export function diffParagraphs(beforeText: string, afterText: string): DiffOp[] {
  const before = splitParagraphs(beforeText)
  const after = splitParagraphs(afterText)

  const raw = lcsDiff(before, after)
  const merged = mergeReplaces(raw)

  return merged.map((op, i) => ({
    index: i,
    kind: op.kind,
    before: op.before,
    after: op.after,
    status: op.kind === 'equal' ? 'accepted' : 'pending',
  }))
}

/**
 * 根据当前 ops 状态合成最终文本：
 * - equal：始终保留 after（== before）
 * - accepted：保留 after
 * - rejected：保留 before
 * - pending（未操作）：保留 before（保守策略，避免误改）
 */
export function composeResult(ops: DiffOp[]): string {
  const lines: string[] = []
  for (const op of ops) {
    if (op.kind === 'equal') {
      if (op.after) lines.push(op.after)
      continue
    }
    if (op.status === 'accepted') {
      if (op.kind === 'insert' || op.kind === 'replace') {
        if (op.after) lines.push(op.after)
      }
      // delete + accepted = 删除该段，不输出
    } else {
      // pending or rejected：保留原文
      if (op.kind === 'delete' || op.kind === 'replace') {
        if (op.before) lines.push(op.before)
      }
      // insert + 拒绝 = 不输出
    }
  }
  return lines.join('\n')
}

export function countByStatus(ops: DiffOp[]): {
  total: number
  accepted: number
  rejected: number
  pending: number
} {
  let accepted = 0
  let rejected = 0
  let pending = 0
  let total = 0
  for (const op of ops) {
    if (op.kind === 'equal') continue
    total += 1
    if (op.status === 'accepted') accepted += 1
    else if (op.status === 'rejected') rejected += 1
    else pending += 1
  }
  return { total, accepted, rejected, pending }
}

/**
 * 异步版本：长文本（阈值 8000 字符）走 Web Worker，避免阻塞主线程；
 * 短文本直接同步计算，避免 worker 启动开销。
 *
 * Worker 实例懒初始化 + 单例复用；worker 不可用时 fallback 同步计算。
 */
const DIFF_WORKER_THRESHOLD = 8000

let workerSingleton: Worker | null = null
let workerSeq = 0
const pendingDiffTasks = new Map<number, (ops: DiffOp[]) => void>()

function getDiffWorker(): Worker | null {
  if (workerSingleton) return workerSingleton
  try {
    workerSingleton = new Worker(
      new URL('./diffWorker.ts', import.meta.url),
      { type: 'module' },
    )
    workerSingleton.onmessage = (ev: MessageEvent<{ id: number; ops: DiffOp[] }>) => {
      const { id, ops } = ev.data
      const resolve = pendingDiffTasks.get(id)
      if (resolve) {
        pendingDiffTasks.delete(id)
        resolve(ops)
      }
    }
    workerSingleton.onerror = () => {
      // Worker 出错：清空 pending 任务（它们会走 fallback 超时后自行解决）
      workerSingleton?.terminate()
      workerSingleton = null
    }
    return workerSingleton
  } catch {
    return null
  }
}

export function diffParagraphsAsync(
  beforeText: string,
  afterText: string,
): Promise<DiffOp[]> {
  const totalLen = (beforeText?.length ?? 0) + (afterText?.length ?? 0)
  if (totalLen < DIFF_WORKER_THRESHOLD) {
    return Promise.resolve(diffParagraphs(beforeText, afterText))
  }
  const worker = getDiffWorker()
  if (!worker) {
    return Promise.resolve(diffParagraphs(beforeText, afterText))
  }

  const id = ++workerSeq
  return new Promise<DiffOp[]>((resolve) => {
    pendingDiffTasks.set(id, resolve)
    worker.postMessage({ id, before: beforeText, after: afterText })
    // 5s 超时保护：worker 异常时 fallback 同步
    setTimeout(() => {
      if (pendingDiffTasks.has(id)) {
        pendingDiffTasks.delete(id)
        resolve(diffParagraphs(beforeText, afterText))
      }
    }, 5000)
  })
}

// ────────────────────────────────────────────────────────────────────────────

/** 把文本按 `\n` 拆，不裁剪空行（保留段落顺序与结构） */
function splitParagraphs(text: string): string[] {
  if (!text) return []
  return text.split('\n')
}

interface RawOp {
  kind: 'equal' | 'insert' | 'delete'
  before: string
  after: string
}

interface MergedOp {
  kind: DiffOpKind
  before: string
  after: string
}

/** 经典 LCS：返回从前到后的 op 序列 */
function lcsDiff(before: string[], after: string[]): RawOp[] {
  const m = before.length
  const n = after.length
  // dp[i][j] = LCS(before[0..i], after[0..j])
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (before[i - 1] === after[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1])
      }
    }
  }

  const ops: RawOp[] = []
  let i = m
  let j = n
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && before[i - 1] === after[j - 1]) {
      ops.push({ kind: 'equal', before: before[i - 1], after: after[j - 1] })
      i--
      j--
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ kind: 'insert', before: '', after: after[j - 1] })
      j--
    } else {
      ops.push({ kind: 'delete', before: before[i - 1], after: '' })
      i--
    }
  }
  ops.reverse()
  return ops
}

/**
 * 后处理：把连续的 delete + insert 合并为 replace 段（一对一对应）。
 * 多余 delete / insert 保持原样。
 *
 * 算法：扫描时维护一个"当前 delete 队列"和"当前 insert 队列"，
 * 遇到 equal 时刷出（合并为 replace 直到一队空，多余的保留为 delete/insert）。
 */
function mergeReplaces(raw: RawOp[]): MergedOp[] {
  const out: MergedOp[] = []
  let pendingDel: string[] = []
  let pendingIns: string[] = []

  const flush = () => {
    const pairCount = Math.min(pendingDel.length, pendingIns.length)
    for (let k = 0; k < pairCount; k++) {
      out.push({ kind: 'replace', before: pendingDel[k], after: pendingIns[k] })
    }
    for (let k = pairCount; k < pendingDel.length; k++) {
      out.push({ kind: 'delete', before: pendingDel[k], after: '' })
    }
    for (let k = pairCount; k < pendingIns.length; k++) {
      out.push({ kind: 'insert', before: '', after: pendingIns[k] })
    }
    pendingDel = []
    pendingIns = []
  }

  for (const op of raw) {
    if (op.kind === 'delete') {
      pendingDel.push(op.before)
    } else if (op.kind === 'insert') {
      pendingIns.push(op.after)
    } else {
      flush()
      out.push({ kind: 'equal', before: op.before, after: op.after })
    }
  }
  flush()
  return out
}

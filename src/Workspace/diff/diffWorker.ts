/**
 * Web Worker：把段落级 diff（O(m*n) LCS）放到后台线程，避免长章节阻塞主线程。
 *
 * 由 `paragraphDiff.ts` 的 `diffParagraphsAsync` 按需实例化。
 * 同一个 worker 实例可串行处理多次请求：前端根据 id 匹配结果。
 *
 * 之所以复制一份 diffParagraphs 实现而不是 `import` 原模块：
 * 原模块导入 React-adjacent 内容很克制，但用 import 在 worker bundle 里
 * 还需要保证 tree-shake 干净；这里逻辑简单，直接复制更省事、零副作用。
 */

export type DiffWorkerRequest = {
  id: number
  before: string
  after: string
}

export type DiffWorkerResponse = {
  id: number
  ops: {
    index: number
    kind: 'equal' | 'replace' | 'insert' | 'delete'
    before: string
    after: string
    status: 'pending' | 'accepted' | 'rejected'
  }[]
}

interface RawOp {
  kind: 'equal' | 'insert' | 'delete'
  before: string
  after: string
}

interface MergedOp {
  kind: 'equal' | 'replace' | 'insert' | 'delete'
  before: string
  after: string
}

function splitParagraphs(text: string): string[] {
  if (!text) return []
  return text.split('\n')
}

function lcsDiff(before: string[], after: string[]): RawOp[] {
  const m = before.length
  const n = after.length
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

function computeOps(beforeText: string, afterText: string): DiffWorkerResponse['ops'] {
  const before = splitParagraphs(beforeText)
  const after = splitParagraphs(afterText)
  const merged = mergeReplaces(lcsDiff(before, after))
  return merged.map((op, i) => ({
    index: i,
    kind: op.kind,
    before: op.before,
    after: op.after,
    status: op.kind === 'equal' ? 'accepted' : 'pending',
  }))
}

self.onmessage = (ev: MessageEvent<DiffWorkerRequest>) => {
  const { id, before, after } = ev.data
  try {
    const ops = computeOps(before, after)
    const payload: DiffWorkerResponse = { id, ops }
    ;(self as unknown as Worker).postMessage(payload)
  } catch {
    // 出错时返回空 ops，主线程会 fallback 到同步计算
    ;(self as unknown as Worker).postMessage({ id, ops: [] } as DiffWorkerResponse)
  }
}

/**
 * 扁平偏移锚定工具：章节纯文本（段落以 \n 拼接，见 editorText.ts）上的
 * 偏移 <-> Lexical 选区映射，以及引文重定位 / 以选区为中心的截断。
 *
 * Lexical 节点 key 在每次载入、重排、外部写回后都会变化，纯文本偏移 +
 * 引文快照才是可校验、可重定位的锚（批注持久化与校验式替换共用本模块）。
 * 仅依赖 lexical 核心，勿在此引入组件或 DOM。
 */

import {
  $createRangeSelection,
  $getRoot,
  $getSelection,
  $isElementNode,
  $isRangeSelection,
  $setSelection,
} from 'lexical'

/** 大小写不敏感地找出 query 在 text 中的全部起始偏移（不重叠） */
export function findAllMatchStarts(text: string, query: string): number[] {
  const q = query.trim()
  if (!q) return []
  const lower = text.toLowerCase()
  const ql = q.toLowerCase()
  const out: number[] = []
  let from = 0
  while (from <= lower.length - ql.length) {
    const idx = lower.indexOf(ql, from)
    if (idx === -1) break
    out.push(idx)
    from = idx + ql.length
  }
  return out
}

export interface FlatRange {
  start: number
  end: number
}

function $textNodePositions(): {
  entries: { key: string; start: number; len: number }[]
  total: number
} {
  const entries: { key: string; start: number; len: number }[] = []
  const children = $getRoot().getChildren()
  let pos = 0
  for (let pi = 0; pi < children.length; pi++) {
    const block = children[pi]
    const textNodes = $isElementNode(block) ? block.getAllTextNodes() : []
    for (const tn of textNodes) {
      const len = tn.getTextContent().length
      entries.push({ key: tn.getKey(), start: pos, len })
      pos += len
    }
    if (pi < children.length - 1) pos += 1
  }
  return { entries, total: pos }
}

/** 当前选区 → 扁平偏移（折叠/无选区/逆序自动归一为 start<end；无文本选区返回 null） */
export function $getFlatSelectionRange(): FlatRange | null {
  const sel = $getSelection()
  if (!$isRangeSelection(sel) || sel.isCollapsed()) return null
  const { entries } = $textNodePositions()

  const flatOf = (key: string, offset: number): number | null => {
    const hit = entries.find((e) => e.key === key)
    return hit ? hit.start + Math.min(offset, hit.len) : null
  }
  const anchorFlat = flatOf(sel.anchor.key, sel.anchor.offset)
  const focusFlat = flatOf(sel.focus.key, sel.focus.offset)
  if (anchorFlat == null || focusFlat == null) return null
  return {
    start: Math.min(anchorFlat, focusFlat),
    end: Math.max(anchorFlat, focusFlat),
  }
}

/** 与 editorStateToText 同一套扁平规则：把偏移设回选区；越界/失效返回 false */
export function $applyFlatSelection(start: number, end: number): boolean {
  if (!(start < end)) return false
  const { entries, total } = $textNodePositions()
  if (end > total) return false
  let anchorKey: string | null = null
  let anchorOffset = 0
  let focusKey: string | null = null
  let focusOffset = 0
  for (const e of entries) {
    const ns = e.start
    const ne = e.start + e.len
    if (start >= ns && start < ne) {
      anchorKey = e.key
      anchorOffset = start - ns
    }
    if (end > ns && end <= ne) {
      focusKey = e.key
      focusOffset = end - ns
    }
  }
  if (anchorKey == null || focusKey == null) return false
  const sel = $createRangeSelection()
  sel.anchor.set(anchorKey, anchorOffset, 'text')
  sel.focus.set(focusKey, focusOffset, 'text')
  $setSelection(sel)
  return true
}

/**
 * 引文重定位：quotedText 在当前全文的全部匹配中取离 preferredStart 最近的一处。
 * 无匹配返回 null。
 */
export function findAnchorStart(
  flatText: string,
  quotedText: string,
  preferredStart: number,
): number | null {
  const q = quotedText.trim()
  if (!q) return null
  const starts = findAllMatchStarts(flatText, q)
  if (starts.length === 0) return null
  let best = starts[0]
  let bestDist = Math.abs(best - preferredStart)
  for (const s of starts) {
    const dist = Math.abs(s - preferredStart)
    if (dist < bestDist) {
      best = s
      bestDist = dist
    }
  }
  return best
}

export interface TruncationResult {
  text: string
  truncated: boolean
}

/**
 * 以选区为中心截断长文本：选区完整保留，剩余篇幅按前后可用空间对半分配，
 * 边界尽量对齐段落（残行过短时丢弃，避免截出半句）。截断结果追加标注。
 */
export function truncateAroundSelection(
  fullText: string,
  selStart: number,
  selEnd: number,
  charLimit: number,
): TruncationResult {
  if (fullText.length <= charLimit) return { text: fullText, truncated: false }
  const s = Math.max(0, Math.min(selStart, fullText.length))
  const e = Math.max(s, Math.min(selEnd, fullText.length))
  const selLen = e - s
  let start = s
  let end = e
  if (selLen < charLimit) {
    const room = charLimit - selLen
    const availBefore = s
    const availAfter = fullText.length - e
    let before = Math.min(availBefore, Math.floor(room / 2))
    const after = Math.min(availAfter, room - before)
    // 后侧用不完时把余额让给前侧
    if (after < room - before) {
      before = Math.min(availBefore, room - after)
    }
    start = s - before
    end = e + after
  }

  // 段落对齐：残行 ≤ 60 字时丢弃，避免开头结尾悬半句；绝不动到选区本身
  const firstNl = fullText.indexOf('\n', start)
  if (firstNl !== -1 && firstNl < s && firstNl - start <= 60) {
    start = firstNl + 1
  }
  const lastNl = fullText.lastIndexOf('\n', end)
  if (lastNl >= e && end - lastNl <= 61) {
    end = lastNl
  }

  const middle = fullText.slice(start, end).trim()
  const head = start > 0 ? '……' : ''
  const tail = end < fullText.length ? '\n……' : ''
  const text = `${head}${middle}${tail}${
    tail ? `\n（已截断，原文约 ${fullText.length} 字）` : ''
  }`
  return { text, truncated: true }
}

const { buildDagFromToolCalls, parseArgsSafe, stringifyArgs, makeToolCall } = require('./skillPlanner')
const { getWritableChaptersForAgent } = require('./writingChaptersForAgent')

function safeLower(s) {
  return String(s || '').trim().toLowerCase()
}

function buildOutlineIndex(toolCtx) {
  const list = Array.isArray(toolCtx?.availableOutlines) ? toolCtx.availableOutlines : []
  return list
    .map((o) => ({
      id: String(o.id ?? ''),
      title: String(o.title || ''),
      type: String(o.type || ''),
    }))
    .filter((o) => o.id.length > 0)
}

function resolveOutlineIdFromCtx(args, toolCtx, latestUserText) {
  const raw = args?.outlineId
  if (raw != null && String(raw).trim() !== '') return String(raw).trim()
  const index = buildOutlineIndex(toolCtx)
  if (index.length === 0) return null
  const oi = args?.outlineIndex
  if (oi != null && oi !== '' && Number.isFinite(Number(oi))) {
    const n = Math.floor(Number(oi))
    if (n >= 1 && n <= index.length) return index[n - 1].id
  }
  const explicitName = String(args?.outlineTitle || args?.targetOutlineTitle || '').trim()
  const target = explicitName || String(latestUserText || '').trim()
  if (!target) return null
  const lower = safeLower(target)
  const hit = index.find((o) => lower.includes(safeLower(o.title)) || safeLower(o.title).includes(lower))
  return hit ? hit.id : null
}

/**
 * 由 chapterTitle / chapterIndex 解析章节 id（与附录中写作目录序号一致）；不向模型暴露 id 时仍可调工具。
 */
function resolveChapterIdFromCtx(args, toolCtx) {
  const raw = args?.chapterId
  if (raw != null && String(raw).trim() !== '') return String(raw).trim()
  const wc = getWritableChaptersForAgent(toolCtx?.writingChapters)
  if (wc.length === 0) return null
  const explicitTitle = String(args?.chapterTitle || args?.targetChapterTitle || '').trim()
  if (explicitTitle) {
    const exact = wc.find((c) => String(c.title || '').trim() === explicitTitle)
    if (exact) return String(exact.id)
    const lower = safeLower(explicitTitle)
    const hit = wc.find((c) => {
      const t = safeLower(String(c.title || ''))
      return t === lower || t.includes(lower) || lower.includes(t)
    })
    if (hit) return String(hit.id)
  }
  const idx = args?.chapterIndex
  if (idx != null && idx !== '' && Number.isFinite(Number(idx))) {
    const n = Math.floor(Number(idx))
    if (n >= 1 && n <= wc.length) return String(wc[n - 1].id)
  }
  return null
}

function normalizeArgsByCtx(name, args, toolCtx, latestUserText) {
  const next = { ...(args || {}) }
  if (next.bookId == null && toolCtx?.bookId != null) next.bookId = toolCtx.bookId

  const hasChapterHint =
    (next.chapterTitle != null && String(next.chapterTitle).trim() !== '') ||
    (next.targetChapterTitle != null && String(next.targetChapterTitle).trim() !== '') ||
    (next.chapterIndex != null && next.chapterIndex !== '')

  if (hasChapterHint) {
    const resolved = resolveChapterIdFromCtx(next, toolCtx)
    if (resolved) next.chapterId = resolved
  }

  if (next.chapterId == null && toolCtx?.chapterId != null) next.chapterId = toolCtx.chapterId

  if (name === 'updateOutline' && (next.outlineId == null || String(next.outlineId).trim() === '')) {
    const resolved = resolveOutlineIdFromCtx(next, toolCtx, latestUserText)
    if (resolved) next.outlineId = resolved
  }
  if (name === 'queryOutline') {
    const hasList =
      Array.isArray(next.outlineIds) &&
      next.outlineIds.length > 0 &&
      next.outlineIds.some((x) => String(x).trim() !== '')
    if (!hasList) {
      if (next.outlineId != null && String(next.outlineId).trim() !== '') {
        next.outlineIds = [String(next.outlineId).trim()]
      } else {
        const resolved = resolveOutlineIdFromCtx(next, toolCtx, latestUserText)
        if (resolved) next.outlineIds = [resolved]
      }
    }
  }
  return next
}

function normalizeToolCalls(toolCalls, toolCtx, latestUserText) {
  return (toolCalls || []).map((tc) => {
    const name = tc?.function?.name || ''
    const args = parseArgsSafe(tc?.function?.arguments || '{}')
    const nextArgs = normalizeArgsByCtx(name, args, toolCtx, latestUserText)
    return {
      ...tc,
      function: {
        ...tc.function,
        arguments: stringifyArgs(nextArgs),
      },
    }
  })
}

function hasCall(list, toolName) {
  return (list || []).some((x) => x?.function?.name === toolName)
}

function buildExecutableCallsFromDag(dag) {
  const byId = new Map((dag.nodes || []).map((n) => [n.nodeId, n]))
  const out = []
  for (const nodeId of dag.topoOrder || []) {
    const node = byId.get(nodeId)
    if (!node?.toolCall?.function?.name) continue
    out.push(node.toolCall)
  }
  return out
}

function injectMvpPrerequisites(calls, toolCtx, latestUserText) {
  const out = [...(calls || [])]
  // updateOutline 前始终补 queryOutline（若 outlineId 可用）和 listOutlines
  for (let i = 0; i < out.length; i++) {
    const c = out[i]
    if (c?.function?.name !== 'updateOutline') continue
    const args = parseArgsSafe(c.function.arguments || '{}')
    const nArgs = normalizeArgsByCtx('updateOutline', args, toolCtx, latestUserText)
    c.function.arguments = stringifyArgs(nArgs)
    if (!hasCall(out, 'listOutlines')) {
      out.splice(i, 0, makeToolCall('listOutlines', { bookId: toolCtx?.bookId }))
      i++
    }
    if (!hasCall(out, 'queryOutline') && nArgs.outlineId != null && String(nArgs.outlineId).trim() !== '') {
      out.splice(
        i,
        0,
        makeToolCall('queryOutline', {
          bookId: toolCtx?.bookId,
          outlineIds: [String(nArgs.outlineId).trim()],
          includeChapters: false,
          includeText: true,
        }),
      )
      i++
    }
  }
  return out
}

function parseJsonSafe(content) {
  try {
    return JSON.parse(content || '{}')
  } catch {
    return null
  }
}

/**
 * @param {{
 *   toolCalls: Array<{ id:string, type:string, function:{ name:string, arguments:string } }>,
 *   skillSpecs: Record<string, { requires?: string[] }>,
 *   toolCtx: object,
 *   latestUserText?: string
 * }} input
 */
function planToolCalls(input) {
  const normalized = normalizeToolCalls(input.toolCalls || [], input.toolCtx || {}, input.latestUserText || '')
  const dag = buildDagFromToolCalls({ toolCalls: normalized, skillSpecs: input.skillSpecs || {} })
  let executable = buildExecutableCallsFromDag(dag)
  executable = injectMvpPrerequisites(executable, input.toolCtx || {}, input.latestUserText || '')
  const telemetry = {
    nodes: dag.nodes.length,
    edges: dag.edges.length,
    insertedByDag: dag.nodes.filter((n) => n.inserted).length,
    insertedSkillNames: dag.nodes.filter((n) => n.inserted).map((n) => n.skill),
    finalCalls: executable.map((x) => x.function?.name).filter(Boolean),
  }
  return { executableCalls: executable, dag, telemetry }
}

/**
 * @param {{
 *   plannedCalls: Array<{ id:string, type:string, function:{ name:string, arguments:string } }>,
 *   toolCtx: object,
 *   latestUserText?: string,
 *   runTools: (calls:Array<any>)=>Promise<Array<{tool_call_id:string, content:string}>>,
 *   maxRepairRounds?: number
 * }} input
 */
async function executeWithRepair(input) {
  const maxRepairRounds = Number.isFinite(Number(input.maxRepairRounds)) ? Number(input.maxRepairRounds) : 1
  const toolCtx = input.toolCtx || {}
  const latestUserText = input.latestUserText || ''
  let calls = [...(input.plannedCalls || [])]
  const allResults = []
  const allCalls = []
  let repairedRounds = 0
  const repairEvents = []

  for (let round = 0; round <= maxRepairRounds; round++) {
    if (calls.length === 0) break
    const thisRoundCalls = calls
    allCalls.push(...thisRoundCalls)
    const res = await input.runTools(thisRoundCalls)
    allResults.push(...res)
    let retryCalls = []

    if (round < maxRepairRounds) {
      for (let i = 0; i < thisRoundCalls.length; i++) {
        const call = thisRoundCalls[i]
        if (call?.function?.name !== 'updateOutline') continue
        const hit = res.find((r) => r.tool_call_id === call.id)
        const payload = parseJsonSafe(hit?.content)
        const err = String(payload?.error || '')
        if (!payload || payload.success !== false) continue
        if (!/outlineid|缺少有效 outlineId/i.test(err)) continue
        const args = parseArgsSafe(call.function.arguments || '{}')
        const resolved = resolveOutlineIdFromCtx(args, toolCtx, latestUserText)
        if (!resolved) continue
        const nextArgs = { ...args, outlineId: resolved, bookId: args.bookId ?? toolCtx.bookId }
        repairEvents.push({
          tool: 'updateOutline',
          reason: '缺少 outlineId，已从上下文自动解析并重试',
          resolvedOutlineId: resolved,
        })
        retryCalls.push(makeToolCall('updateOutline', nextArgs))
      }
    }

    if (retryCalls.length > 0) {
      repairedRounds++
      calls = retryCalls
      continue
    }
    break
  }

  return {
    toolCalls: allCalls,
    toolResults: allResults,
    repairedRounds,
    repairEvents,
  }
}

module.exports = {
  planToolCalls,
  executeWithRepair,
  resolveChapterIdFromCtx,
  resolveOutlineIdFromCtx,
}

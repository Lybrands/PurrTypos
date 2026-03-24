const { buildDagFromToolCalls, parseArgsSafe, stringifyArgs, makeToolCall } = require('./skillPlanner')

function safeLower(s) {
  return String(s || '').trim().toLowerCase()
}

function buildOutlineIndex(toolCtx) {
  const list = Array.isArray(toolCtx?.availableOutlines) ? toolCtx.availableOutlines : []
  return list
    .map((o) => ({
      id: Number(o.id),
      title: String(o.title || ''),
      type: String(o.type || ''),
    }))
    .filter((o) => Number.isFinite(o.id) && o.id > 0)
}

function resolveOutlineIdFromCtx(args, toolCtx, latestUserText) {
  if (Number.isFinite(Number(args?.outlineId)) && Number(args.outlineId) > 0) return Number(args.outlineId)
  const index = buildOutlineIndex(toolCtx)
  if (index.length === 0) return null
  const explicitName = String(args?.outlineTitle || args?.targetOutlineTitle || '').trim()
  const target = explicitName || String(latestUserText || '').trim()
  if (!target) return null
  const lower = safeLower(target)
  const hit = index.find((o) => lower.includes(safeLower(o.title)) || safeLower(o.title).includes(lower))
  return hit ? hit.id : null
}

function normalizeArgsByCtx(name, args, toolCtx, latestUserText) {
  const next = { ...(args || {}) }
  if (next.bookId == null && toolCtx?.bookId != null) next.bookId = toolCtx.bookId
  if (next.chapterId == null && toolCtx?.chapterId != null) next.chapterId = toolCtx.chapterId
  if (name === 'updateOutline' && (next.outlineId == null || Number(next.outlineId) <= 0)) {
    const resolved = resolveOutlineIdFromCtx(next, toolCtx, latestUserText)
    if (resolved) next.outlineId = resolved
  }
  if (name === 'queryOutline' && next.outlineIds == null && Number.isFinite(Number(next.outlineId))) {
    next.outlineIds = [Number(next.outlineId)]
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
      out.splice(i, 0, makeToolCall('listOutlines', { bookId: toolCtx?.bookId }, 'sys', Date.now()))
      i++
    }
    if (!hasCall(out, 'queryOutline') && Number.isFinite(Number(nArgs.outlineId))) {
      out.splice(
        i,
        0,
        makeToolCall(
          'queryOutline',
          { bookId: toolCtx?.bookId, outlineIds: [Number(nArgs.outlineId)], includeChapters: false, includeText: true },
          'sys',
          Date.now() + 1,
        ),
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
  const dag = buildDagFromToolCalls({ toolCalls: normalized, skillSpecs: input.skillSpecs || {}, idPrefix: 'sys' })
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
        retryCalls.push(makeToolCall('updateOutline', nextArgs, 'repair', Date.now() + i))
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
}

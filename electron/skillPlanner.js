/**
 * Skill DAG 规划器（MVP）
 * - 输入模型 tool_calls + SkillSpec
 * - 输出线性 DAG（nodes/edges/topoOrder）
 * - 自动补齐静态前置依赖（requires）
 */

function parseArgsSafe(argsText) {
  if (typeof argsText !== 'string') return {}
  try {
    const parsed = JSON.parse(argsText)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function stringifyArgs(args) {
  try {
    return JSON.stringify(args || {})
  } catch {
    return '{}'
  }
}

function cloneToolCall(tc) {
  return {
    id: String(tc?.id || ''),
    type: tc?.type || 'function',
    function: {
      name: String(tc?.function?.name || ''),
      arguments: typeof tc?.function?.arguments === 'string' ? tc.function.arguments : '{}',
    },
  }
}

function makeToolCall(name, args, idPrefix, seq) {
  return {
    id: `${idPrefix}_${name}_${seq}`,
    type: 'function',
    function: {
      name,
      arguments: stringifyArgs(args),
    },
  }
}

/**
 * @param {{
 *   toolCalls: Array<{ id:string, type:string, function:{ name:string, arguments:string } }>,
 *   skillSpecs: Record<string, { requires?: string[] }>,
 *   idPrefix?: string
 * }} input
 */
function buildDagFromToolCalls(input) {
  const toolCalls = Array.isArray(input?.toolCalls) ? input.toolCalls : []
  const skillSpecs = input?.skillSpecs || {}
  const idPrefix = input?.idPrefix || 'sys'
  const nodes = []
  const edges = []
  const topoOrder = []
  const warnings = []
  const seenPrereq = new Set()
  let seq = 0

  for (const original of toolCalls) {
    const cur = cloneToolCall(original)
    const name = cur.function.name
    if (!name) continue
    const spec = skillSpecs[name] || {}
    const requires = Array.isArray(spec.requires) ? spec.requires : []
    for (const dep of requires) {
      if (!dep || dep === name) continue
      const key = `${dep}=>${name}`
      if (seenPrereq.has(key)) continue
      seenPrereq.add(key)
      const depCall = makeToolCall(dep, {}, idPrefix, seq++)
      nodes.push({
        nodeId: depCall.id,
        skill: dep,
        toolCall: depCall,
        inserted: true,
      })
      edges.push({
        from: depCall.id,
        to: cur.id,
        type: 'depends_on',
      })
      topoOrder.push(depCall.id)
    }
    if (!cur.id) {
      cur.id = `${idPrefix}_${name}_${seq++}`
      warnings.push(`tool_call 缺少 id，已自动生成：${cur.id}`)
    }
    nodes.push({
      nodeId: cur.id,
      skill: name,
      toolCall: cur,
      inserted: false,
    })
    topoOrder.push(cur.id)
  }

  return { nodes, edges, topoOrder, warnings }
}

module.exports = {
  buildDagFromToolCalls,
  parseArgsSafe,
  stringifyArgs,
  makeToolCall,
}

/**
 * Skill DAG 规划器（MVP）
 * - 输入模型 tool_calls + SkillSpec
 * - 输出线性 DAG（nodes/edges/topoOrder）
 * - 自动补齐静态前置依赖（requires）
 * - 若本轮 tool_calls 中在**当前调用之前**已出现同名依赖，则复用该节点加边，不再插入合成 tool_call
 */

const { shortId8 } = require('./idUtils')

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

function makeToolCall(name, args) {
  return {
    id: shortId8(),
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
 * }} input
 */
function buildDagFromToolCalls(input) {
  const toolCalls = Array.isArray(input?.toolCalls) ? input.toolCalls : []
  const skillSpecs = input?.skillSpecs || {}
  const nodes = []
  const edges = []
  const topoOrder = []
  const warnings = []
  const seenPrereq = new Set()

  const prepared = toolCalls.map((original) => {
    const cur = cloneToolCall(original)
    if (!cur.id) {
      cur.id = shortId8()
      warnings.push(`tool_call 缺少 id，已自动生成：${cur.id}`)
    }
    return cur
  })

  for (let inputIdx = 0; inputIdx < prepared.length; inputIdx++) {
    const cur = prepared[inputIdx]
    const name = cur.function.name
    if (!name) continue
    const spec = skillSpecs[name] || {}
    const requires = Array.isArray(spec.requires) ? spec.requires : []
    for (const dep of requires) {
      if (!dep || dep === name) continue
      const key = `${dep}=>${name}`
      if (seenPrereq.has(key)) continue
      seenPrereq.add(key)

      let fromInputId = ''
      for (let j = inputIdx - 1; j >= 0; j--) {
        if (String(prepared[j].function.name || '') === dep) {
          fromInputId = prepared[j].id
          break
        }
      }

      if (fromInputId) {
        edges.push({
          from: fromInputId,
          to: cur.id,
          type: 'depends_on',
        })
        continue
      }

      const depCall = makeToolCall(dep, {})
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

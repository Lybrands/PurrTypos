/**
 * Subagent 多阶段管线：与 legacy 流式对话隔离，便于单独维护与测试。
 */

const toolRouter = require('./toolRouter')
const { buildToolRouterEmbeddingQuery } = require('./toolRouterQueryText')
const openaiChat = require('./openaiChat')
const anthropicChat = require('./anthropicChat')
const toolExecutor = require('./toolExecutor')
const skillOrchestrator = require('./skillOrchestrator')
const { normalizeToolCallsList } = require('./toolCallUtils')
const { ROUTER_SKILL_ITEMS, toOpenAiTools } = require('./agentToolDefinitions')
const {
  STAGES,
  EXEC_ACTIONS,
  SUBAGENT_REGISTRY,
  buildToolPermissionsForStage,
  extractStructuredJsonFromModelText,
  normalizeAnalyzeReport,
  normalizeWritingBlueprint,
  normalizeDraftDocument,
  normalizeReviewIssues,
  validateSubagentConfig,
} = require('./subagentConfig')

try {
  validateSubagentConfig()
} catch (err) {
  console.error('[subagent-config] invalid:', (err && err.message) || String(err))
}

function filterToolSchemasByNames(toolSchemas, allowedNamesSet) {
  if (!Array.isArray(toolSchemas) || !allowedNamesSet || allowedNamesSet.size === 0) return []
  return toolSchemas.filter((t) => {
    const name = t?.function?.name
    return Boolean(name && allowedNamesSet.has(name))
  })
}

function splitDraftIntoSegments(text, maxChars = 1600) {
  const source = String(text || '').trim()
  if (!source) return []
  const paras = source.split(/\n{2,}/).map((x) => x.trim()).filter(Boolean)
  if (paras.length === 0) return [source]
  const out = []
  let buf = ''
  for (const p of paras) {
    if (!buf) {
      buf = p
      continue
    }
    if ((buf + '\n\n' + p).length > maxChars) {
      out.push(buf)
      buf = p
    } else {
      buf += '\n\n' + p
    }
  }
  if (buf) out.push(buf)
  return out
}

function extractContextAroundSpan(text, span, radius = 240) {
  const src = String(text || '')
  const s = String(span || '').trim()
  if (!src || !s) return ''
  const idx = src.indexOf(s)
  if (idx < 0) return s
  const start = Math.max(0, idx - radius)
  const end = Math.min(src.length, idx + s.length + radius)
  return src.slice(start, end)
}

/**
 * 审校/润色/全流程时若无模型产出的初稿，将用户输入视为待处理正文（显式「仅审校」等场景）。
 */
function resolveDraftPlainText(draftDoc, userText, action) {
  const fromModel = String(draftDoc?.content || '').trim()
  if (fromModel) return fromModel
  const needBody = [EXEC_ACTIONS.REVIEW, EXEC_ACTIONS.POLISH, EXEC_ACTIONS.FULL].includes(action)
  const u = String(userText || '').trim()
  if (needBody && u.length >= 40) return u
  return ''
}

/**
 * @param {object} input
 * @param {Function} input.sendChunk
 * @param {AbortSignal} input.signal
 * @param {string} input.key
 * @param {string} input.apiProvider
 * @param {object} input.requestParams
 * @param {object} input.toolCtx
 * @param {Record<string, unknown>} input.skillSpecs
 * @param {Array} input.messages
 * @param {string} input.latestUserTextForPlan
 * @param {boolean} input.useToolRouter
 * @param {Array} input.toolsFromFront
 * @param {string} input.action
 * @param {string} input.model
 */
async function runSubagentPipeline(input) {
  const {
    sendChunk,
    signal,
    key,
    apiProvider,
    requestParams,
    toolCtx,
    skillSpecs,
    messages,
    latestUserTextForPlan,
    useToolRouter,
    toolsFromFront,
    action,
    model,
  } = input

  const getToolsForStage = async (stage, stageMessages) => {
    const allowedSet = new Set(buildToolPermissionsForStage(stage))
    let candidateTools = Array.isArray(toolsFromFront) ? toolsFromFront : []
    if (useToolRouter && toolCtx.bookId != null && Array.isArray(stageMessages) && stageMessages.length > 0) {
      try {
        const routed = await toolRouter.getToolsForQuery(buildToolRouterEmbeddingQuery(stageMessages))
        candidateTools = routed.tools || []
        if (Array.isArray(routed.warnings) && routed.warnings.length > 0) {
          sendChunk({ toolRouterWarning: routed.warnings.join(' ') })
        }
      } catch (err) {
        sendChunk({ toolRouterWarning: `工具路由调用失败，${(err && err.message) || String(err)}` })
      }
    } else if ((!candidateTools || candidateTools.length === 0) && allowedSet.size > 0) {
      const allTools = toOpenAiTools(ROUTER_SKILL_ITEMS)
      candidateTools = filterToolSchemasByNames(allTools, allowedSet)
    }
    return filterToolSchemasByNames(candidateTools, allowedSet)
  }

  const chatNoStreamUnified = async (msgs, stageTools) => {
    const stageParams = { ...requestParams, tools: stageTools }
    if (apiProvider === 'anthropic') {
      return anthropicChat.chatNoStreamAsOpenAIFormat(key, msgs, stageParams, signal)
    }
    return openaiChat.chatNoStream(key, msgs, stageParams, signal)
  }

  const runNonStreamToolLoop = async (currentMessages, stageTools, stageName) => {
    const allowedNames = new Set(buildToolPermissionsForStage(stageName))
    const toolCtxWithAllow = { ...toolCtx, subagentAllowedToolNames: allowedNames }

    let loopMessages = currentMessages
    let finalText = ''
    let finalThinking = ''
    for (let guard = 0; guard < 6; guard++) {
      if (signal.aborted) break
      const { message } = await chatNoStreamUnified(loopMessages, stageTools)
      const content = String(message?.content || '')
      const thinking = String(message?.reasoning_content || '')
      if (content) finalText = content
      if (thinking) finalThinking = thinking
      const list = normalizeToolCallsList(Array.isArray(message?.tool_calls) ? message.tool_calls : [])
      if (list.length === 0) {
        return { text: finalText, thinking: finalThinking }
      }
      const plan = skillOrchestrator.planToolCalls({
        toolCalls: list,
        skillSpecs,
        toolCtx: toolCtxWithAllow,
        latestUserText: latestUserTextForPlan,
      })
      const executableCalls = plan.executableCalls || list
      sendChunk({
        toolCalls: executableCalls,
        toolCallsInProgress: true,
        partialContent: finalText,
        partialThinking: finalThinking,
        orchestratorInfo: {
          insertedByDag: plan?.telemetry?.insertedByDag || 0,
          plannedNodeCount: plan?.telemetry?.nodes || executableCalls.length,
          insertedSkillNames: plan?.telemetry?.insertedSkillNames || [],
          plannedToolNames: plan?.telemetry?.finalCalls || executableCalls.map((x) => x.function?.name).filter(Boolean),
          stage: stageName,
        },
        model,
      })
      const executed = await skillOrchestrator.executeWithRepair({
        plannedCalls: executableCalls,
        toolCtx: toolCtxWithAllow,
        latestUserText: latestUserTextForPlan,
        maxRepairRounds: 1,
        runTools: async (calls) =>
          toolExecutor.runTools(calls, toolCtxWithAllow, (ev) => {
            if (!ev) return
            if (ev.chapterContentUpdated != null) sendChunk({ chapterContentUpdated: ev.chapterContentUpdated })
            if (typeof ev.toolIndexCompleted === 'number') sendChunk({ toolIndexCompleted: ev.toolIndexCompleted })
          }),
      })
      const toolResults = executed.toolResults || []
      const usedCalls = executed.toolCalls || executableCalls
      const assistantMsg = {
        role: 'assistant',
        content: finalText,
        tool_calls: usedCalls.map((tc) => ({
          id: tc.id,
          type: 'function',
          function: { name: tc.function.name, arguments: tc.function.arguments },
        })),
        ...(finalThinking ? { reasoning_content: finalThinking } : {}),
      }
      const toolMsgs = toolResults.map((r) => ({ role: 'tool', tool_call_id: r.tool_call_id, content: r.content }))
      loopMessages = [...loopMessages, assistantMsg, ...toolMsgs]
    }
    return { text: finalText, thinking: finalThinking }
  }

  const runStage = async (stage, userContent) => {
    const stageCfg = SUBAGENT_REGISTRY[stage]
    const baseSystem = (messages.find((m) => m && m.role === 'system')?.content || '').trim()
    const stageSystem = [baseSystem, stageCfg.systemPrompt].filter(Boolean).join('\n\n')
    const stageMessages = [
      { role: 'system', content: stageSystem },
      ...messages.filter((m) => m && m.role !== 'system'),
      { role: 'user', content: userContent },
    ]
    const stageTools = await getToolsForStage(stage, stageMessages)
    const stageRes = await runNonStreamToolLoop(stageMessages, stageTools, stage)
    sendChunk({ subagentStageDone: stage, subagentStageName: stageCfg.name })
    const parsed = extractStructuredJsonFromModelText(stageRes.text)
    return { rawText: stageRes.text, parsed }
  }

  const latestUser = [...messages].reverse().find((m) => m && m.role === 'user')
  const userText = String(latestUser?.content || '')

  const analyzeInput = `请输出 AnalyzeReport JSON（仅 JSON 对象，字段：summary, goals, constraints, risks, evidence）。\n用户请求：${userText}`
  const analyzeRes = await runStage(STAGES.ANALYZE, analyzeInput)
  const analyzeReport = normalizeAnalyzeReport(analyzeRes.parsed, userText)
  sendChunk({ subagentStage: STAGES.ANALYZE, subagentPayload: analyzeReport })

  let blueprint = null
  let draft = null
  let reviewIssues = []

  const needPlan = [EXEC_ACTIONS.PLAN, EXEC_ACTIONS.DRAFT, EXEC_ACTIONS.REVIEW, EXEC_ACTIONS.POLISH, EXEC_ACTIONS.FULL].includes(action)
  if (needPlan) {
    const planInput = [
      '请基于 AnalyzeReport 生成 WritingBlueprint JSON（仅 JSON）。',
      'AnalyzeReport:',
      JSON.stringify(analyzeReport),
      '注意：不要复述原始长素材。',
    ].join('\n')
    const planRes = await runStage(STAGES.PLAN, planInput)
    blueprint = normalizeWritingBlueprint(planRes.parsed)
    sendChunk({ subagentStage: STAGES.PLAN, subagentPayload: blueprint })
  }

  const needDraft = [EXEC_ACTIONS.DRAFT, EXEC_ACTIONS.REVIEW, EXEC_ACTIONS.POLISH, EXEC_ACTIONS.FULL].includes(action)
  if (needDraft) {
    const draftInput = [
      '请输出 DraftDocument JSON（仅 JSON，字段：title, content, notes）。',
      '输入蓝图:',
      JSON.stringify(blueprint || {}),
      '并仅引用当前章节必要素材；content 为完整初稿正文。',
    ].join('\n')
    const draftRes = await runStage(STAGES.DRAFT, draftInput)
    draft = normalizeDraftDocument(draftRes.parsed)
    sendChunk({ subagentStage: STAGES.DRAFT, subagentPayloadMeta: { contentLength: draft.content.length } })
  }

  const draftPlain = resolveDraftPlainText(draft, userText, action)

  const needReview = [EXEC_ACTIONS.REVIEW, EXEC_ACTIONS.POLISH, EXEC_ACTIONS.FULL].includes(action)
  if (needReview) {
    const segments = splitDraftIntoSegments(draftPlain)
    const allIssues = []
    if (segments.length === 0) {
      sendChunk({
        toolRouterWarning: '当前没有可审校的文本：请先由写作阶段生成初稿，或在输入框粘贴待审正文（≥40 字）后再选「仅审校/仅润色」。',
      })
    }
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i]
      const reviewInput = [
        '请输出 ReviewIssues JSON 数组（仅 JSON 数组）。每项含 segmentIndex, span, issueType, severity, suggestion, context。',
        `segmentIndex=${i}`,
        'segmentText:',
        seg,
      ].join('\n')
      try {
        const reviewRes = await runStage(STAGES.REVIEW, reviewInput)
        const issues = normalizeReviewIssues(reviewRes.parsed)
        allIssues.push(...issues.map((x) => ({ ...x, segmentIndex: i })))
      } catch (err) {
        allIssues.push({
          segmentIndex: i,
          span: seg.slice(0, 50),
          issueType: 'system',
          severity: 'low',
          suggestion: `该分段审校失败：${(err && err.message) || String(err)}`,
          context: seg.slice(0, 300),
        })
      }
    }
    reviewIssues = allIssues
    sendChunk({ subagentStage: STAGES.REVIEW, subagentPayloadMeta: { issueCount: reviewIssues.length } })
  }

  const needPolish = [EXEC_ACTIONS.POLISH, EXEC_ACTIONS.FULL].includes(action)
  if (needPolish) {
    const polishInput = [
      '请输出 PolishedResult JSON（仅 JSON），字段：finalText、changeSummary。',
      '不要要求全文，仅基于问题点位与上下文窗口修订。',
      'ReviewIssuesWithContext:',
      JSON.stringify(
        reviewIssues.map((x) => ({
          ...x,
          context: x.context || extractContextAroundSpan(draftPlain, x.span),
        })),
      ),
    ].join('\n')
    const polishRes = await runStage(STAGES.POLISH, polishInput)
    const p = polishRes.parsed && typeof polishRes.parsed === 'object' ? polishRes.parsed : {}
    const finalText = String(p.finalText || draftPlain || '')
    sendChunk({ delta: finalText })
  } else if (action === EXEC_ACTIONS.REVIEW) {
    sendChunk({
      delta: JSON.stringify({ issues: reviewIssues }, null, 2),
    })
  } else if (action === EXEC_ACTIONS.DRAFT) {
    sendChunk({ delta: String(draftPlain || '') })
  } else if (action === EXEC_ACTIONS.PLAN) {
    sendChunk({ delta: JSON.stringify(blueprint || {}, null, 2) })
  } else {
    sendChunk({ delta: JSON.stringify(analyzeReport, null, 2) })
  }

  sendChunk({ done: true, model })
}

module.exports = {
  runSubagentPipeline,
  filterToolSchemasByNames,
  splitDraftIntoSegments,
  extractContextAroundSpan,
}

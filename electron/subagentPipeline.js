/**
 * Subagent 多阶段管线：与 legacy 流式对话隔离，便于单独维护与测试。
 */

const { setImmediate } = require('timers')
const toolRouter = require('./toolRouter')
const { buildSubagentStageRouterQuery } = require('./toolRouterQueryText')
const openaiChat = require('./openaiChat')
const anthropicChat = require('./anthropicChat')
const toolExecutor = require('./toolExecutor')
const skillOrchestrator = require('./skillOrchestrator')
const { normalizeToolCallsList } = require('./toolCallUtils')
const { toOpenAiTools } = require('./agentToolDefinitions')
const {
  STAGES,
  EXEC_ACTIONS,
  SUBAGENT_REGISTRY,
  buildToolPermissionsForStage,
  extractStructuredJsonFromModelText,
  normalizeAnalyzeReport,
  normalizeWritingBlueprint,
  normalizeDraftDocument,
  normalizeStyleUnifyResult,
  normalizeReviewIssues,
  validateSubagentConfig,
} = require('./subagentConfig')

try {
  validateSubagentConfig()
} catch (err) {
  console.error('[subagent-config] invalid:', (err && err.message) || String(err))
}

/** 流式片段（OpenAI / 网关常把 content 做成对象数组）→ 纯文本 */
function streamPartToText(part) {
  if (part == null) return ''
  if (typeof part === 'string') return part
  if (typeof part !== 'object') return ''
  if (typeof part.text === 'string') return part.text
  if (typeof part.content === 'string') return part.content
  return ''
}

/** OpenAI 兼容流式 chunk.delta → 可拼接的正文（兼容 content 为数组等形态） */
function textFromChatDelta(delta) {
  if (!delta || typeof delta !== 'object') return ''
  const c = delta.content
  if (c == null || c === '') {
    if (delta.refusal != null && String(delta.refusal).trim()) return String(delta.refusal)
    if (typeof delta.text === 'string' && delta.text) return delta.text
    return ''
  }
  if (typeof c === 'string') return c
  if (Array.isArray(c)) {
    return c.map(streamPartToText).join('')
  }
  return String(c)
}

/** 单条 choice：优先 delta，其次少数实现放在 message / text 上的整段增量 */
function textFromStreamChoice0(choice0) {
  if (!choice0) return ''
  const fromDelta = textFromChatDelta(choice0.delta || {})
  if (fromDelta) return fromDelta
  const msg = choice0.message
  if (msg && typeof msg === 'object') {
    const mc = msg.content
    if (typeof mc === 'string' && mc) return mc
    if (Array.isArray(mc)) return mc.map(streamPartToText).join('')
  }
  if (typeof choice0.text === 'string') return choice0.text
  return ''
}

function filterToolSchemasByNames(toolSchemas, allowedNamesSet) {
  if (!Array.isArray(toolSchemas) || !allowedNamesSet || allowedNamesSet.size === 0) return []
  return toolSchemas.filter((t) => {
    const name = t?.function?.name
    return Boolean(name && allowedNamesSet.has(name))
  })
}

/**
 * 向量路由 Top-K 常漏掉本阶段必需的写入类工具（如润色需 editChapterContent）。
 * 在阶段白名单内按约定顺序补全缺失项，保证模型能看到完整 allowed 列表。
 */
function ensureStageCandidateTools(candidateTools, allowedSet) {
  toolRouter.ensureSkillsLoaded()
  const allApi = toOpenAiTools(toolRouter.getApiSkillItems())
  const allByName = new Map(allApi.map((t) => [t?.function?.name, t]))
  const present = new Set()
  const out = []
  for (const t of candidateTools || []) {
    const n = t?.function?.name
    if (n && allowedSet.has(n) && !present.has(n)) {
      out.push(t)
      present.add(n)
    }
  }
  for (const name of allowedSet) {
    if (!present.has(name) && allByName.has(name)) {
      out.push(allByName.get(name))
      present.add(name)
    }
  }
  return out
}

/** 章节正文/目录 gate 类工具：若本批仅调用这些且全部返回 JSON error，提前结束 tool loop，避免反复用错误 id 打 API */
const CHAPTER_CATALOG_BODY_TOOL_NAMES = new Set(['getChapterContent', 'editChapterContent', 'batchGetChapterContents'])

function toolResultContentLooksLikeJsonError(content) {
  if (typeof content !== 'string') return false
  const s = content.trim()
  if (!s.startsWith('{')) return false
  try {
    const o = JSON.parse(s)
    return o != null && typeof o === 'object' && typeof o.error === 'string' && o.error.length > 0
  } catch {
    return false
  }
}

/**
 * 将 toolCtx 渲染为 system 附录（与 main.js 构造的 toolCtx 字段一致）。
 * 刻意不暴露 bookId/chapterId/outlineId：模型用「章节标题 / 序号」指称，由 skillOrchestrator 解析为 id。
 * 不含：用户多轮对话正文（那部分在 messages）；不含历史 tool 的原始返回 JSON。
 */
const { getWritableChaptersForAgent } = require('./writingChaptersForAgent')

function buildSubagentToolingContextAppendix(toolCtx) {
  if (!toolCtx || toolCtx.bookId == null) return ''
  const wcRaw = Array.isArray(toolCtx.writingChapters) ? toolCtx.writingChapters : []
  const wc = getWritableChaptersForAgent(wcRaw)
  const ao = Array.isArray(toolCtx.availableOutlines) ? toolCtx.availableOutlines : []
  const accCh = Array.isArray(toolCtx.associatedChapterIds) ? toolCtx.associatedChapterIds : []
  const accOl = Array.isArray(toolCtx.associatedOutlineIds) ? toolCtx.associatedOutlineIds : []
  const lines = []
  lines.push('【会话同步 — 工具与界面上下文（不向模型暴露数据库 id）】')
  lines.push(
    '宿主已为当前会话绑定书籍与章节；bookId/chapterId 若缺省将由工具层注入。若需指定**非当前**章节或大纲：'
      + '工具参数使用 chapterTitle（与下方《》内标题完全一致）或 chapterIndex、outlineTitle 或 outlineIndex；禁止编造 id。',
  )
  const bookTitle = String(toolCtx.bookTitle || '').replace(/\s+/g, ' ').trim()
  lines.push(
    `当前书籍：${bookTitle ? `《${bookTitle}》` : '（未传书名）'}；当前写作章节：${
      toolCtx.chapterId
        ? `《${String(toolCtx.currentChapterTitle || '').replace(/\s+/g, ' ')}》`
        : '（未选章节）'
    }`,
  )
  if (accCh.length && wc.length) {
    const bits = accCh.map((id) => {
      const c = wc.find((x) => String(x.id) === String(id))
      const title = c ? String(c.title || '').replace(/\r?\n/g, ' ').trim() : ''
      return title ? `《${title}》` : '（未匹配章节）'
    })
    lines.push(`用户在本轮关联的写作章节：${bits.join('、')}`)
  }
  if (accOl.length && ao.length) {
    const bits = accOl.map((id) => {
      const o = ao.find((x) => String(x.id) === String(id))
      const title = o ? String(o.title || '').replace(/\r?\n/g, ' ').trim() : ''
      return title ? `《${title}》` : '（未匹配大纲）'
    })
    lines.push(`用户在本轮关联的大纲：${bits.join('、')}`)
  }
  const WC_CAP = 100
  if (wc.length) {
    lines.push(
      `写作章节目录（仅含可写正文的章节，不含卷名行；chapterIndex=1…${Math.min(wc.length, WC_CAP)} 或 chapterTitle=与下列标题完全一致）。共 ${wc.length} 条：`,
    )
    for (let i = 0; i < Math.min(wc.length, WC_CAP); i++) {
      const c = wc[i]
      lines.push(`- [${i + 1}] ${String(c.title || '').replace(/\r?\n/g, ' ').slice(0, 120)}`)
    }
    if (wc.length > WC_CAP) lines.push(`… 余 ${wc.length - WC_CAP} 条略`)
  }
  const AO_CAP = 60
  if (ao.length) {
    lines.push(
      `大纲列表（参数 outlineIndex=1…${Math.min(ao.length, AO_CAP)} 或 outlineTitle；与 listOutlines/queryOutline/updateOutline 等一致）。共 ${ao.length} 条：`,
    )
    for (let i = 0; i < Math.min(ao.length, AO_CAP); i++) {
      const o = ao[i]
      const typ = o.type != null ? String(o.type) : ''
      lines.push(
        `- [${i + 1}] ${String(o.title || '').replace(/\r?\n/g, ' ').slice(0, 80)}${typ ? `\t${typ}` : ''}`,
      )
    }
    if (ao.length > AO_CAP) lines.push(`… 余 ${ao.length - AO_CAP} 条略`)
  }
  return lines.join('\n')
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
  const needBody = [
    EXEC_ACTIONS.REVIEW,
    EXEC_ACTIONS.POLISH,
    EXEC_ACTIONS.FULL,
    EXEC_ACTIONS.STYLE_UNIFY,
  ].includes(action)
  const u = String(userText || '').trim()
  if (needBody && u.length >= 40) return u
  return ''
}

/**
 * 由多选阶段推导管线开关与用于「用户粘贴代正文」的 resolveAction。
 * @param {string[]|null|undefined} agentActions
 */
function normalizePipelineFlags(agentActions) {
  const list =
    Array.isArray(agentActions) && agentActions.length > 0
      ? agentActions.map(String)
      : [EXEC_ACTIONS.FULL]
  const keys = new Set(list)
  if (keys.has(EXEC_ACTIONS.FULL)) {
    return {
      needPlan: true,
      needDraft: true,
      needStyleUnify: true,
      needReview: true,
      needPolish: true,
      onlyDraft: false,
      resolveAction: EXEC_ACTIONS.FULL,
      pipelineActionLabel: 'full',
    }
  }
  const needPlan = ['plan', 'draft', 'styleUnify', 'review', 'polish'].some((k) => keys.has(k))
  const needDraft = ['draft', 'styleUnify', 'review', 'polish'].some((k) => keys.has(k))
  const needStyleUnify = ['styleUnify', 'review', 'polish'].some((k) => keys.has(k))
  const needReview = ['review', 'polish'].some((k) => keys.has(k))
  const needPolish = keys.has('polish')
  const onlyDraft = keys.size === 1 && keys.has('draft')
  let resolveAction = EXEC_ACTIONS.ANALYZE
  if (needPolish) resolveAction = EXEC_ACTIONS.POLISH
  else if (needReview) resolveAction = EXEC_ACTIONS.REVIEW
  else if (needStyleUnify) resolveAction = EXEC_ACTIONS.STYLE_UNIFY
  else if (needDraft) resolveAction = EXEC_ACTIONS.DRAFT
  else if (needPlan) resolveAction = EXEC_ACTIONS.PLAN
  const pipelineActionLabel = [...keys].sort().join('+')
  return {
    needPlan,
    needDraft,
    needStyleUnify,
    needReview,
    needPolish,
    onlyDraft,
    resolveAction,
    pipelineActionLabel,
  }
}

/**
 * 根据当前写作章在目录中的位置，推算应读取的「紧邻前文」chapterIndex 列表（3～5 章，不足则全读）。
 */
function buildStyleUnifyPriorChapterHint(toolCtx) {
  if (!toolCtx || toolCtx.bookId == null) {
    return '【宿主提示】未绑定书籍时仍应用工具列出章节并自行选取前文。'
  }
  const wcRaw = Array.isArray(toolCtx.writingChapters) ? toolCtx.writingChapters : []
  const wc = getWritableChaptersForAgent(wcRaw)
  const curId = toolCtx.chapterId
  if (!curId || !wc.length) {
    return '【宿主提示】未选可写章节或目录为空：请先 listWritingChapters；若无前文则在 styleAnchors 中说明。'
  }
  const idx = wc.findIndex((c) => String(c.id) === String(curId))
  if (idx < 0) {
    return '【宿主提示】当前章节不在可写目录中：请核对章节标题与 chapterIndex。'
  }
  if (idx === 0) {
    return '【宿主提示】当前章为目录中第 1 章，无前文：styleAnchors 须注明「无前文可参照」，仅做语气与蓝图内约束下的统一。'
  }
  const nPrior = idx
  const nRead = nPrior >= 3 ? Math.min(5, nPrior) : nPrior
  const start = idx - nRead
  const lines = []
  lines.push(
    `【宿主推算】当前章在写作目录中为第 ${idx + 1} 章（chapterIndex=${idx + 1}）。请必读以下 ${nRead} 章正文以萃取文风（紧邻当前章向前的连续章）：`,
  )
  for (let i = start; i < idx; i++) {
    const t = String(wc[i]?.title || '').replace(/\r?\n/g, ' ').slice(0, 120)
    lines.push(`- chapterIndex=${i + 1} 《${t}》`)
  }
  lines.push(
    '请优先使用 batchGetChapterContents 一次传入多个 chapterIndex；或多次 getChapterContent。归纳 styleAnchors 后再改写下方「待统一初稿」。',
  )
  return lines.join('\n')
}

const MAIN_AGENT_PRESENTER_EXTRA = [
  '你是主稿专家：用户看到的回复正文只能由你通过流式输出给出。',
  '后台各专业写作专家已完成本会话中的结构化步骤，并在用户消息中提供了「内部产出」（含结构化字段与/或正文）。',
  '请严格依据用户问题与这些产出，用自然、清晰的中文作答；结构复杂时可用小标题或列表。',
  '不要向用户提及写作专家、管线、阶段、JSON 或「内部产出」等实现细节；不要整段照抄 JSON。',
  '若产出中已有可交付的正文（如初稿、润色定稿），应在回答中完整呈现该正文，并可酌情加简短辅说明。',
].join('')

const MAIN_AGENT_TRANSITION_SYSTEM =
  '你是主稿专家：用户只能看到你输出的文字。各子步骤在后台静默完成并已汇总到上下文；子步骤的原始 JSON/工具细节对用户不可见。' +
  '请用一两句自然、简短的中文概括刚为用户完成了什么、接下来要做什么；不要输出 JSON、不要复述内部字段名、不要长篇。'

const TRANSITION_MAX_TOKENS = 320

/**
 * 将模型流式结果推到前端；过渡片段不发送 done，最终主答复发送 done。
 */
async function pipeModelStreamToChunks({
  sendChunk,
  signal,
  key,
  apiProvider,
  streamMessages,
  requestParams,
  modelName,
  paramOverrides = {},
  emitDoneWhenFinished,
}) {
  const params = { ...requestParams, model: modelName, ...paramOverrides }
  delete params.tools

  const { stream } =
    apiProvider === 'anthropic'
      ? await anthropicChat.chatStreamAsOpenAIFormat(key, streamMessages, params, signal)
      : await openaiChat.chatStream(key, streamMessages, params, signal)

  let finished = false
  try {
    for await (const chunk of stream) {
      if (signal.aborted) break
      const choice0 = chunk.choices?.[0]
      if (!choice0) continue
      const contentDelta = textFromStreamChoice0(choice0)
      if (contentDelta) {
        sendChunk({ delta: contentDelta })
      }
      const finishReason = choice0.finish_reason
      if (finishReason === 'stop' || finishReason === 'length') {
        finished = true
        if (emitDoneWhenFinished) {
          sendChunk({ done: true, model: modelName })
        }
        return
      }
    }
  } catch (err) {
    if (!signal.aborted) throw err
  }

  if (emitDoneWhenFinished && !finished) {
    sendChunk({ done: true, model: modelName, aborted: signal.aborted })
  }
}

async function streamStageTransition({
  sendChunk,
  signal,
  key,
  apiProvider,
  requestParams,
  messages,
  userText,
  completedStageName,
  nextLine,
  modelName,
}) {
  if (signal.aborted) return
  sendChunk({ subagentBridging: true })
  const baseSystem = (messages.find((m) => m && m.role === 'system')?.content || '').trim()
  const systemContent = [baseSystem, MAIN_AGENT_TRANSITION_SYSTEM].filter(Boolean).join('\n\n')
  const excerpt = userText.length > 600 ? `${userText.slice(0, 600)}…` : userText
  const userContent = [
    `【用户诉求摘要】\n${excerpt}`,
    `\n【刚完成的子步骤】${completedStageName}`,
    nextLine ? `\n【下一步提示（请用自然语气转述，勿照抄本行）】${nextLine}` : '',
    '\n请只写简短过渡说明（建议不超过 120 字）。',
  ].join('')

  const cap = Math.min(
    TRANSITION_MAX_TOKENS,
    typeof requestParams.max_tokens === 'number' && requestParams.max_tokens > 0
      ? requestParams.max_tokens
      : 1024,
  )

  try {
    await pipeModelStreamToChunks({
      sendChunk,
      signal,
      key,
      apiProvider,
      streamMessages: [
        { role: 'system', content: systemContent },
        { role: 'user', content: userContent },
      ],
      requestParams,
      modelName,
      paramOverrides: { max_tokens: cap },
      emitDoneWhenFinished: false,
    })
    if (!signal.aborted) {
      sendChunk({ delta: '\n\n' })
    }
  } finally {
    sendChunk({ subagentBridging: false })
  }
}

/**
 * 各专家阶段全部完成后，由主稿专家流式生成面向用户的唯一可见回复。
 */
async function streamMainAgentPresenter({
  sendChunk,
  signal,
  key,
  apiProvider,
  requestParams,
  messages,
  userText,
  pipelineActionLabel,
  analyzeReport,
  blueprint,
  draftPlain,
  styleUnifyReport,
  /** 风格统一阶段已成功 editChapterContent 写入章节时，主回复中勿重复粘贴该正文 */
  styleSkipFullText,
  reviewIssues,
  polishFinalText,
  polishChangeSummary,
  /** 润色阶段已成功 editChapterContent 写入章节时，不在对话中再要求输出润色全文 */
  polishSkipFullText,
  modelName,
}) {
  const baseSystem = (messages.find((m) => m && m.role === 'system')?.content || '').trim()
  const polishSavedHint =
    polishSkipFullText === true
      ? '润色结果已通过工具写入当前章节：回复中不要重复粘贴润色后的全文，用一两句说明已保存即可，可结合下方「修订摘要」简述改动。'
      : ''
  const styleSavedHint =
    styleSkipFullText === true
      ? '风格统一后的正文已通过工具写入当前章节：回复中不要重复粘贴该正文，用一两句说明已保存即可，可结合「文风锚点」或「风格修订摘要」简述。'
      : ''
  const systemContent = [
    baseSystem,
    MAIN_AGENT_PRESENTER_EXTRA,
    polishSavedHint,
    styleSavedHint,
    `本轮管线动作：${String(pipelineActionLabel || '')}。`,
  ]
    .filter(Boolean)
    .join('\n\n')

  const parts = []
  parts.push(`【用户问题】\n${userText}`)
  parts.push('\n【内部产出 — 仅供你组织给用户的最终回复】\n')
  if (analyzeReport && typeof analyzeReport === 'object' && Object.keys(analyzeReport).length) {
    parts.push('\n## 分析报告（结构化）\n')
    parts.push(JSON.stringify(analyzeReport, null, 2))
  }
  if (blueprint && typeof blueprint === 'object' && Object.keys(blueprint).length) {
    parts.push('\n## 写作蓝图（结构化）\n')
    parts.push(JSON.stringify(blueprint, null, 2))
  }
  const sur = styleUnifyReport && typeof styleUnifyReport === 'object' ? styleUnifyReport : null
  const sa = String(sur?.styleAnchors || '').trim()
  if (sa) {
    parts.push('\n## 文风锚点（摘录）\n')
    parts.push(sa)
  }
  const csStyle = String(sur?.changeSummary || '').trim()
  if (csStyle) {
    parts.push('\n## 风格修订摘要\n')
    parts.push(csStyle)
  }
  const dp = String(draftPlain || '').trim()
  if (dp) {
    if (styleSkipFullText === true) {
      parts.push('\n## 正文（风格统一后）\n')
      parts.push('（该正文已由风格统一阶段写入当前章节，请勿在对话中再次输出全文。）\n')
    } else {
      parts.push('\n## 初稿正文\n')
      parts.push(dp)
    }
  }
  if (Array.isArray(reviewIssues) && reviewIssues.length) {
    parts.push('\n## 审校问题列表（结构化）\n')
    parts.push(JSON.stringify(reviewIssues, null, 2))
  }
  const pf = String(polishFinalText || '').trim()
  if (polishSkipFullText === true) {
    parts.push('\n## 润色定稿\n')
    parts.push('（正文已由润色阶段写入当前章节，请勿在对话中再次输出全文。）\n')
  } else if (pf) {
    parts.push('\n## 润色定稿（若存在，应在回复中完整呈现）\n')
    parts.push(pf)
  }
  const ps = String(polishChangeSummary || '').trim()
  if (ps) {
    parts.push('\n## 修订摘要\n')
    parts.push(ps)
  }

  const presenterMessages = [
    { role: 'system', content: systemContent },
    { role: 'user', content: parts.join('\n') },
  ]

  sendChunk({ subagentMainPresenter: true })
  await pipeModelStreamToChunks({
    sendChunk,
    signal,
    key,
    apiProvider,
    streamMessages: presenterMessages,
    requestParams,
    modelName,
    paramOverrides: {},
    emitDoneWhenFinished: true,
  })
}

/**
 * Subagent 上下文契约（主进程侧「明确传入」的内容）
 *
 * 1) `messages`（来自渲染进程，与本轮主链路入口一致）
 *    - `system`：用户 systemPrompt + 书籍/章节说明 +（写作专家模式下）前端拼接的「关联章节/大纲」短说明
 *    随后各阶段再把本文件生成的 **toolCtx 附录**、**阶段 systemPrompt** 接在同一跳 system 里，顺序为：
 *    `原 system` → `buildSubagentToolingContextAppendix(toolCtx)` → `SUBAGENT_REGISTRY[stage].systemPrompt`
 *    - `user` / `assistant`：最近多轮纯文本（渲染进程对「仅工具、无正文」的助手轮会用 toolCallSegments 合成摘要）
 *    ⚠ 不含：`role: tool` 消息及历史工具返回 JSON；若需与 legacy 主循环逐字一致，须另行持久化或注入。
 *
 * 2) `toolCtx`（main.js 构造，toolExecutor / 编排器与写作专家管线共用）
 *    - bookId, chapterId, currentChapterTitle, writingChapters, availableOutlines
 *    - associatedChapterIds, associatedOutlineIds（IPC 自界面关联）
 *    各阶段执行前会合并 `subagentAllowedToolNames`。
 *
 * 3) `latestUserTextForPlan`
 *    - 主链路在开启工具路由时为 `buildToolRouterEmbeddingQuery(messages)` 的检索串；编排器 plan/repair 使用。
 *
 * 4) 工具列表（与 legacy 主链路同源，均来自 toolRouter 扫描 electron/skills 下各工具目录的 SKILL.md）
 *    - 开启路由：`toolRouter.getToolsForQuery(...)` → `buildToolSchemas` 与主进程一致。
 *    - 回退全量：`toOpenAiTools(toolRouter.getApiSkillItems())` 再按阶段 `filterToolSchemasByNames`。
 *    - `getToolsForStage` 使用 `buildSubagentStageRouterQuery(messages, 阶段 user)`，避免向量锚点仅绑阶段指令。
 *
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
 * @param {string[]} [input.agentActions] 多选阶段，如 ['analyze','plan','draft']
 * @param {string} input.model
 */
async function runSubagentPipeline(input) {
  const {
    sendChunk,
    signal,
    key,
    apiProvider,
    requestParams: rawRequestParams,
    toolCtx,
    skillSpecs,
    messages,
    latestUserTextForPlan,
    useToolRouter,
    toolsFromFront,
    agentActions,
    model,
  } = input
  /** 写作专家管线整段禁用思考扩展，避免各阶段/主稿专家流式把推理内容推到对话 UI */
  const requestParams = { ...rawRequestParams, thinking: { type: 'disabled' } }
  const toolingContextAppendix = buildSubagentToolingContextAppendix(toolCtx)

  const getToolsForStage = async (stage, pipelineMessages, stageUserContent) => {
    const allowedSet = new Set(buildToolPermissionsForStage(stage))
    let candidateTools = Array.isArray(toolsFromFront) ? toolsFromFront : []
    if (
      useToolRouter &&
      toolCtx.bookId != null &&
      Array.isArray(pipelineMessages) &&
      pipelineMessages.length > 0
    ) {
      try {
        const routed = await toolRouter.getToolsForQuery(
          buildSubagentStageRouterQuery(pipelineMessages, stageUserContent),
        )
        candidateTools = routed.tools || []
        if (Array.isArray(routed.warnings) && routed.warnings.length > 0) {
          sendChunk({ toolRouterWarning: routed.warnings.join(' ') })
        }
      } catch (err) {
        sendChunk({ toolRouterWarning: `工具路由调用失败，${(err && err.message) || String(err)}` })
      }
    } else if ((!candidateTools || candidateTools.length === 0) && allowedSet.size > 0) {
      const allTools = toOpenAiTools(toolRouter.getApiSkillItems())
      candidateTools = filterToolSchemasByNames(allTools, allowedSet)
    }
    candidateTools = ensureStageCandidateTools(candidateTools, allowedSet)
    return filterToolSchemasByNames(candidateTools, allowedSet)
  }

  const chatNoStreamUnified = async (msgs, stageTools) => {
    const stageParams = { ...requestParams, tools: stageTools }
    if (apiProvider === 'anthropic') {
      return anthropicChat.chatNoStreamAsOpenAIFormat(key, msgs, stageParams, signal)
    }
    return openaiChat.chatNoStream(key, msgs, stageParams, signal)
  }

  function markEditChapterSavedFromResults(usedCalls, toolResults) {
    for (const tc of usedCalls || []) {
      if (tc?.function?.name !== 'editChapterContent') continue
      const tr = (toolResults || []).find((r) => r.tool_call_id === tc.id)
      if (!tr?.content) continue
      try {
        const o = JSON.parse(tr.content)
        if (o && o.success === true) return true
      } catch {
        /* ignore */
      }
    }
    return false
  }

  const runNonStreamToolLoop = async (currentMessages, stageTools, stageName) => {
    const allowedNames = new Set(buildToolPermissionsForStage(stageName))
    const toolCtxWithAllow = { ...toolCtx, subagentAllowedToolNames: allowedNames }

    let loopMessages = currentMessages
    let finalText = ''
    let editChapterSaved = false
    for (let guard = 0; guard < 6; guard++) {
      if (signal.aborted) break
      const { message } = await chatNoStreamUnified(loopMessages, stageTools)
      const content = String(message?.content || '')
      if (content) finalText = content
      const list = normalizeToolCallsList(Array.isArray(message?.tool_calls) ? message.tool_calls : [])
      if (list.length === 0) {
        return { text: finalText, thinking: '', editChapterSaved }
      }
      const plan = skillOrchestrator.planToolCalls({
        toolCalls: list,
        skillSpecs,
        toolCtx: toolCtxWithAllow,
        latestUserText: latestUserTextForPlan,
      })
      const executableCalls = plan.executableCalls || list
      /** 向气泡同步工具调用状态（ToolCallStatus）；不推送 partialContent，各专家模型正文仍仅由主稿专家过渡/终稿流式呈现 */
      sendChunk({
        toolCalls: executableCalls,
        toolCallsInProgress: true,
        partialContent: '',
        partialThinking: '',
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
            if (Array.isArray(ev.toolReadCacheMask)) sendChunk({ toolReadCacheMask: ev.toolReadCacheMask })
            if (typeof ev.toolIndexCompleted === 'number') {
              sendChunk({
                toolIndexCompleted: ev.toolIndexCompleted,
                ...(ev.toolFromCache === true ? { toolFromCache: true } : {}),
              })
            }
          }),
      })
      const toolResults = executed.toolResults || []
      const usedCalls = executed.toolCalls || executableCalls
      if (markEditChapterSavedFromResults(usedCalls, toolResults)) {
        editChapterSaved = true
      }
      const assistantMsg = {
        role: 'assistant',
        content: finalText,
        tool_calls: usedCalls.map((tc) => ({
          id: tc.id,
          type: 'function',
          function: { name: tc.function.name, arguments: tc.function.arguments },
        })),
      }
      const toolMsgs = toolResults.map((r) => ({ role: 'tool', tool_call_id: r.tool_call_id, content: r.content }))
      loopMessages = [...loopMessages, assistantMsg, ...toolMsgs]

      const allChapterBodyTools =
        usedCalls.length > 0 &&
        usedCalls.every((tc) => CHAPTER_CATALOG_BODY_TOOL_NAMES.has(tc.function?.name))
      const allChapterBodyFailed =
        allChapterBodyTools &&
        toolResults.length === usedCalls.length &&
        toolResults.every((r) => toolResultContentLooksLikeJsonError(r.content))
      if (allChapterBodyFailed) {
        return { text: finalText, thinking: '', editChapterSaved }
      }
    }
    return { text: finalText, thinking: '', editChapterSaved }
  }

  const runStage = async (stage, userContent) => {
    const stageCfg = SUBAGENT_REGISTRY[stage]
    const baseSystem = (messages.find((m) => m && m.role === 'system')?.content || '').trim()
    const stageSystem = [baseSystem, toolingContextAppendix, stageCfg.systemPrompt].filter(Boolean).join('\n\n')
    const stageMessages = [
      { role: 'system', content: stageSystem },
      ...messages.filter((m) => m && m.role !== 'system'),
      { role: 'user', content: userContent },
    ]
    sendChunk({ subagentStage: stage, subagentStageName: stageCfg.name, subagentStageStarting: true })
    /** 工具路由：主会话 messages 与阶段任务串联，避免把阶段 JSON 指令当作「当前提问」量 embedding */
    const stageTools = await getToolsForStage(stage, messages, userContent)
    const stageRes = await runNonStreamToolLoop(stageMessages, stageTools, stage)
    sendChunk({ subagentStageDone: stage, subagentStageName: stageCfg.name })
    const parsed = extractStructuredJsonFromModelText(stageRes.text)
    return {
      rawText: stageRes.text,
      parsed,
      editChapterSaved: Boolean(stageRes.editChapterSaved),
    }
  }

  const latestUser = [...messages].reverse().find((m) => m && m.role === 'user')
  const userText = String(latestUser?.content || '')

  const {
    needPlan,
    needDraft,
    needStyleUnify,
    needReview,
    needPolish,
    onlyDraft,
    resolveAction,
    pipelineActionLabel,
  } = normalizePipelineFlags(agentActions)

  const analyzeInput = `请输出 AnalyzeReport JSON（仅 JSON 对象，字段：summary, goals, constraints, risks, evidence）。\n用户请求：${userText}`
  const analyzeRes = await runStage(STAGES.ANALYZE, analyzeInput)
  const analyzeReport = normalizeAnalyzeReport(analyzeRes.parsed, userText)
  sendChunk({ subagentStage: STAGES.ANALYZE, subagentPayload: analyzeReport })

  let hintAfterAnalyze = '即将由主稿专家汇总本轮结果。'
  if (needPlan) hintAfterAnalyze = '接下来将进行写作规划。'
  else if (needDraft) hintAfterAnalyze = '接下来将处理正文与后续步骤。'
  await streamStageTransition({
    sendChunk,
    signal,
    key,
    apiProvider,
    requestParams,
    messages,
    userText,
    completedStageName: SUBAGENT_REGISTRY[STAGES.ANALYZE].name,
    nextLine: hintAfterAnalyze,
    modelName: model,
  })

  let blueprint = null
  let draft = null
  let reviewIssues = []

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
    let hintAfterPlan = '即将由主稿专家汇总。'
    if (needDraft) hintAfterPlan = '接下来将撰写初稿正文。'
    await streamStageTransition({
      sendChunk,
      signal,
      key,
      apiProvider,
      requestParams,
      messages,
      userText,
      completedStageName: SUBAGENT_REGISTRY[STAGES.PLAN].name,
      nextLine: hintAfterPlan,
      modelName: model,
    })
  }

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
    let hintAfterDraft = '即将由主稿专家汇总。'
    if (needStyleUnify) hintAfterDraft = '接下来将参照前文 3～5 章统一文风。'
    else if (needReview) hintAfterDraft = '接下来将审校正文。'
    else if (onlyDraft) hintAfterDraft = '接下来由主稿专家为你整理并呈现初稿。'
    await streamStageTransition({
      sendChunk,
      signal,
      key,
      apiProvider,
      requestParams,
      messages,
      userText,
      completedStageName: SUBAGENT_REGISTRY[STAGES.DRAFT].name,
      nextLine: hintAfterDraft,
      modelName: model,
    })
  }

  let draftPlain = resolveDraftPlainText(draft, userText, resolveAction)
  let styleUnifyReport = null
  let styleSkipFullText = false

  if (needStyleUnify && String(draftPlain || '').trim()) {
    const priorHint = buildStyleUnifyPriorChapterHint(toolCtx)
    const styleInput = [
      '请输出 StyleUnifyResult JSON（仅 JSON），字段：',
      'styleAnchors（string：从前文归纳的人称/时态/节奏/句式与用语习惯，勿大段粘贴原文）、',
      'content（string：对齐文风后的当前章完整正文；须保留初稿剧情与人设，仅做叙述层面统一）、',
      'changeSummary（string：相对「待统一初稿」的修改说明）、',
      'priorChaptersRead（可选 array，每项含 chapterIndex、title，标明实际参照的章节）。',
      '必须先 listWritingChapters，再按宿主列表用 batchGetChapterContents 或 getChapterContent 读取前文；不得跳过读前文直接臆造风格。',
      '若需写回当前章：在输出 JSON 前调用 editChapterContent；成功后 JSON 中 content 可短占位。',
      priorHint,
      '\n待统一初稿：\n',
      draftPlain,
    ].join('\n')
    const styleRes = await runStage(STAGES.STYLE_UNIFY, styleInput)
    styleUnifyReport = normalizeStyleUnifyResult(styleRes.parsed)
    styleSkipFullText = Boolean(styleRes.editChapterSaved)
    const merged = String(styleUnifyReport.content || '').trim()
    if (merged) draftPlain = merged
    sendChunk({
      subagentStage: STAGES.STYLE_UNIFY,
      subagentPayloadMeta: {
        unifiedLength: String(draftPlain || '').length,
        priorCount: (styleUnifyReport.priorChaptersRead || []).length,
      },
    })
    let hintAfterStyle = '接下来由主稿专家整合并正式回复你。'
    if (needReview) hintAfterStyle = '接下来将审校正文。'
    await streamStageTransition({
      sendChunk,
      signal,
      key,
      apiProvider,
      requestParams,
      messages,
      userText,
      completedStageName: SUBAGENT_REGISTRY[STAGES.STYLE_UNIFY].name,
      nextLine: hintAfterStyle,
      modelName: model,
    })
  }

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
    let hintAfterReview = '接下来由主稿专家汇总审校结果。'
    if (needPolish) hintAfterReview = '接下来将进行润色定稿。'
    await streamStageTransition({
      sendChunk,
      signal,
      key,
      apiProvider,
      requestParams,
      messages,
      userText,
      completedStageName: SUBAGENT_REGISTRY[STAGES.REVIEW].name,
      nextLine: hintAfterReview,
      modelName: model,
    })
  }

  let polishFinalText = ''
  let polishChangeSummary = ''
  let polishSkipFullText = false
  if (needPolish) {
    const polishInput = [
      '请输出 PolishedResult JSON（仅 JSON），字段：finalText、changeSummary。',
      '若需将润色结果保存到左侧当前写作章节：在输出 JSON 之前先调用 editChapterContent，参数 content 为润色后的完整正文（chapterId/chapterTitle 可省略，由宿主按当前章注入）。',
      '若已成功调用 editChapterContent，JSON 中 finalText 可填简短占位或空串（正文以已写入编辑器为准），changeSummary 仍须简要说明改动。',
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
    polishSkipFullText = Boolean(polishRes.editChapterSaved)
    polishFinalText = String(p.finalText || draftPlain || '')
    polishChangeSummary = String(p.changeSummary || '')
    await streamStageTransition({
      sendChunk,
      signal,
      key,
      apiProvider,
      requestParams,
      messages,
      userText,
      completedStageName: SUBAGENT_REGISTRY[STAGES.POLISH].name,
      nextLine: '接下来由主稿专家整合全文并正式回复你。',
      modelName: model,
    })
  }

  await streamMainAgentPresenter({
    sendChunk,
    signal,
    key,
    apiProvider,
    requestParams,
    messages,
    userText,
    pipelineActionLabel,
    analyzeReport,
    blueprint,
    draftPlain,
    styleUnifyReport,
    styleSkipFullText,
    reviewIssues,
    polishFinalText,
    polishChangeSummary,
    polishSkipFullText,
    modelName: model,
  })
}

module.exports = {
  runSubagentPipeline,
  filterToolSchemasByNames,
  splitDraftIntoSegments,
  extractContextAroundSpan,
}

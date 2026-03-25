const STAGES = {
  ANALYZE: 'analyze',
  PLAN: 'plan',
  DRAFT: 'draft',
  REVIEW: 'review',
  POLISH: 'polish',
}

const EXEC_ACTIONS = {
  ANALYZE: 'analyze',
  PLAN: 'plan',
  DRAFT: 'draft',
  REVIEW: 'review',
  POLISH: 'polish',
  FULL: 'full',
}

const SHARED_TOOL_NAMES = [
  'listWritingChapters',
  'getChapterContent',
  'batchGetChapterContents',
  'getBookCharacters',
  'listBookCharacters',
  'getStoryBackground',
  'getGlobalOutline',
  'queryOutline',
  'listOutlines',
  'searchMemories',
]

const AGENT_EXCLUSIVE_TOOL_NAMES = {
  [STAGES.ANALYZE]: ['addMemory'],
  [STAGES.PLAN]: ['addMemory'],
  [STAGES.DRAFT]: ['editChapterContent', 'addForeshadowing', 'addMemory'],
  [STAGES.REVIEW]: ['addMemory'],
  [STAGES.POLISH]: ['editChapterContent', 'addMemory'],
}

const SUBAGENT_REGISTRY = {
  [STAGES.ANALYZE]: {
    name: '分析代理',
    outputType: 'AnalyzeReport',
    systemPrompt:
      '你是小说写作“分析代理”。你需要拆解用户目标、约束和风险，并给出可执行结论。允许调用工具补全证据。输出必须是 JSON，且只包含约定字段。',
  },
  [STAGES.PLAN]: {
    name: '规划代理',
    outputType: 'WritingBlueprint',
    systemPrompt:
      '你是小说写作“规划代理”。基于 AnalyzeReport 生成可执行蓝图，不要重复长素材原文。允许调用工具补齐缺失信息。输出必须是 JSON，且只包含约定字段。',
  },
  [STAGES.DRAFT]: {
    name: '写作代理',
    outputType: 'DraftDocument',
    systemPrompt:
      '你是小说写作“写作代理”。基于蓝图写出完整初稿。允许调用工具检索素材与落库。输出必须是 JSON，且只包含约定字段。',
  },
  [STAGES.REVIEW]: {
    name: '审校代理',
    outputType: 'ReviewIssues',
    systemPrompt:
      '你是小说写作“审校代理”。只做问题定位与建议，不改写全文。审校按分段输入处理。允许调用工具辅助核对设定。输出必须是 JSON，且只包含约定字段。',
  },
  [STAGES.POLISH]: {
    name: '润色代理',
    outputType: 'PolishedResult',
    systemPrompt:
      '你是小说写作“润色代理”。基于问题点位与局部上下文给出修订结果，不应依赖全文。允许调用工具做必要同步。输出必须是 JSON，且只包含约定字段。',
  },
}

function buildToolPermissionsForStage(stage) {
  const exclusive = AGENT_EXCLUSIVE_TOOL_NAMES[stage] || []
  return Array.from(new Set([...SHARED_TOOL_NAMES, ...exclusive]))
}

function parseJsonSafe(text, fallback = null) {
  try {
    return JSON.parse(String(text || ''))
  } catch (_) {
    return fallback
  }
}

/**
 * 从模型回复中提取 JSON（裸 JSON、markdown 代码块、或首尾包裹的对象/数组）。
 * 审校阶段可能返回数组，其它阶段多为对象。
 * @param {string} text
 * @returns {object|Array|null}
 */
function extractStructuredJsonFromModelText(text) {
  const s = String(text || '').trim()
  if (!s) return null
  try {
    return JSON.parse(s)
  } catch (_) {
    /* continue */
  }
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence) {
    const inner = String(fence[1] || '').trim()
    try {
      return JSON.parse(inner)
    } catch (_) {
      /* continue */
    }
  }
  const objStart = s.indexOf('{')
  const objEnd = s.lastIndexOf('}')
  if (objStart >= 0 && objEnd > objStart) {
    try {
      return JSON.parse(s.slice(objStart, objEnd + 1))
    } catch (_) {
      /* continue */
    }
  }
  const arrStart = s.indexOf('[')
  const arrEnd = s.lastIndexOf(']')
  if (arrStart >= 0 && arrEnd > arrStart) {
    try {
      return JSON.parse(s.slice(arrStart, arrEnd + 1))
    } catch (_) {
      /* continue */
    }
  }
  return null
}

function normalizeAnalyzeReport(raw, fallbackUserText) {
  const val = raw && typeof raw === 'object' ? raw : {}
  return {
    summary: String(val.summary || fallbackUserText || '').slice(0, 1200),
    goals: Array.isArray(val.goals) ? val.goals.map((x) => String(x)).slice(0, 12) : [],
    constraints: Array.isArray(val.constraints) ? val.constraints.map((x) => String(x)).slice(0, 12) : [],
    risks: Array.isArray(val.risks) ? val.risks.map((x) => String(x)).slice(0, 12) : [],
    evidence: Array.isArray(val.evidence)
      ? val.evidence
          .map((x) => ({
            source: String(x?.source || ''),
            snippet: String(x?.snippet || '').slice(0, 400),
          }))
          .slice(0, 12)
      : [],
  }
}

function normalizeWritingBlueprint(raw) {
  const val = raw && typeof raw === 'object' ? raw : {}
  return {
    chapterGoal: String(val.chapterGoal || '').slice(0, 1000),
    beats: Array.isArray(val.beats) ? val.beats.map((x) => String(x)).slice(0, 20) : [],
    tone: String(val.tone || '').slice(0, 200),
    constraints: Array.isArray(val.constraints) ? val.constraints.map((x) => String(x)).slice(0, 20) : [],
    requiredMaterials: Array.isArray(val.requiredMaterials)
      ? val.requiredMaterials
          .map((x) => ({
            type: String(x?.type || ''),
            ref: String(x?.ref || ''),
            note: String(x?.note || '').slice(0, 300),
          }))
          .slice(0, 40)
      : [],
  }
}

function normalizeDraftDocument(raw) {
  const val = raw && typeof raw === 'object' ? raw : {}
  return {
    title: String(val.title || ''),
    content: String(val.content || ''),
    notes: Array.isArray(val.notes) ? val.notes.map((x) => String(x)).slice(0, 12) : [],
  }
}

function normalizeReviewIssues(raw) {
  let list = []
  if (Array.isArray(raw)) {
    list = raw
  } else if (raw && typeof raw === 'object' && Array.isArray(raw.issues)) {
    list = raw.issues
  }
  return list
    .map((x) => ({
      segmentIndex: Number.isFinite(Number(x?.segmentIndex)) ? Number(x.segmentIndex) : 0,
      span: String(x?.span || '').slice(0, 200),
      issueType: String(x?.issueType || 'general').slice(0, 80),
      severity: String(x?.severity || 'medium').slice(0, 20),
      suggestion: String(x?.suggestion || '').slice(0, 600),
      context: String(x?.context || '').slice(0, 500),
    }))
    .slice(0, 120)
}

function validateSubagentConfig() {
  const stageKeys = Object.values(STAGES)
  for (const key of stageKeys) {
    if (!SUBAGENT_REGISTRY[key]) {
      throw new Error(`subagent registry 缺少阶段: ${key}`)
    }
    const tools = buildToolPermissionsForStage(key)
    if (!Array.isArray(tools) || tools.length === 0) {
      throw new Error(`subagent 工具权限为空: ${key}`)
    }
  }
  return true
}

module.exports = {
  STAGES,
  EXEC_ACTIONS,
  SUBAGENT_REGISTRY,
  SHARED_TOOL_NAMES,
  AGENT_EXCLUSIVE_TOOL_NAMES,
  buildToolPermissionsForStage,
  parseJsonSafe,
  extractStructuredJsonFromModelText,
  normalizeAnalyzeReport,
  normalizeWritingBlueprint,
  normalizeDraftDocument,
  normalizeReviewIssues,
  validateSubagentConfig,
}

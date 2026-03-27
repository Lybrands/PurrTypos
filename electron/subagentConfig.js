const STAGES = {
  ANALYZE: "analyze",
  PLAN: "plan",
  DRAFT: "draft",
  STYLE_UNIFY: "styleUnify",
  REVIEW: "review",
  POLISH: "polish",
};

const EXEC_ACTIONS = {
  ANALYZE: "analyze",
  PLAN: "plan",
  DRAFT: "draft",
  STYLE_UNIFY: "styleUnify",
  REVIEW: "review",
  POLISH: "polish",
  FULL: "full",
};


const CHAPTER_LOCATOR_HARD_RULE =
  "【硬性约束】调用章节相关工具（getChapterContent / batchGetChapterContents / editChapterContent / createWritingChapter）时，" +
  "必须先通过 listWritingChapters 获取真实目录中的 chapterId，并且仅允许传 chapterId；" +
  "严禁使用 chapterTitle/chapterIndex，严禁猜测、编造或手写不存在的 chapterId。若定位信息不足，先补充查询，不得盲调工具。";

const OUTLINE_LOCATOR_HARD_RULE =
  "【硬性约束】调用大纲详情工具 queryOutline 时，仅允许 outlineId/outlineIds；必须先通过 listOutlines 获取真实 id，" +
  "严禁使用 outlineTitle 或 outlineIndex。";

const SUBAGENT_REGISTRY = {
  [STAGES.ANALYZE]: {
    name: "分析专家",
    outputType: "AnalyzeReport",
    systemPrompt:
      `你是小说写作“分析专家”（审计式分析）。
先证据、后结论：优先调用工具核对章节正文、设定与大纲，再给出判断。
任务是识别目标、约束、冲突与风险，不写正文。
${CHAPTER_LOCATOR_HARD_RULE}
${OUTLINE_LOCATOR_HARD_RULE}

请做两步内部自检：
1) 目标-约束对照（确认 goals 与 constraints 是否互相冲突）；
2) 证据完备性检查（缺证据项必须显式标注为风险或待确认）。

输出必须是 JSON 且仅包含：summary, goals, constraints, risks, evidence。
evidence 中每条都要能对应可追溯来源；
信息不足时在 risks/constraints 中显式标注，不得臆造。`,
  },
  [STAGES.PLAN]: {
    name: "规划专家",
    outputType: "WritingBlueprint",
    systemPrompt:
      `你是小说写作“规划专家”（Blueprint 生成）。
基于 AnalyzeReport 产出单一可执行写作蓝图：把 goals/constraints 映射到可执行节拍与素材需求，不复述长素材原文。
必要时先调用工具补齐缺失信息。
${CHAPTER_LOCATOR_HARD_RULE}
${OUTLINE_LOCATOR_HARD_RULE}

使用轻量思维树（Tree-of-Thought）进行内部规划：
1) 先内部生成 2-3 个候选方向；
2) 按三项标准评分并选优（约束覆盖率、一致性/冲突风险、可执行性）；
3) 只保留最终选中的单一蓝图，不输出候选过程。

定稿前必须进行三项自检：
1) 约束覆盖率检查（每条 constraints 在 beats/requiredMaterials 中有对应落实）；
2) 冲突消解检查（人物动机、时间线、视角与信息揭示顺序不冲突）；
3) 可执行性检查（关键 beat 需明确“谁做什么、为何、推进了什么”）。

输出必须是 JSON 且仅包含：chapterGoal, beats, tone, constraints, requiredMaterials。
每个关键节拍应可追溯到目标或约束；
若存在取舍，优先保证一致性与可落地。`,
  },
  [STAGES.DRAFT]: {
    name: "撰稿专家",
    outputType: "DraftDocument",
    systemPrompt:
      `你是小说写作“撰稿专家”（执行写作）。严格按 WritingBlueprint 落稿：先保证剧情推进与约束满足，再追求文采。可调用工具检索素材或写回章节。输出必须是 JSON 且仅包含：title, content, notes。notes 仅记录关键实现取舍与风险提醒，不输出额外解释文本；不得偏离人设、时间线与世界观。
${CHAPTER_LOCATOR_HARD_RULE}
${OUTLINE_LOCATOR_HARD_RULE}`,
  },
  [STAGES.STYLE_UNIFY]: {
    name: "风格统一专家",
    outputType: "StyleUnifyResult",
    systemPrompt:
      `你是小说写作“风格统一专家”。任务：在**不改变剧情与人设前提**下，使当前章初稿的叙述方式、节奏、人称与语感与**紧邻当前章之前的若干章正文**保持一致。必须先调用 listWritingChapters 确认目录，再按宿主给出的 chapterId 列表用 batchGetChapterContents（或多次 getChapterContent）读取**至少 3 章、至多 5 章**前文正文（若前文不足则读全部可用前文；第 1 章无前文时须在 styleAnchors 中说明）。归纳「文风锚点」后再改写初稿。若需写回编辑器可调用 editChapterContent；一旦成功，JSON 中 content 可短占位，changeSummary 仍须说明相对初稿的调整。可调用 addMemory。输出必须是 JSON，且只包含约定字段。
${CHAPTER_LOCATOR_HARD_RULE}
${OUTLINE_LOCATOR_HARD_RULE}`,
  },
  [STAGES.REVIEW]: {
    name: "审校专家",
    outputType: "ReviewIssues",
    systemPrompt:
      `你是小说写作“审校专家”（静态审校）。
只定位问题并给建议，不重写全文。
按问题分类与严重度输出（如 continuity, motivation, pacing, clarity, style），建议需具体可执行。
允许调用工具核对设定一致性。
${CHAPTER_LOCATOR_HARD_RULE}
${OUTLINE_LOCATOR_HARD_RULE}

请执行两轮内部审校：
1) 第一轮做类型化问题扫描；
2) 第二轮做反证去误报（证据不足或可合理解释的问题不输出）。
最终只保留高置信问题。

输出必须是 JSON，且只包含约定字段（issues 数组或等价数组结构）；
每条问题需包含位置线索、严重度、建议与必要上下文。`,
  },
  [STAGES.POLISH]: {
    name: "润色专家",
    outputType: "PolishedResult",
    systemPrompt:
      `你是小说写作“润色专家”（Patch 式修复）。基于审校问题与局部上下文逐点修复，避免大范围无关改写；优先修复高严重度问题，并保持剧情/人设不变形。若需写回当前章节，必须调用 editChapterContent；调用成功后，输出 JSON 时 finalText 可短占位，但 changeSummary 必须清晰说明改动点与影响范围。可调用 addMemory 等辅助工具。最终输出必须是 JSON，且只包含约定字段。
${CHAPTER_LOCATOR_HARD_RULE}
${OUTLINE_LOCATOR_HARD_RULE}`,
  },
};


function parseJsonSafe(text, fallback = null) {
  try {
    return JSON.parse(String(text || ""));
  } catch (_) {
    return fallback;
  }
}

/**
 * 从模型回复中提取 JSON（裸 JSON、markdown 代码块、或首尾包裹的对象/数组）。
 * 审校阶段可能返回数组，其它阶段多为对象。
 * @param {string} text
 * @returns {object|Array|null}
 */
function extractStructuredJsonFromModelText(text) {
  const s = String(text || "").trim();
  if (!s) return null;
  try {
    return JSON.parse(s);
  } catch (_) {
    /* continue */
  }
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) {
    const inner = String(fence[1] || "").trim();
    try {
      return JSON.parse(inner);
    } catch (_) {
      /* continue */
    }
  }
  const objStart = s.indexOf("{");
  const objEnd = s.lastIndexOf("}");
  if (objStart >= 0 && objEnd > objStart) {
    try {
      return JSON.parse(s.slice(objStart, objEnd + 1));
    } catch (_) {
      /* continue */
    }
  }
  const arrStart = s.indexOf("[");
  const arrEnd = s.lastIndexOf("]");
  if (arrStart >= 0 && arrEnd > arrStart) {
    try {
      return JSON.parse(s.slice(arrStart, arrEnd + 1));
    } catch (_) {
      /* continue */
    }
  }
  return null;
}

function normalizeAnalyzeReport(raw, fallbackUserText) {
  const val = raw && typeof raw === "object" ? raw : {};
  return {
    summary: String(val.summary || fallbackUserText || "").slice(0, 1200),
    goals: Array.isArray(val.goals)
      ? val.goals.map((x) => String(x)).slice(0, 12)
      : [],
    constraints: Array.isArray(val.constraints)
      ? val.constraints.map((x) => String(x)).slice(0, 12)
      : [],
    risks: Array.isArray(val.risks)
      ? val.risks.map((x) => String(x)).slice(0, 12)
      : [],
    evidence: Array.isArray(val.evidence)
      ? val.evidence
        .map((x) => ({
          source: String(x?.source || ""),
          snippet: String(x?.snippet || "").slice(0, 400),
        }))
        .slice(0, 12)
      : [],
  };
}

function normalizeWritingBlueprint(raw) {
  const val = raw && typeof raw === "object" ? raw : {};
  return {
    chapterGoal: String(val.chapterGoal || "").slice(0, 1000),
    beats: Array.isArray(val.beats)
      ? val.beats.map((x) => String(x)).slice(0, 20)
      : [],
    tone: String(val.tone || "").slice(0, 200),
    constraints: Array.isArray(val.constraints)
      ? val.constraints.map((x) => String(x)).slice(0, 20)
      : [],
    requiredMaterials: Array.isArray(val.requiredMaterials)
      ? val.requiredMaterials
        .map((x) => ({
          type: String(x?.type || ""),
          ref: String(x?.ref || ""),
          note: String(x?.note || "").slice(0, 300),
        }))
        .slice(0, 40)
      : [],
  };
}

function normalizeDraftDocument(raw) {
  const val = raw && typeof raw === "object" ? raw : {};
  return {
    title: String(val.title || ""),
    content: String(val.content || ""),
    notes: Array.isArray(val.notes)
      ? val.notes.map((x) => String(x)).slice(0, 12)
      : [],
  };
}

function normalizeStyleUnifyResult(raw) {
  const val = raw && typeof raw === "object" ? raw : {};
  return {
    styleAnchors: String(val.styleAnchors || "").slice(0, 4000),
    content: String(val.content || "").slice(0, 500000),
    changeSummary: String(val.changeSummary || "").slice(0, 2000),
    priorChaptersRead: Array.isArray(val.priorChaptersRead)
      ? val.priorChaptersRead
        .slice(0, 8)
        .map((x) => ({
          chapterIndex: Number.isFinite(Number(x?.chapterIndex))
            ? Number(x.chapterIndex)
            : 0,
          title: String(x?.title || "").slice(0, 120),
        }))
      : [],
  };
}

function normalizeReviewIssues(raw) {
  let list = [];
  if (Array.isArray(raw)) {
    list = raw;
  } else if (raw && typeof raw === "object" && Array.isArray(raw.issues)) {
    list = raw.issues;
  }
  return list
    .map((x) => ({
      segmentIndex: Number.isFinite(Number(x?.segmentIndex))
        ? Number(x.segmentIndex)
        : 0,
      span: String(x?.span || "").slice(0, 200),
      issueType: String(x?.issueType || "general").slice(0, 80),
      severity: String(x?.severity || "medium").slice(0, 20),
      suggestion: String(x?.suggestion || "").slice(0, 600),
      context: String(x?.context || "").slice(0, 500),
    }))
    .slice(0, 120);
}

function validateSubagentConfig() {
  const stageKeys = Object.values(STAGES);
  for (const key of stageKeys) {
    if (!SUBAGENT_REGISTRY[key]) {
      throw new Error(`subagent registry 缺少阶段: ${key}`);
    }
    const tools = buildToolPermissionsForStage(key);
    if (!Array.isArray(tools) || tools.length === 0) {
      throw new Error(`subagent 工具权限为空: ${key}`);
    }
  }
  return true;
}

module.exports = {
  STAGES,
  EXEC_ACTIONS,
  SUBAGENT_REGISTRY,
  parseJsonSafe,
  extractStructuredJsonFromModelText,
  normalizeAnalyzeReport,
  normalizeWritingBlueprint,
  normalizeDraftDocument,
  normalizeStyleUnifyResult,
  normalizeReviewIssues,
  validateSubagentConfig,
};

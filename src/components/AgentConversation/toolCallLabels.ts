import type { Outline, EntityId } from "../../types";
import type { ToolCallLabelOutcome } from "../../agent-runtime/contracts";

export const KNOWN_TOOL_CALL_LABELS = {
  addForeshadowing: "添加伏笔",
  addSparkIdea: "添加设定",
  archiveMemory: "归档长期记忆",
  batchGetChapterContents: "查看多章内容",
  beginSceneListArtifact: "开始整理场景表",
  beginSourceAnalysisArtifact: "开始整理原作分析",
  beginCreativeBriefArtifact: "开始整理创作简报",
  beginScreenplayReviewArtifact: "开始整理剧本审阅",
  beginScreenplayStructureArtifact: "开始整理剧本结构",
  beginScreenplayRevisionArtifact: "开始整理剧本修订",
  createCharacter: "创建人物",
  createMemory: "创建长期记忆",
  createSettingEntity: "创建世界设定",
  createWritingChapter: "创建章节",
  createWritingChapters: "创建章节/卷",
  delegateToAgents: "委派子 Agent 协作",
  deleteCharacter: "删除人物",
  deleteSettingEntity: "删除世界设定条目",
  deleteSparkIdea: "删除设定",
  editChapterContent: "编辑章节内容",
  editGlobalOutline: "编辑总纲",
  editStoryBackground: "编辑小说背景",
  finalizeSceneListProposal: "完成场景表提案",
  finalizeSourceAnalysisProposal: "完成原作范围分析",
  finalizeCreativeBriefProposal: "完成创作简报提案",
  finalizeScreenplayReviewProposal: "完成剧本审阅报告",
  finalizeScreenplayStructureProposal: "完成剧本结构提案",
  finalizeScreenplayRevisionProposal: "完成剧本修订提案",
  getBookCharacters: "查看人物信息",
  getChapterContent: "查看章节内容",
  getGlobalOutline: "查看总纲",
  getScreenplayDocument: "读取剧本文档",
  getScreenplayDraftContext: "查看正文创作上下文",
  getScreenplayProject: "读取剧本项目",
  getScreenplayEpisodeContext: "读取分集上下文",
  getSettingEntities: "查看世界设定详情",
  getSourceBookOverview: "读取原作概览",
  getSourceCharacters: "读取原作人物",
  getSourceCoveragePlan: "规划原作阅读范围",
  getSourceWorldSettings: "读取原作世界设定",
  getStoryBackground: "查看小说背景",
  getStoryHealthDashboard: "查看故事健康度仪表盘",
  getWritingStatsDashboard: "查看写作统计仪表盘",
  linkMemories: "关联长期记忆",
  listBookCharacters: "查看人物列表",
  listOutlines: "查看大纲列表",
  listSettingEntities: "查看世界设定列表",
  listWritingChapters: "查看章节目录",
  listWritingOutlines: "查看大纲目录",
  proposeBeatSheet: "形成故事节拍",
  proposeCreativeBrief: "形成创作简报",
  proposeEpisodeOutline: "形成分集结构",
  proposeSceneDraft: "创作剧本正文",
  proposeSceneList: "形成场景表",
  proposeScreenplayReview: "审阅完整剧本",
  proposeScreenplayRevision: "修订完整剧本",
  proposeSourceAnalysis: "整理原作范围分析",
  queryOutline: "查看大纲详情",
  readSourceCoverageBatch: "阅读原作范围",
  readSourcePassages: "精读关键原文",
  resolveForeshadowing: "回收伏笔",
  searchMemories: "检索长期记忆",
  searchSourceMaterial: "检索原作素材",
  searchSparkIdeas: "检索设定",
  updateCharacter: "更新人物设定",
  updateMemory: "更新长期记忆",
  updateOutline: "更新大纲",
  updateSettingEntity: "更新世界设定",
  updateSparkIdea: "更新设定",
  inspectScreenplayProject: "查看剧本项目",
  readScreenplayDeliverable: "读取剧本交付物",
  searchScreenplayDeliverables: "检索剧本交付物",
  inspectSourceStructure: "查看原作结构",
  readSourceChapters: "读取原文章节",
  searchSourceText: "检索原文",
  listSourceCharacters: "查看原作人物",
  readSourceCharacters: "读取人物资料",
  listSourceWorldEntities: "查看世界设定",
  readSourceWorldEntities: "读取世界设定",
  readSourceBackground: "读取故事背景",
  querySourceStoryFacts: "检索故事事实",
  readSourceOutline: "读取原作大纲",
  readScreenplayTaskDependencies: "读取剧本任务依赖",
  readScreenplayPartDependenciesV1: "读取剧本任务依赖",
  readWritingChapters: "读取章节正文",
  readWritingOutlines: "读取大纲正文",
  readWritingTechniqueFile: "读取写作技法文件",
  searchWritingMemories: "检索写作记忆",
  readWritingMemories: "读取写作记忆",
  readNovelSourceSlice: "读取小说分片",
  readNovelAnalysisReduceInputs: "读取待归并分析",
  readNovelAnalysisSynthesisInputs: "读取整书分析结果",
  readNovelAnalysisReviewInput: "审核整书分析",
  writeScreenplayCandidatePart: "写入剧本候选稿",
  inspectScreenplayCandidate: "检查剧本候选稿",
  appendSceneListBatch: "追加场景表批次",
  appendSourceAnalysisBatch: "追加原作分析条目",
  appendCreativeBriefBatch: "追加创作简报条目",
  appendScreenplayReviewBatch: "追加剧本审阅条目",
  appendScreenplayStructureBatch: "追加剧本结构条目",
  appendScreenplayRevisionBatch: "追加剧本修订场景",
  appendScreenplayRevisionResolutionBatch: "追加审阅问题回写",
} as const satisfies Record<string, string>;

export function resolveLocalizedToolDisplayName(
  displayNames: Record<string, string> | undefined,
  locale = typeof document === "undefined"
    ? "zh-CN"
    : document.documentElement.lang || "zh-CN",
): string | undefined {
  if (!displayNames) return undefined;
  const entries = Object.entries(displayNames)
    .map(([tag, value]) => [
      tag.replace(/_/g, "-").toLowerCase(),
      value.trim(),
    ] as const)
    .filter((entry) => entry[1]);
  if (entries.length === 0) return undefined;
  const normalized = String(locale || "zh-CN").replace(/_/g, "-").toLowerCase();
  const language = normalized.split("-", 1)[0];
  const candidates = [normalized, language, "zh-cn", "zh", "en-us", "en"];
  for (const candidate of candidates) {
    const exact = entries.find(([tag]) => tag === candidate);
    if (exact) return exact[1];
    const languageMatch = entries.find(
      ([tag]) => tag.split("-", 1)[0] === candidate,
    );
    if (languageMatch) return languageMatch[1];
  }
  return entries[0][1];
}

function staticToolCallLabel(name: string, displayName?: string): string {
  if (displayName) return displayName;
  return KNOWN_TOOL_CALL_LABELS[name as keyof typeof KNOWN_TOOL_CALL_LABELS]
    || `执行工具 ${name}`;
}

function resolveChapterTitleInCatalog(
  chapterId: EntityId | undefined,
  writingChapters: { id: EntityId; title: string }[],
): string | undefined {
  if (chapterId == null || String(chapterId).trim() === "") return undefined;
  const key = String(chapterId);
  const ch = writingChapters.find((c) => String(c.id) === key);
  const t = ch?.title?.trim();
  return t || undefined;
}

const CHAPTER_TITLE_PREFIX =
  /^\s*第\s*([0-9０-９]+|[零〇一两二三四五六七八九十百千万]+)\s*[章回][\s:：.。、．\-—–·_　]*/;

function normalizeDigits(value: string): string {
  return value.replace(/[０-９]/g, (ch) =>
    String.fromCharCode(ch.charCodeAt(0) - 0xfee0),
  );
}

const CN_DIGITS: Record<string, number> = {
  零: 0, 〇: 0, 一: 1, 两: 2, 二: 2, 三: 3, 四: 4,
  五: 5, 六: 6, 七: 7, 八: 8, 九: 9,
};
const CN_UNITS: Record<string, number> = { 十: 10, 百: 100, 千: 1000, 万: 10000 };

function parseChineseInteger(value: string): number | undefined {
  if (!/^[零〇一两二三四五六七八九十百千万]+$/.test(value)) return undefined;
  let total = 0;
  let section = 0;
  let number = 0;
  for (const ch of value) {
    if (CN_DIGITS[ch] != null) {
      number = CN_DIGITS[ch];
      continue;
    }
    const unit = CN_UNITS[ch];
    if (unit === 10000) {
      section = (section + number) * unit;
      total += section;
      section = 0;
    } else {
      section += (number || 1) * unit;
    }
    number = 0;
  }
  const result = total + section + number;
  return result > 0 ? result : undefined;
}

function chapterPositionInCatalog(
  chapterId: string,
  writingChapters: { id: EntityId; title: string }[],
): { index: number; number: number; title?: string } | undefined {
  const idx = writingChapters.findIndex((c) => String(c.id) === chapterId);
  if (idx < 0) return undefined;
  const raw = writingChapters[idx].title?.trim();
  if (!raw) return undefined;
  // 章节号优先取标题里的「第N章」：目录序号会因卷节点等混排而错位
  // （线上案例：目录第 54 项标题是「第53章」）。
  const numberMatch = raw.match(CHAPTER_TITLE_PREFIX);
  const number = numberMatch
    ? (/^[0-9０-９]+$/.test(numberMatch[1])
      ? Number(normalizeDigits(numberMatch[1]))
      : parseChineseInteger(numberMatch[1]) ?? NaN)
    : NaN;
  // 序号后剩余部分才作为展示标题；标题仅是「第53章」时剩余为空。
  const rest = numberMatch ? raw.replace(numberMatch[0], "").trim() : raw;
  return Number.isInteger(number) && number > 0
    ? { index: idx, number, title: rest || undefined }
    : { index: idx, number: idx + 1, title: rest || raw };
}

function chapterPiece(position: { number: number; title?: string }): string {
  return position.title
    ? `第${position.number}章《${position.title}》`
    : `第${position.number}章`;
}

/**
 * 多章节读取的范围描述：按目录顺序判断连续性——
 * 连续章节显示「第53章到第55章《收网》」，离散章节逐个列出
 * 「第2章《旧宅》、第5章《收网》」（超过 3 个收敛为「等N章」）。
 * 任一章节无法在本地目录解析时返回空串，由调用方退回计数文案。
 */
function chapterScopeDescription(
  ids: string[],
  writingChapters: { id: EntityId; title: string }[],
): string {
  const positions: { index: number; number: number; title?: string }[] = [];
  for (const id of ids) {
    const position = chapterPositionInCatalog(id, writingChapters);
    if (!position) return "";
    positions.push(position);
  }
  if (positions.length === 0) return "";
  positions.sort((left, right) => left.index - right.index);
  const min = positions[0];
  const max = positions[positions.length - 1];
  if (positions.length > 1 && max.index - min.index + 1 === positions.length) {
    return `${chapterPiece(min)}到${chapterPiece(max)}`;
  }
  const list = positions
    .slice(0, 3)
    .map((p) => chapterPiece(p))
    .join("、");
  return positions.length <= 3
    ? list
    : `${list}等${positions.length}章`;
}

function characterNamesSuffix(
  characterIds: unknown,
  characters: { id: number; name: string }[] | undefined,
): string {
  const ids = Array.isArray(characterIds)
    ? characterIds.map((value) => Number(value)).filter((id) => Number.isInteger(id))
    : [];
  if (ids.length === 0 || !characters?.length) return "";
  const names = ids
    .map((id) => characters.find((c) => Number(c.id) === id)?.name?.trim())
    .filter((name): name is string => Boolean(name));
  if (names.length !== ids.length) return "";
  return displayNamesSuffix(names);
}

const ENTITY_TYPE_LABELS: Record<string, string> = {
  location: "地点",
  faction: "阵营",
  item: "物品",
  other: "其他",
};

const WRITING_MEMORY_KIND_LABELS: Record<string, string> = {
  spark: "灵感",
  foreshadowing: "伏笔",
  longTerm: "长期记忆",
};

/** 气泡内引用检索词/路径等用户可读参数时的安全长度 */
function displayQuery(value: unknown, max = 40): string {
  const text = String(value ?? "").trim();
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function displayQuerySuffix(value: unknown, max = 40): string {
  const text = displayQuery(value, max);
  return text ? `“${text}”` : "";
}

function displayNamesSuffix(names: unknown, max = 3): string {
  const list = Array.isArray(names)
    ? names.map((name) => displayQuery(name, 24)).filter(Boolean)
    : [];
  if (list.length === 0) return "";
  const head = list.slice(0, max).join("、");
  return `（${head}${list.length > max ? "等" : ""}）`;
}

function entityTypeSuffix(value: unknown): string {
  const key = String(value ?? "").trim();
  const label = ENTITY_TYPE_LABELS[key];
  return label ? `（${label}类）` : "";
}

function resolveChapterTitleByIndexInCatalog(
  chapterIndex: unknown,
  writingChapters: { id: EntityId; title: string }[],
): string | undefined {
  const idx = Number(chapterIndex);
  if (!Number.isInteger(idx) || idx <= 0) return undefined;
  const row = writingChapters[idx - 1];
  const t = String(row?.title || "").trim();
  return t || undefined;
}

function resolveOutlineTitleInCatalog(
  outlineId: EntityId | undefined,
  availableOutlines: Outline[],
): string | undefined {
  if (outlineId == null || String(outlineId).trim() === "") return undefined;
  const key = String(outlineId).trim();
  const row = availableOutlines.find((o) => String(o.id) === key);
  const t = row?.title?.trim();
  return t || undefined;
}

/** 工具气泡文案；章节类若在本地章节目录无对应标题则 outcome=context_error（视为参数/上下文有误） */
export function toolCallDisplayRow(
  name: string,
  args: Record<string, unknown>,
  writingChapters: { id: EntityId; title: string }[],
  availableOutlines: Outline[],
  displayName?: string,
  characters?: { id: number; name: string }[],
): { label: string; outcome: ToolCallLabelOutcome } {
  try {
    switch (name) {
      case "getChapterContent":
      case "readWritingChapters": {
        const chapterTitleArg =
          args.chapterTitle != null && String(args.chapterTitle).trim() !== ""
            ? String(args.chapterTitle).trim()
            : "";
        if (chapterTitleArg) {
          return { label: `查看《${chapterTitleArg}》章节内容`, outcome: "ok" };
        }
        const titleByIndex = resolveChapterTitleByIndexInCatalog(
          args.chapterIndex,
          writingChapters,
        );
        if (titleByIndex) {
          return { label: `查看《${titleByIndex}》章节内容`, outcome: "ok" };
        }
        const ids = Array.isArray(args.chapterIds)
          ? args.chapterIds.map((value) => String(value).trim()).filter(Boolean)
          : [];
        if (ids.length > 1) {
          const scope = chapterScopeDescription(ids, writingChapters);
          return {
            label: scope ? `读取${scope}` : `读取 ${ids.length} 章正文`,
            outcome: "ok",
          };
        }
        const cid = ids[0] || (
          args.chapterId != null && args.chapterId !== ""
            ? String(args.chapterId).trim()
            : ""
        );
        if (!cid) {
          return { label: "查看章节内容", outcome: "ok" };
        }
        const position = chapterPositionInCatalog(cid, writingChapters);
        if (position) {
          return {
            label: `查看${chapterPiece(position)}章节内容`,
            outcome: "ok",
          };
        }
        const title = resolveChapterTitleInCatalog(cid, writingChapters);
        return { label: title ? `查看《${title}》章节内容` : "查看章节内容", outcome: "ok" };
      }
      case "editChapterContent": {
        const chapterTitleArg =
          args.chapterTitle != null && String(args.chapterTitle).trim() !== ""
            ? String(args.chapterTitle).trim()
            : "";
        if (chapterTitleArg) {
          return { label: `编辑《${chapterTitleArg}》章节内容`, outcome: "ok" };
        }
        const titleByIndex = resolveChapterTitleByIndexInCatalog(
          args.chapterIndex,
          writingChapters,
        );
        if (titleByIndex) {
          return { label: `编辑《${titleByIndex}》章节内容`, outcome: "ok" };
        }
        const cid =
          args.chapterId != null && args.chapterId !== ""
            ? String(args.chapterId).trim()
            : "";
        if (!cid) {
          return { label: "编辑章节内容", outcome: "ok" };
        }
        const position = chapterPositionInCatalog(cid, writingChapters);
        if (position) {
          return {
            label: `编辑${chapterPiece(position)}章节内容`,
            outcome: "ok",
          };
        }
        const title = resolveChapterTitleInCatalog(cid, writingChapters);
        return { label: title ? `编辑《${title}》章节内容` : "编辑章节内容", outcome: "ok" };
      }
      case "batchGetChapterContents": {
        const raw = args.chapterIds;
        if (!Array.isArray(raw) || raw.length === 0) {
          return { label: "查看多章内容", outcome: "ok" };
        }
        const ids = raw
          .map((x) => String(x).trim())
          .filter((s) => s !== "");
        if (ids.length === 0) {
          return { label: "查看多章内容", outcome: "context_error" };
        }
        const bad = ids.filter((id) => !resolveChapterTitleInCatalog(id, writingChapters));
        if (bad.length > 0) {
          return { label: "查看多章内容", outcome: "context_error" };
        }
        if (ids.length === 1) {
          const t0 = resolveChapterTitleInCatalog(ids[0], writingChapters)!;
          return { label: `查看《${t0}》章节内容`, outcome: "ok" };
        }
        const scope = chapterScopeDescription(ids, writingChapters);
        if (scope) return { label: `查看${scope}`, outcome: "ok" };
        const head = ids
          .slice(0, 3)
          .map((id) => `《${resolveChapterTitleInCatalog(id, writingChapters)}》`)
          .join("");
        if (ids.length <= 3) return { label: `查看${head}等多章内容`, outcome: "ok" };
        return { label: `查看${head}等 ${ids.length} 章内容`, outcome: "ok" };
      }
      case "listWritingChapters":
        return { label: "查看章节目录", outcome: "ok" };
      case "createWritingChapter":
        return { label: "创建章节", outcome: "ok" };
      case "listBookCharacters":
        return { label: "查看人物列表", outcome: "ok" };
      case "createCharacter": {
        const n = args.name != null ? String(args.name).trim() : "";
        return { label: n ? `创建人物「${n}」` : "创建人物", outcome: "ok" };
      }
      case "updateCharacter": {
        const n = args.name != null ? String(args.name).trim() : "";
        return { label: n ? `更新人物「${n}」设定` : "更新人物设定", outcome: "ok" };
      }
      case "deleteCharacter":
        return { label: "删除人物", outcome: "ok" };
      case "getStoryBackground":
        return { label: "查看小说背景", outcome: "ok" };
      case "editStoryBackground":
        return { label: "编辑小说背景", outcome: "ok" };
      case "createSettingEntity": {
        const n = args.name != null ? String(args.name).trim() : "";
        return { label: n ? `创建设定「${n}」` : "创建世界设定", outcome: "ok" };
      }
      case "updateSettingEntity": {
        const n = args.name != null ? String(args.name).trim() : "";
        return { label: n ? `更新设定「${n}」` : "更新世界设定", outcome: "ok" };
      }
      case "deleteSettingEntity":
        return { label: "删除世界设定条目", outcome: "ok" };
      case "getStoryHealthDashboard":
        return { label: "查看故事健康度仪表盘", outcome: "ok" };
      case "getWritingStatsDashboard":
        return { label: "查看写作统计仪表盘", outcome: "ok" };
      case "getGlobalOutline":
        return { label: "查看总纲", outcome: "ok" };
      case "editGlobalOutline":
        return { label: "编辑总纲", outcome: "ok" };
      case "queryOutline":
      case "readWritingOutlines": {
        const outlineIdArg =
          args.outlineId != null && String(args.outlineId).trim() !== ""
            ? String(args.outlineId).trim()
            : "";
        /** 本地 availableOutlines 可能与 listOutlines 时点不一致；只要模型传了合法 id 就不标 context_error（以服务端结果为准） */
        const labelForUnknownId = (id: string) =>
          id.length > 12 ? `查看大纲详情（${id.slice(0, 10)}…）` : `查看大纲详情（${id}）`;
        if (outlineIdArg) {
          const title = resolveOutlineTitleInCatalog(outlineIdArg, availableOutlines);
          if (title) return { label: `查看《${title}》大纲详情`, outcome: "ok" };
          return { label: labelForUnknownId(outlineIdArg), outcome: "ok" };
        }
        if (Array.isArray(args.outlineIds) && args.outlineIds.length > 0) {
          const ids = args.outlineIds
            .map((x) => String(x).trim())
            .filter((s) => s !== "");
          if (ids.length === 1) {
            const title = resolveOutlineTitleInCatalog(ids[0], availableOutlines);
            if (title) return { label: `查看《${title}》大纲详情`, outcome: "ok" };
            return { label: labelForUnknownId(ids[0]), outcome: "ok" };
          }
          const titles = ids
            .map((id) => resolveOutlineTitleInCatalog(id, availableOutlines))
            .filter((t): t is string => Boolean(t));
          if (titles.length > 0) {
            const head = titles.slice(0, 3).map((t) => `《${t}》`).join("");
            if (titles.length <= 3) return { label: `查看${head}等多条大纲详情`, outcome: "ok" };
            return { label: `查看${head}等 ${titles.length} 条大纲详情`, outcome: "ok" };
          }
          if (ids.length > 0) {
            return { label: `查看 ${ids.length} 条大纲详情`, outcome: "ok" };
          }
        }
        return { label: "查看大纲详情", outcome: "context_error" };
      }
      case "listOutlines":
      case "listWritingOutlines":
        return { label: "查看大纲列表", outcome: "ok" };
      case "updateOutline":
        return { label: "更新大纲", outcome: "ok" };
      case "addSparkIdea":
        return { label: "添加设定", outcome: "ok" };
      case "updateSparkIdea":
        return { label: "更新设定", outcome: "ok" };
      case "deleteSparkIdea":
        return { label: "删除设定", outcome: "ok" };
      case "searchSparkIdeas":
        return { label: "检索设定", outcome: "ok" };
      case "addForeshadowing":
        return { label: "添加伏笔", outcome: "ok" };
      case "createWritingChapters": {
        const entries = Array.isArray(args.chapters)
          ? args.chapters.filter(
              (item): item is Record<string, unknown> =>
                Boolean(item) && typeof item === "object" && !Array.isArray(item),
            )
          : [];
        if (entries.length === 0) {
          return { label: "创建章节/卷", outcome: "ok" };
        }
        const volumeCount = entries.filter((item) => item.isVolume === true).length;
        const noun = volumeCount === 0
          ? "章节"
          : volumeCount === entries.length
            ? "卷"
            : "章节/卷";
        const head = entries
          .slice(0, 3)
          .map((item) => `《${displayQuery(item.title, 24)}》`)
          .filter((title) => title !== "《》")
          .join("");
        const suffix = entries.length <= 3
          ? head
          : `${head}等 ${entries.length} 项`;
        return { label: `创建${noun}${suffix}`, outcome: "ok" };
      }
      case "getBookCharacters": {
        const names = displayNamesSuffix(args.names);
        if (names) return { label: `读取人物详情${names}`, outcome: "ok" };
        const resolvedIds = characterNamesSuffix(args.characterIds, characters);
        if (resolvedIds) return { label: `读取人物详情${resolvedIds}`, outcome: "ok" };
        const count = Array.isArray(args.characterIds) ? args.characterIds.length : 0;
        return {
          label: count > 1 ? `读取 ${count} 位人物详情` : "查看人物信息",
          outcome: "ok",
        };
      }
      case "getSettingEntities": {
        const names = displayNamesSuffix(args.names);
        if (names) return { label: `读取设定详情${names}`, outcome: "ok" };
        return {
          label: args.entityType
            ? `读取设定详情${entityTypeSuffix(args.entityType)}`
            : "查看世界设定详情",
          outcome: "ok",
        };
      }
      case "listSettingEntities":
        return {
          label: args.entityType
            ? `查看设定目录${entityTypeSuffix(args.entityType)}`
            : "查看世界设定列表",
          outcome: "ok",
        };
      case "searchWritingMemories":
        return {
          label: `检索写作记忆${displayQuerySuffix(args.query)}`,
          outcome: "ok",
        };
      case "searchNovelKnowledge":
        return {
          label: `检索创作资料${displayQuerySuffix(args.query)}`,
          outcome: "ok",
        };
      case "readNovelKnowledge":
        return { label: "读取创作资料文档", outcome: "ok" };
      case "readWritingMemories": {
        const refs = Array.isArray(args.refs) ? args.refs : [];
        if (refs.length === 0) return { label: "读取写作记忆", outcome: "ok" };
        const kinds: string[] = [];
        for (const ref of refs) {
          const label = WRITING_MEMORY_KIND_LABELS[
            String((ref as Record<string, unknown>)?.kind ?? "")
          ];
          if (label && !kinds.includes(label)) kinds.push(label);
        }
        return {
          label: kinds.length
            ? `读取 ${refs.length} 条写作记忆（${kinds.join("、")}）`
            : `读取 ${refs.length} 条写作记忆`,
          outcome: "ok",
        };
      }
      case "readWritingTechniqueFile": {
        const path = displayQuery(args.path, 60);
        return {
          label: path ? `读取写作技法文件 ${path}` : "读取写作技法文件",
          outcome: "ok",
        };
      }
      case "searchScreenplayDeliverables":
      case "searchSourceText":
      case "querySourceStoryFacts": {
        const query = displayQuerySuffix(args.searchQuery ?? args.query);
        return {
          label: query
            ? `${staticToolCallLabel(name, displayName)}${query}`
            : staticToolCallLabel(name, displayName),
          outcome: "ok",
        };
      }
      case "readSourceChapters":
      case "readScreenplayTaskDependencies": {
        const targets = Array.isArray(args.readTargets)
          ? args.readTargets.map((t) => String(t)).filter(Boolean)
          : [];
        if (targets.length === 0) {
          return {
            label: staticToolCallLabel(name, displayName),
            outcome: "ok",
          };
        }
        const head = targets.slice(0, 3).join("、");
        return {
          label: targets.length <= 3
            ? `${staticToolCallLabel(name, displayName)}：${head}`
            : `${staticToolCallLabel(name, displayName)}：${head}等 ${targets.length} 项`,
          outcome: "ok",
        };
      }
      case "getScreenplayProject":
        return { label: "读取剧本项目", outcome: "ok" };
      case "getScreenplayDocument":
        return { label: "读取剧本文档", outcome: "ok" };
      case "getSourceCoveragePlan":
        return { label: "规划原作阅读范围", outcome: "ok" };
      case "readSourceCoverageBatch":
        return { label: "阅读原作范围", outcome: "ok" };
      case "searchSourceMaterial":
        return { label: "检索原作素材", outcome: "ok" };
      case "readSourcePassages":
        return { label: "精读关键原文", outcome: "ok" };
      case "proposeSourceAnalysis":
        return { label: "整理原作范围分析", outcome: "ok" };
      case "beginSourceAnalysisArtifact":
        return { label: "开始整理原作分析", outcome: "ok" };
      case "appendSourceAnalysisBatch":
        return { label: "追加原作分析条目", outcome: "ok" };
      case "finalizeSourceAnalysisProposal":
        return { label: "完成原作范围分析", outcome: "ok" };
      case "beginCreativeBriefArtifact":
        return { label: "开始整理创作简报", outcome: "ok" };
      case "appendCreativeBriefBatch":
        return { label: "追加创作简报条目", outcome: "ok" };
      case "finalizeCreativeBriefProposal":
        return { label: "完成创作简报提案", outcome: "ok" };
      case "proposeCreativeBrief":
        return { label: "形成创作简报", outcome: "ok" };
      case "beginScreenplayStructureArtifact":
        return { label: "开始整理剧本结构", outcome: "ok" };
      case "appendScreenplayStructureBatch":
        return { label: "追加剧本结构条目", outcome: "ok" };
      case "finalizeScreenplayStructureProposal":
        return { label: "完成剧本结构提案", outcome: "ok" };
      case "proposeBeatSheet":
        return { label: "形成故事节拍", outcome: "ok" };
      case "proposeEpisodeOutline":
        return { label: "形成分集结构", outcome: "ok" };
      case "beginSceneListArtifact":
        return { label: "开始整理场景表", outcome: "ok" };
      case "appendSceneListBatch":
        return { label: "追加场景表批次", outcome: "ok" };
      case "finalizeSceneListProposal":
        return { label: "完成场景表提案", outcome: "ok" };
      case "proposeSceneList":
        return { label: "形成场景表", outcome: "ok" };
      case "proposeSceneDraft":
        return { label: "创作剧本正文", outcome: "ok" };
      case "proposeScreenplayReview":
        return { label: "审阅完整剧本", outcome: "ok" };
      case "beginScreenplayReviewArtifact":
        return { label: "开始整理剧本审阅", outcome: "ok" };
      case "appendScreenplayReviewBatch":
        return { label: "追加剧本审阅条目", outcome: "ok" };
      case "finalizeScreenplayReviewProposal":
        return { label: "完成剧本审阅报告", outcome: "ok" };
      case "beginScreenplayRevisionArtifact":
        return { label: "开始整理剧本修订", outcome: "ok" };
      case "appendScreenplayRevisionBatch":
        return { label: "追加剧本修订场景", outcome: "ok" };
      case "appendScreenplayRevisionResolutionBatch":
        return { label: "追加审阅问题回写", outcome: "ok" };
      case "finalizeScreenplayRevisionProposal":
        return { label: "完成剧本修订提案", outcome: "ok" };
      case "proposeScreenplayRevision":
        return { label: "修订完整剧本", outcome: "ok" };
      default:
        return { label: staticToolCallLabel(name, displayName), outcome: "ok" };
    }
  } catch {
    return { label: staticToolCallLabel(name, displayName), outcome: "ok" };
  }
}

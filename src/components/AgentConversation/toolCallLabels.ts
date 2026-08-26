import type { Outline, EntityId } from "../../types";
import type { ToolCallLabelOutcome } from "../../agent-runtime/contracts";

/** 旧会话和旧后端事件的兼容文案；新事件优先使用 ToolSchema.display_names。 */
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
  getBookStyle: "查看风格基调",
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
  readSourceStyle: "读取原作风格",
  writeScreenplayCandidatePart: "写入剧本候选稿",
  inspectScreenplayCandidate: "检查剧本候选稿",
  appendSceneListBatch: "追加场景表批次",
  appendSourceAnalysisBatch: "追加原作分析条目",
  appendCreativeBriefBatch: "追加创作简报条目",
  appendScreenplayReviewBatch: "追加剧本审阅条目",
  appendScreenplayStructureBatch: "追加剧本结构条目",
  appendScreenplayRevisionBatch: "追加剧本修订场景",
  appendScreenplayRevisionResolutionBatch: "追加审阅问题回写",
  // 兼容旧会话中已经持久化的历史工具名。
  listScreenplayDocuments: "查看项目文档",
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
    || "执行 Agent 工具操作";
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
): { label: string; outcome: ToolCallLabelOutcome } {
  try {
    switch (name) {
      case "getChapterContent": {
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
        const cid =
          args.chapterId != null && args.chapterId !== ""
            ? String(args.chapterId).trim()
            : "";
        if (!cid) {
          return { label: "查看章节内容", outcome: "ok" };
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
      case "getBookCharacters":
        return { label: "查看人物信息", outcome: "ok" };
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
      case "listSettingEntities":
        return { label: "查看世界设定列表", outcome: "ok" };
      case "getSettingEntities":
        return { label: "查看世界设定详情", outcome: "ok" };
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
      case "getBookStyle":
        return { label: "查看风格基调", outcome: "ok" };
      case "getStoryHealthDashboard":
        return { label: "查看故事健康度仪表盘", outcome: "ok" };
      case "getWritingStatsDashboard":
        return { label: "查看写作统计仪表盘", outcome: "ok" };
      case "getGlobalOutline":
        return { label: "查看总纲", outcome: "ok" };
      case "editGlobalOutline":
        return { label: "编辑总纲", outcome: "ok" };
      case "queryOutline": {
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
      case "getScreenplayProject":
        return { label: "读取剧本项目", outcome: "ok" };
      case "listScreenplayDocuments":
        return { label: "查看项目文档", outcome: "ok" };
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

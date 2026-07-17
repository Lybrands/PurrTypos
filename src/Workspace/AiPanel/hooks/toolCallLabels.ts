import type { Outline, EntityId } from "../../../types";
import type { ToolCallLabelOutcome } from "./chat.types";

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
      default:
        return { label: name, outcome: "ok" };
    }
  } catch {
    return { label: name, outcome: "ok" };
  }
}

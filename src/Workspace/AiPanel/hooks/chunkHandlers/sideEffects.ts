import type { ChunkHandler } from "./types";

export const handleChapterContentUpdated: ChunkHandler = (chunk, ctx) => {
  if (chunk.chapterContentUpdated == null || !ctx.isVisibleSession()) return;
  window.dispatchEvent(
    new CustomEvent("chapter-content-updated", {
      detail: { chapterId: chunk.chapterContentUpdated },
    }),
  );
};

/**
 * AI 工具 editChapterContent 不再直接落库，改为推送 diff 提议给前端，
 * 由 DiffProvider 监听并 startDiff，最终用户在 overlay 接受后走 commitChapterDiff。
 */
export const handleProposedChapterDiff: ChunkHandler = (chunk, ctx) => {
  if (!chunk.proposedChapterDiff || !ctx.isVisibleSession()) return;
  const p = chunk.proposedChapterDiff;
  if (p.chapterId == null || typeof p.proposedText !== "string") return;
  window.dispatchEvent(
    new CustomEvent("ai-propose-chapter-diff", {
      detail: {
        chapterId: p.chapterId,
        beforeText: typeof p.beforeText === "string" ? p.beforeText : "",
        proposedText: p.proposedText,
        source: p.source || "ai_tool_edit",
      },
    }),
  );
};

/**
 * AI 写工具改动了设定类数据（人物 / 故事背景 / 大纲）：
 * 广播给打开中的设定面板（CharacterTab / StoryBackgroundTab 等）刷新展示。
 */
export const handleSettingUpdated: ChunkHandler = (chunk, ctx) => {
  if (!chunk.settingUpdated || !ctx.isVisibleSession()) return;
  window.dispatchEvent(
    new CustomEvent("setting-updated", { detail: chunk.settingUpdated }),
  );
};

export const handleChapterCreated: ChunkHandler = (chunk, ctx) => {
  if (chunk.chapterCreated == null || !ctx.isVisibleSession()) return;
  window.dispatchEvent(
    new CustomEvent("chapter-created", {
      detail: {
        chapterId: chunk.chapterCreated.chapterId,
        title: chunk.chapterCreated.title,
        parentId: chunk.chapterCreated.parentId ?? null,
      },
    }),
  );
};

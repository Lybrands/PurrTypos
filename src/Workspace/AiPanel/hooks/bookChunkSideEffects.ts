import type {
  AgentChunkHost,
  AiStreamChunk,
} from '../../../agent-runtime/chunkHandlers/types'

/**
 * AI 工具 editChapterContent 不再直接落库，改为推送 diff 提议给前端，
 * 由 DiffProvider 监听并 startDiff，最终用户在 overlay 接受后走 commitChapterDiff。
 */
export function handleProposedChapterDiff(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
): void {
  if (!chunk.proposedChapterDiff || !host.isVisible()) return;
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
}

/**
 * AI 写工具改动了设定类数据（人物 / 故事背景 / 大纲）：
 * 广播给打开中的设定面板（CharacterTab / StoryBackgroundTab 等）刷新展示。
 */
export function handleSettingUpdated(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
): void {
  if (!chunk.settingUpdated || !host.isVisible()) return;
  window.dispatchEvent(
    new CustomEvent("setting-updated", { detail: chunk.settingUpdated }),
  );
}

export function handleChapterCreated(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
): void {
  if (chunk.chapterCreated == null || !host.isVisible()) return;
  window.dispatchEvent(
    new CustomEvent("chapter-created", {
      detail: {
        chapterId: chunk.chapterCreated.chapterId,
        title: chunk.chapterCreated.title,
        parentId: chunk.chapterCreated.parentId ?? null,
      },
    }),
  );
}

/**
 * 写作 Agent 的 editChapterContent 已获批准并落库（仅通知、不含正文）：
 * EditorPanel 监听该事件后重新拉取当前章节正文。
 */
export function handleChapterContentUpdated(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
): void {
  if (!chunk.chapterContentUpdated || !host.isVisible()) return;
  window.dispatchEvent(
    new CustomEvent("chapter-content-updated", {
      detail: {
        chapterId: chunk.chapterContentUpdated.chapterId,
        firstContent: chunk.chapterContentUpdated.firstContent === true,
      },
    }),
  );
}

/**
 * 写作 Agent 批量创建了章节/卷。Workspace 监听单个 chapter-created
 * 事件完成目录刷新与选章；逆序派发使最终选中本批第一章。
 */
export function handleChaptersCreated(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
): void {
  if (!chunk.chaptersCreated || !host.isVisible()) return;
  const chapters = Array.isArray(chunk.chaptersCreated.chapters)
    ? chunk.chaptersCreated.chapters
    : [];
  [...chapters].reverse().forEach((chapter) => {
    if (chapter?.chapterId == null || !chapter.title) return;
    window.dispatchEvent(
      new CustomEvent("chapter-created", {
        detail: {
          chapterId: chapter.chapterId,
          title: chapter.title,
        },
      }),
    );
  });
}

import type {
  AgentChunkHost,
  AiStreamChunk,
} from '../../../agent-runtime/chunkHandlers/types'
import {
  notifyChapterContentUpdated,
  reloadWritingChapters,
  setActiveChapter,
  useWorkspaceStore,
} from '../../../stores/workspaceStore'
import { notifySettingsUpdated } from '../../../stores/settingsInvalidationStore'
import { proposeChapterDiff } from '../../../stores/aiProposalBridge'

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
  proposeChapterDiff({
    chapterId: p.chapterId,
    beforeText: typeof p.beforeText === "string" ? p.beforeText : "",
    proposedText: p.proposedText,
    source: p.source || "ai_tool_edit",
  });
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
  const detail = chunk.settingUpdated as {
    kind?: string;
    action?: string;
    id?: string | number;
    name?: string;
  };
  const kind =
    detail.kind === "character" || detail.kind === "entity" || detail.kind === "background"
      ? detail.kind
      : undefined;
  if (kind) notifySettingsUpdated(kind, { id: detail.id, name: detail.name });
}

/** AI 创建章节：选中新章节并刷新目录（原 Workspace 事件监听逻辑并入） */
export function handleChapterCreated(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
): void {
  if (chunk.chapterCreated == null || !host.isVisible()) return;
  const { chapterId, title } = chunk.chapterCreated;
  if (chapterId == null || !title) return;
  setActiveChapter(chapterId, title);
  void reloadWritingChapters(useWorkspaceStore.getState().bookId);
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
  if (chunk.chapterContentUpdated.chapterId == null) return;
  notifyChapterContentUpdated(chunk.chapterContentUpdated.chapterId);
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
  // 逆序选章，使最终选中本批第一章；目录最后统一刷新一次
  [...chapters].reverse().forEach((chapter) => {
    if (chapter?.chapterId == null || !chapter.title) return;
    setActiveChapter(chapter.chapterId, chapter.title);
  });
  if (chapters.length > 0) {
    void reloadWritingChapters(useWorkspaceStore.getState().bookId);
  }
}

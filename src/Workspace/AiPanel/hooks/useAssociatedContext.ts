import { services } from '@/services'
import React from "react";
import type { EntityId, Outline } from "../../../types";
import { formatAssociableOutlineLabel, getAvailableOutlines } from "../../utils";
import {
  useAssociatedContextEntry,
  writeAssociatedContext,
} from "../../../stores/chatContextStore";

export interface UseAssociatedContextParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  writingChapters: { id: EntityId; title: string }[];
}

/**
 * 关联章节 / 大纲勾选（与主 AI 面板共享）：
 * 存储在 chatContextStore（按书持久化 + 响应式），
 * Inline 弹层与主面板两处挂载共享同一份勾选。
 */
export function useAssociatedContext({
  bookId,
  chapterId,
  writingChapters,
}: UseAssociatedContextParams) {
  const entry = useAssociatedContextEntry(bookId)
  const associatedChapterIds = entry.chapters
  const associatedOutlineIds = entry.outlines
  const [availableOutlines, setAvailableOutlines] = React.useState<Outline[]>(
    [],
  )

  React.useEffect(() => {
    if (bookId == null) {
      setAvailableOutlines([]);
      return;
    }
    getAvailableOutlines(bookId).then(setAvailableOutlines);
  }, [bookId]);

  // 大纲下拉选项，依赖 availableOutlines，避免每次渲染重新生成
  const outlineSelectOptions = React.useMemo(
    () =>
      availableOutlines.map((o) => ({
        label: formatAssociableOutlineLabel(o, availableOutlines),
        value: o.id,
      })),
    [availableOutlines],
  );

  // 章节下拉选项，依赖 writingChapters
  const chapterSelectOptions = React.useMemo(
    () => writingChapters.map((c) => ({ label: c.title, value: c.id })),
    [writingChapters],
  );

  const setAssociatedChapterIds = React.useCallback(
    (ids: EntityId[]) => {
      writeAssociatedContext(bookId, { chapters: ids, outlines: entry.outlines })
    },
    [bookId, entry.outlines],
  )
  const setAssociatedOutlineIds = React.useCallback(
    (ids: EntityId[]) => {
      writeAssociatedContext(bookId, { chapters: entry.chapters, outlines: ids })
    },
    [bookId, entry.chapters],
  )

  // 将当前章节追加到关联章节列表（已存在则跳过）
  const handleQuickAssociateChapter = React.useCallback(() => {
    if (!chapterId) return;
    if (associatedChapterIds.some((id) => String(id) === String(chapterId))) return;
    writeAssociatedContext(bookId, {
      chapters: [...associatedChapterIds, chapterId],
      outlines: entry.outlines,
    })
  }, [bookId, chapterId, associatedChapterIds, entry.outlines])

  // 查询当前章节对应的大纲并追加到关联大纲列表（已存在则跳过）
  const handleQuickAssociateOutline = React.useCallback(async () => {
    if (!chapterId) return;
    const res = await services.outlines.getOutlineForChapter(chapterId);
    if (res.success && res.data) {
      const id = res.data.id;
      if (associatedOutlineIds.some((x) => String(x) === String(id))) return;
      writeAssociatedContext(bookId, {
        chapters: entry.chapters,
        outlines: [...associatedOutlineIds, id],
      })
    }
  }, [bookId, chapterId, associatedOutlineIds, entry.chapters])

  return {
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    availableOutlines,
    chapterSelectOptions,
    outlineSelectOptions,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
  };
}

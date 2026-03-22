import React from "react";
import type { Outline } from "../../../types";
import { getAvailableOutlines } from "../../utils";

export interface UseAssociatedContextParams {
  bookId: number | null | undefined;
  chapterId: number | null | undefined;
  writingChapters: { id: number; title: string }[];
}

export function useAssociatedContext({
  bookId,
  chapterId,
  writingChapters,
}: UseAssociatedContextParams) {
  const [associatedChapterIds, setAssociatedChapterIds] = React.useState<
    number[]
  >([]);
  const [associatedOutlineIds, setAssociatedOutlineIds] = React.useState<
    number[]
  >([]);
  const [availableOutlines, setAvailableOutlines] = React.useState<Outline[]>(
    [],
  );

  // 大纲下拉选项，依赖 availableOutlines，避免每次渲染重新生成
  const outlineSelectOptions = React.useMemo(
    () => availableOutlines.map((o) => ({ label: o.title, value: o.id })),
    [availableOutlines],
  );

  // 章节下拉选项，依赖 writingChapters
  const chapterSelectOptions = React.useMemo(
    () => writingChapters.map((c) => ({ label: c.title, value: c.id })),
    [writingChapters],
  );

  React.useEffect(() => {
    if (bookId == null) {
      setAvailableOutlines([]);
      return;
    }
    getAvailableOutlines(bookId).then(setAvailableOutlines);
  }, [bookId]);

  // 将当前章节追加到关联章节列表（已存在则跳过）
  const handleQuickAssociateChapter = React.useCallback(() => {
    if (!chapterId) return;
    setAssociatedChapterIds((prev) =>
      prev.includes(chapterId) ? prev : [...prev, chapterId],
    );
  }, [chapterId]);

  // 查询当前章节对应的大纲并追加到关联大纲列表（已存在则跳过）
  const handleQuickAssociateOutline = React.useCallback(async () => {
    if (!chapterId) return;
    const res = await window.electronAPI.getOutlineByWritingChapter(chapterId);
    if (res.success && res.data) {
      const id = res.data.id;
      setAssociatedOutlineIds((prev) =>
        prev.includes(id) ? prev : [...prev, id],
      );
    }
  }, [chapterId]);

  return {
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    availableOutlines,
    outlineSelectOptions,
    chapterSelectOptions,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
  };
}

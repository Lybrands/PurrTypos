import React from "react";
import type { EntityId, Outline } from "../../../types";
import { formatAssociableOutlineLabel, getAvailableOutlines } from "../../utils";

export interface UseAssociatedContextParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  writingChapters: { id: EntityId; title: string }[];
}

export function useAssociatedContext({
  bookId,
  chapterId,
  writingChapters,
}: UseAssociatedContextParams) {
  const [associatedChapterIds, setAssociatedChapterIds] = React.useState<
    EntityId[]
  >([]);
  const [associatedOutlineIds, setAssociatedOutlineIds] = React.useState<
    EntityId[]
  >([]);
  const [availableOutlines, setAvailableOutlines] = React.useState<Outline[]>(
    [],
  );

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
    const key = String(chapterId);
    setAssociatedChapterIds((prev) =>
      prev.some((x) => String(x) === key) ? prev : [...prev, chapterId],
    );
  }, [chapterId]);

  // 查询当前章节对应的大纲并追加到关联大纲列表（已存在则跳过）
  const handleQuickAssociateOutline = React.useCallback(async () => {
    if (!chapterId) return;
    const res = await window.electronAPI.getOutlineByWritingChapter(chapterId);
    if (res.success && res.data) {
      const id = res.data.id;
      setAssociatedOutlineIds((prev) =>
        prev.some((x) => String(x) === String(id)) ? prev : [...prev, id],
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

import React from "react";
import type { EntityId, Outline } from "../../../types";
import { formatAssociableOutlineLabel, getAvailableOutlines } from "../../utils";

export interface UseAssociatedContextParams {
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  writingChapters: { id: EntityId; title: string }[];
}

const STORAGE_KEY = "purrtypos_ai_associated_context";

interface PersistedEntry {
  chapters: EntityId[];
  outlines: EntityId[];
}

function loadAll(): Record<string, PersistedEntry> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const obj = JSON.parse(raw);
    return obj && typeof obj === "object" ? obj : {};
  } catch {
    return {};
  }
}

function saveAll(all: Record<string, PersistedEntry>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // ignore
  }
}

function loadForBook(bookId: EntityId | null | undefined): PersistedEntry {
  if (bookId == null) return { chapters: [], outlines: [] };
  const all = loadAll();
  const entry = all[String(bookId)];
  if (!entry) return { chapters: [], outlines: [] };
  return {
    chapters: Array.isArray(entry.chapters) ? entry.chapters : [],
    outlines: Array.isArray(entry.outlines) ? entry.outlines : [],
  };
}

function saveForBook(
  bookId: EntityId | null | undefined,
  entry: PersistedEntry,
) {
  if (bookId == null) return;
  const all = loadAll();
  all[String(bookId)] = entry;
  saveAll(all);
}

export function useAssociatedContext({
  bookId,
  chapterId,
  writingChapters,
}: UseAssociatedContextParams) {
  const initial = React.useMemo(() => loadForBook(bookId), [bookId]);
  const [associatedChapterIds, setAssociatedChapterIds] = React.useState<
    EntityId[]
  >(initial.chapters);
  const [associatedOutlineIds, setAssociatedOutlineIds] = React.useState<
    EntityId[]
  >(initial.outlines);
  const [availableOutlines, setAvailableOutlines] = React.useState<Outline[]>(
    [],
  );

  // 切书时重新载入该书的勾选
  const lastBookKeyRef = React.useRef<string>("");
  React.useEffect(() => {
    const key = String(bookId ?? "");
    if (key === lastBookKeyRef.current) return;
    lastBookKeyRef.current = key;
    const next = loadForBook(bookId);
    setAssociatedChapterIds(next.chapters);
    setAssociatedOutlineIds(next.outlines);
  }, [bookId]);

  // 勾选变化时持久化（skip 首次 mount 重复写入无所谓）
  React.useEffect(() => {
    saveForBook(bookId, {
      chapters: associatedChapterIds,
      outlines: associatedOutlineIds,
    });
  }, [bookId, associatedChapterIds, associatedOutlineIds]);

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

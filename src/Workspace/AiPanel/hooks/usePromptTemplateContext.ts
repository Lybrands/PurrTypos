import React from "react";
import type { EntityId } from "../../../types";

interface SelectOption {
  label: string;
  value: EntityId;
}

interface WritingChapter {
  id: EntityId;
  title: string;
  parent_id?: EntityId | null;
}

export interface UsePromptTemplateContextParams {
  chapterId: EntityId | null | undefined;
  writingChapters: WritingChapter[];
  bookTitle: string;
  activeChapterTitle: string;
  associatedChapterIds: EntityId[];
  associatedOutlineIds: EntityId[];
  chapterSelectOptions: SelectOption[];
  outlineSelectOptions: SelectOption[];
}

/**
 * 组装 prompt 模板占位符所需的上下文（书名 / 本章标题 / 本章大纲 / 前文标题 /
 * 关联章节&大纲标题）。
 *
 * 内部负责按需拉取「本章大纲」markdown（activeChapterId 变化时），其余字段从入参
 * 派生。纯展示层数据，不参与发送逻辑。
 */
export function usePromptTemplateContext({
  chapterId,
  writingChapters,
  bookTitle,
  activeChapterTitle,
  associatedChapterIds,
  associatedOutlineIds,
  chapterSelectOptions,
  outlineSelectOptions,
}: UsePromptTemplateContextParams) {
  /** 本章大纲 markdown 文本：activeChapterId 变化时按需拉取，给 {本章大纲} 占位符用。 */
  const [currentChapterOutlineText, setCurrentChapterOutlineText] =
    React.useState<string>("");
  React.useEffect(() => {
    if (!chapterId) {
      setCurrentChapterOutlineText("");
      return;
    }
    let aborted = false;
    (async () => {
      try {
        const res = await window.electronAPI.getOutlineForChapter(chapterId);
        if (aborted) return;
        const md = res?.success
          ? (res.data?.markdown_content ?? "").toString()
          : "";
        setCurrentChapterOutlineText(md);
      } catch {
        if (!aborted) setCurrentChapterOutlineText("");
      }
    })();
    return () => {
      aborted = true;
    };
  }, [chapterId]);

  /** 前文标题（最多 5 章）：从 writingChapters 中取当前章之前最近 5 个“可写”章节。
   * volume 模式下卷条目不算章节（heuristic：一旦发现 parent_id 非 null 的章节即视作
   * volume 模式，过滤掉 parent_id 为 null 的卷头）。 */
  const previousChapterTitles = React.useMemo(() => {
    if (!chapterId || writingChapters.length === 0) return [] as string[];
    const isVolumeMode = writingChapters.some((c) => c.parent_id != null);
    const writable = isVolumeMode
      ? writingChapters.filter((c) => c.parent_id != null)
      : writingChapters;
    const idx = writable.findIndex((c) => String(c.id) === String(chapterId));
    if (idx <= 0) return [];
    const start = Math.max(0, idx - 5);
    return writable.slice(start, idx).map((c) => c.title);
  }, [chapterId, writingChapters]);

  return React.useMemo(() => {
    const chapterTitles = associatedChapterIds
      .map(
        (id) =>
          chapterSelectOptions.find((o) => String(o.value) === String(id))
            ?.label ?? "",
      )
      .filter(Boolean);
    const outlineTitles = associatedOutlineIds
      .map(
        (id) =>
          outlineSelectOptions.find((o) => String(o.value) === String(id))
            ?.label ?? "",
      )
      .filter(Boolean);
    return {
      bookTitle,
      currentChapterTitle: activeChapterTitle,
      currentChapterOutline: currentChapterOutlineText,
      previousChapterTitles,
      associatedChapterTitles: chapterTitles,
      associatedOutlineTitles: outlineTitles,
    };
  }, [
    bookTitle,
    activeChapterTitle,
    currentChapterOutlineText,
    previousChapterTitles,
    associatedChapterIds,
    associatedOutlineIds,
    chapterSelectOptions,
    outlineSelectOptions,
  ]);
}

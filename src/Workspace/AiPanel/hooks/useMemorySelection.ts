import React from "react";
import type { EntityId } from "../../../types";

/**
 * 「本书设定 / 伏笔」勾选状态的共享 hook：
 * - 按 bookId 持久化到 localStorage，刷新或不同入口（主 AI 面板 / Inline 改写）共享同一份默认勾选；
 * - 切换 bookId 时自动加载对应书的勾选；
 * - 各处仍可在自己的「本次提交」过程中临时排除某些条目（局部 state），不影响这里。
 */

const STORAGE_KEY = "purrtypos_ai_memory_selection";

interface PersistedEntry {
  longTerm: string[];
  memory: (number | string)[];
  foreshadowing: (number | string)[];
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
  if (bookId == null) return { longTerm: [], memory: [], foreshadowing: [] };
  const all = loadAll();
  const entry = all[String(bookId)];
  if (!entry) return { longTerm: [], memory: [], foreshadowing: [] };
  return {
    longTerm: Array.isArray(entry.longTerm)
      ? entry.longTerm.map(String)
      : [],
    memory: Array.isArray(entry.memory) ? entry.memory : [],
    foreshadowing: Array.isArray(entry.foreshadowing) ? entry.foreshadowing : [],
  };
}

function saveForBook(bookId: EntityId | null | undefined, entry: PersistedEntry) {
  if (bookId == null) return;
  const all = loadAll();
  all[String(bookId)] = entry;
  saveAll(all);
}

export function useMemorySelection(bookId: EntityId | null | undefined) {
  const initial = React.useMemo(() => loadForBook(bookId), [bookId]);
  const [selectedMemoryIds, setSelectedMemoryIds] = React.useState<
    (number | string)[]
  >(initial.memory);
  const [selectedLongTermMemoryIds, setSelectedLongTermMemoryIds] = React.useState<
    string[]
  >(initial.longTerm);
  const [selectedForeshadowingIds, setSelectedForeshadowingIds] = React.useState<
    (number | string)[]
  >(initial.foreshadowing);

  // 切书时重新载入该书的勾选
  const lastBookKeyRef = React.useRef<string>(String(bookId ?? ""));
  React.useEffect(() => {
    const key = String(bookId ?? "");
    if (key === lastBookKeyRef.current) return;
    lastBookKeyRef.current = key;
    const next = loadForBook(bookId);
    setSelectedLongTermMemoryIds(next.longTerm);
    setSelectedMemoryIds(next.memory);
    setSelectedForeshadowingIds(next.foreshadowing);
  }, [bookId]);

  React.useEffect(() => {
    saveForBook(bookId, {
      longTerm: selectedLongTermMemoryIds,
      memory: selectedMemoryIds,
      foreshadowing: selectedForeshadowingIds,
    });
  }, [bookId, selectedLongTermMemoryIds, selectedMemoryIds, selectedForeshadowingIds]);

  return {
    selectedLongTermMemoryIds,
    setSelectedLongTermMemoryIds,
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  };
}

import React from "react";
import type { EntityId } from "../../../types";
import {
  useMemorySelectionEntry,
  writeMemorySelection,
} from "../../../stores/chatContextStore";

/**
 * 「本书设定 / 伏笔」勾选状态的共享 hook：
 * - 存储在 chatContextStore（按书持久化 + 响应式），刷新或不同入口
 *   （主 AI 面板 / Inline 弹层）共享同一份勾选，一侧变更另一侧立即可见；
 * - 切换 bookId 时 selector 自动切到对应书的条目；
 * - 各处仍可在自己的「本次提交」过程中临时排除某些条目（局部 state），不影响这里。
 */
export function useMemorySelection(bookId: EntityId | null | undefined) {
  const entry = useMemorySelectionEntry(bookId);

  const setSelectedLongTermMemoryIds = React.useCallback(
    (value: string[] | ((prev: string[]) => string[])) => {
      writeMemorySelection(bookId, {
        longTerm: Array.isArray(value) ? value : value(entry.longTerm),
        memory: entry.memory,
        foreshadowing: entry.foreshadowing,
      })
    },
    [bookId, entry],
  );
  const setSelectedMemoryIds = React.useCallback(
    (value: (number | string)[] | ((prev: (number | string)[]) => (number | string)[])) => {
      writeMemorySelection(bookId, {
        longTerm: entry.longTerm,
        memory: Array.isArray(value) ? value : value(entry.memory),
        foreshadowing: entry.foreshadowing,
      })
    },
    [bookId, entry],
  );
  const setSelectedForeshadowingIds = React.useCallback(
    (value: (number | string)[] | ((prev: (number | string)[]) => (number | string)[])) => {
      writeMemorySelection(bookId, {
        longTerm: entry.longTerm,
        memory: entry.memory,
        foreshadowing: Array.isArray(value) ? value : value(entry.foreshadowing),
      })
    },
    [bookId, entry],
  );

  return {
    selectedLongTermMemoryIds: entry.longTerm,
    setSelectedLongTermMemoryIds,
    selectedMemoryIds: entry.memory,
    setSelectedMemoryIds,
    selectedForeshadowingIds: entry.foreshadowing,
    setSelectedForeshadowingIds,
  };
}

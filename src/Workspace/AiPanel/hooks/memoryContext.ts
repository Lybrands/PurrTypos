/**
 * 用户在 AiContextBar「本书设定」按钮里勾选的设定/伏笔条目：
 * 不依赖 LLM 工具调用（后端没暴露查 spark idea 的工具，且即便有 LLM 也不知道用户选了哪些 id），
 * 直接前置 fetch 出来作为高优先级上下文注入到 system 末尾。
 *
 * 失败时返回空串而不是抛异常 —— 拉记忆失败不应阻断对话发送。
 */
export async function fetchSelectedMemoryContext(params: {
  selectedMemoryIds?: (number | string)[];
  selectedForeshadowingIds?: (number | string)[];
}): Promise<string> {
  const { selectedMemoryIds, selectedForeshadowingIds } = params;
  try {
    const memBlocks: string[] = [];
    if (selectedMemoryIds && selectedMemoryIds.length > 0) {
      const res = await window.electronAPI.getSparkIdeasByIds({
        ids: selectedMemoryIds,
      });
      if (res?.success && Array.isArray(res.data) && res.data.length > 0) {
        const lines = res.data.map(
          (m: { layer?: string; content?: string }) =>
            `- [${m.layer ?? "?"}] ${(m.content ?? "").trim()}`,
        );
        memBlocks.push(
          `【用户在本轮已勾选的本书设定 — 必须严格遵循，不得与之矛盾】\n${lines.join("\n")}`,
        );
      }
    }
    if (selectedForeshadowingIds && selectedForeshadowingIds.length > 0) {
      const res = await window.electronAPI.getForeshadowingByIds({
        ids: selectedForeshadowingIds,
      });
      if (res?.success && Array.isArray(res.data) && res.data.length > 0) {
        const lines = res.data.map(
          (f: { type?: string; status?: string; content?: string }) =>
            `- [${f.type ?? "?"}|${f.status ?? "?"}] ${(f.content ?? "").trim()}`,
        );
        memBlocks.push(
          `【用户在本轮已勾选的伏笔 — 优先呼应或铺垫，不得与之矛盾】\n${lines.join("\n")}`,
        );
      }
    }
    if (memBlocks.length === 0) return "";
    return `\n\n${memBlocks.join("\n\n")}`;
  } catch {
    return "";
  }
}

import type { ProposedSettingDiff, SettingDiffCardState } from "../../../../types";
import type { ChunkHandler } from "./types";

/**
 * AI 工具 updateCharacter / editStoryBackground 提交的设定差异提议：
 * 由 SettingDiffProvider 监听并 startDiff，用户在 SettingPanel 审阅后 commit。
 */
export const handleProposedSettingDiff: ChunkHandler = (chunk, ctx) => {
  if (!chunk.proposedSettingDiff || !ctx.isVisibleSession()) return;
  const p = chunk.proposedSettingDiff as ProposedSettingDiff;
  if (!p.kind) return;

  window.dispatchEvent(
    new CustomEvent("ai-propose-setting-diff", { detail: p }),
  );

  const sessionKey =
    p.kind === "character"
      ? `character:${p.characterId}`
      : `background:${p.bookId}`;

  const proposedName =
    p.kind === "character"
      ? p.characterName ||
        (p.proposed as { name?: string }).name ||
        (p.before as { name?: string }).name ||
        ""
      : "";
  const title =
    p.kind === "character"
      ? proposedName
        ? `人物「${proposedName}」`
        : "人物设定"
      : "故事背景";

  const card: SettingDiffCardState = {
    sessionKey,
    kind: p.kind,
    title,
    status: "pending",
  };

  ctx.setConversations((prev) => {
    const next = [...prev];
    for (let i = next.length - 1; i >= 0; i--) {
      if (next[i].role !== "assistant") continue;
      const msg = next[i];
      const cards = [...(msg.settingDiffCards || [])];
      const existingIdx = cards.findIndex((c) => c.sessionKey === sessionKey);
      if (existingIdx >= 0) cards[existingIdx] = card;
      else cards.push(card);
      next[i] = { ...msg, settingDiffCards: cards };

      if (p.kind === "character" && proposedName && msg.toolCallSegments?.length) {
        const segments = msg.toolCallSegments.map((seg) => ({
          ...seg,
          labels: seg.labels.map((label) =>
            label === "更新人物设定"
              ? `更新人物「${proposedName}」设定`
              : label,
          ),
        }));
        next[i] = { ...next[i], toolCallSegments: segments };
      }
      break;
    }
    return next;
  });
};

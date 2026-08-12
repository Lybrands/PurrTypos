import type {
  ProposedSettingDiff,
  SettingDiffCardState,
} from '../../../types'
import type {
  AgentChunkHost,
  AiStreamChunk,
} from '../../../agent-runtime/chunkHandlers/types'
import type { ChatMessage } from './chat.types'

export type BookSettingDiffAttachmentHandler = (
  message: ChatMessage,
  card: SettingDiffCardState,
) => void

/**
 * AI 工具 updateCharacter / editStoryBackground 提交的设定差异提议：
 * 由 SettingDiffProvider 监听并 startDiff，用户在 SettingPanel 审阅后 commit。
 */
export function handleProposedSettingDiff(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
  onAssistantAttachment?: BookSettingDiffAttachmentHandler,
): void {
  if (!chunk.proposedSettingDiff || !host.isVisible()) return;
  const p = chunk.proposedSettingDiff as ProposedSettingDiff;
  if (!p.kind) return;

  window.dispatchEvent(
    new CustomEvent("ai-propose-setting-diff", { detail: p }),
  );

  const sessionKey =
    p.kind === "character"
      ? `character:${p.characterId}`
      : p.kind === "entity"
        ? `entity:${p.entityId}`
        : `background:${p.bookId}`;

  const proposedName =
    p.kind === "character" || p.kind === "entity"
      ? (p.kind === "character" ? p.characterName : p.entityName) ||
        (p.proposed as { name?: string }).name ||
        (p.before as { name?: string }).name ||
        ""
      : "";
  const title =
    p.kind === "character"
      ? proposedName
        ? `人物「${proposedName}」`
        : "人物设定"
      : p.kind === "entity"
        ? proposedName
          ? `设定「${proposedName}」`
          : "世界设定"
        : "故事背景";

  const card: SettingDiffCardState = {
    sessionKey,
    kind: p.kind,
    title,
    status: "pending",
  };

  const assistant = [...host.readMessages()].reverse().find(
    (message) => message.role === 'assistant',
  )
  if (assistant) onAssistantAttachment?.(assistant, card)

  host.scheduleCommit((prev) => {
    const next = [...prev] as ChatMessage[];
    for (let i = next.length - 1; i >= 0; i--) {
      if (next[i].role !== "assistant") continue;
      const msg = next[i] as ChatMessage;
      if (p.kind === "character" && proposedName && msg.toolCallSegments?.length) {
        const segments = msg.toolCallSegments.map((seg) => ({
          ...seg,
          labels: seg.labels.map((label) =>
            label === "更新人物设定"
              ? `更新人物「${proposedName}」设定`
              : label,
          ),
        }));
        next[i] = { ...msg, toolCallSegments: segments };
      }
      break;
    }
    return next;
  });
}

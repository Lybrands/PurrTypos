import type {
  EntityId,
  ProposedSettingDiff,
  SettingDiffCardState,
} from '../../../types'
import type {
  AgentChunkHost,
  AiStreamChunk,
} from '../../../agent-runtime/chunkHandlers/types'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts'
import { settingDiffCard } from '../settingDiffProjection'

export type BookSettingDiffAttachmentHandler = (
  message: AgentConversationMessage,
  card: SettingDiffCardState,
  owner: BookSettingDiffAttachmentOwner,
) => void

export interface BookSettingDiffAttachmentOwner {
  sessionId: number
  bookId: EntityId
  chapterId: EntityId | null
  prompt: string
}

/**
 * AI 工具 updateCharacter / editStoryBackground 提交的设定差异提议：
 * 由 SettingDiffProvider 监听并 startDiff，用户在 SettingPanel 审阅后 commit。
 */
export function handleProposedSettingDiff(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
  onAssistantAttachment?: BookSettingDiffAttachmentHandler,
  owner?: BookSettingDiffAttachmentOwner,
): void {
  if (!chunk.proposedSettingDiff || !host.isVisible()) return;
  const raw = chunk.proposedSettingDiff as ProposedSettingDiff;
  const p: ProposedSettingDiff = owner && chunk.runId
    ? {
        ...raw,
        resolutionTarget: {
          sessionId: owner.sessionId,
          agentRunId: chunk.runId,
          prompt: owner.prompt,
        },
      }
    : raw
  if (!p.kind) return;

  const card = settingDiffCard(p)
  if (!card) return

  window.dispatchEvent(
    new CustomEvent("ai-propose-setting-diff", { detail: p }),
  );

  const proposedName =
    p.kind === "character" || p.kind === "entity"
      ? (p.kind === "character" ? p.characterName : p.entityName) ||
        (p.proposed as { name?: string }).name ||
        (p.before as { name?: string }).name ||
        ""
      : "";
  const assistant = [...host.readMessages()].reverse().find(
    (message) => message.role === 'assistant',
  )
  if (assistant && owner) onAssistantAttachment?.(assistant, card, owner)

  host.scheduleCommit((prev) => {
    const next = [...prev];
    for (let i = next.length - 1; i >= 0; i--) {
      if (next[i].role !== "assistant") continue;
      const msg = next[i];
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

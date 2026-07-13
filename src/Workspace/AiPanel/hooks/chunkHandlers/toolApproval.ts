import type { ToolApprovalRequest } from "../../../../types";
import type { ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

/** Add a server-issued Human-in-the-Loop approval card to the active turn. */
export const handleToolApprovalRequired: ChunkHandler = (chunk, ctx) => {
  const approval = chunk.toolApprovalRequired as ToolApprovalRequest | undefined;
  if (!approval || !ctx.isVisibleSession() || !approval.approvalId) return;

  ctx.setConversations((prev) => {
    const next = [...prev];
    for (let index = next.length - 1; index >= 0; index -= 1) {
      if (next[index].role !== "assistant") continue;
      const message = next[index] as ChatMessage;
      const approvals = [...(message.toolApprovals || [])];
      const existing = approvals.findIndex(
        (item) => item.approvalId === approval.approvalId,
      );
      if (existing >= 0) approvals[existing] = approval;
      else approvals.push(approval);
      next[index] = { ...message, toolApprovals: approvals };
      break;
    }
    return next;
  });
};

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

/** Reconcile cards with the server-owned approval lifecycle. */
export const handleToolApprovalResolved: ChunkHandler = (chunk, ctx) => {
  const resolved = chunk.toolApprovalResolved;
  if (!resolved?.approvalId || !ctx.isVisibleSession()) return;

  ctx.setConversations((prev) => {
    let changed = false;
    const next = prev.map((message) => {
      if (message.role !== "assistant" || !message.toolApprovals?.length) {
        return message;
      }
      let approvalChanged = false;
      const approvals = message.toolApprovals.map((approval) => {
        if (approval.approvalId !== resolved.approvalId) return approval;
        changed = true;
        approvalChanged = true;
        return { ...approval, status: resolved.status };
      });
      return approvalChanged ? { ...message, toolApprovals: approvals } : message;
    });
    return changed ? next : prev;
  });
};

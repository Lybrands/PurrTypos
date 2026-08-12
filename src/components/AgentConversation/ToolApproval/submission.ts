const FALLBACK_ERROR = "确认请求已过期或处理失败。";

export interface ToolApprovalSubmissionGuard {
  pending: boolean;
}

export type ToolApprovalDecisionResult =
  | { state: "approved" | "rejected" }
  | { state: "error"; error: string };

export async function submitToolApprovalDecision(
  guard: ToolApprovalSubmissionGuard,
  onResolve: (
    approvalId: string,
    approved: boolean,
  ) => Promise<{ success: boolean; error?: string }>,
  approvalId: string,
  approved: boolean,
): Promise<ToolApprovalDecisionResult | null> {
  if (guard.pending) return null;
  guard.pending = true;
  try {
    const result = await onResolve(approvalId, approved);
    if (!result.success) {
      return { state: "error", error: result.error || FALLBACK_ERROR };
    }
    return { state: approved ? "approved" : "rejected" };
  } catch {
    return { state: "error", error: FALLBACK_ERROR };
  } finally {
    guard.pending = false;
  }
}

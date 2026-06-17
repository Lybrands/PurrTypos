import type { AiStreamChunk, ChunkCtx } from "./types";
import {
  handleAgentRunStarted,
  handleAgentRunTerminal,
  handleAgentRunTodoUpdated,
  handleAgentRunTodosUpdated,
} from "./agentRun";
import {
  handleOrchestratorRepair,
  handleWritingSubagentDelta,
  handleWritingSubagentDone,
  handleWritingSubagentResult,
  handleWritingSubagentStart,
} from "./subagent";
import {
  handleCollabLatestParagraph,
  handleDelta,
  handleThinkingDelta,
  handleToolRouterWarning,
} from "./streaming";
import {
  handleChapterContentUpdated,
  handleChapterCreated,
  handleProposedChapterDiff,
  handleSettingUpdated,
} from "./sideEffects";
import { handleProposedSettingDiff } from "./settingDiff";
import { handleTaskPlan } from "./taskPlan";
import {
  handleToolCallCachedIndex,
  handleToolIndexCompleted,
  handleToolReadCacheMask,
} from "./toolProgress";
import { handleToolCallsInProgress } from "./toolStart";
import { handleDone, handleError } from "./terminal";

export type { AiStreamChunk, ChunkCtx, AccState } from "./types";

/**
 * 单个 SSE chunk 的总分发器：保持与原 onAiChunk 完全一致的执行顺序与短路语义。
 *
 * - 多数 handler 静默：返回 void → 继续走后续 if。
 * - 终态 / 缓存独立 chunk：返回 true → 整个 dispatcher 立即 return，模拟原代码的 `return;`。
 * - error / done 由 handler 自身负责清理订阅 + refs（通过 ctx.cleanup()）。
 */
export function dispatchChunk(chunk: AiStreamChunk, ctx: ChunkCtx): void {
  // 1. 独立通知 / 子专家管线 / 编排修复（原顺序，互不冲突，皆无短路）
  handleToolRouterWarning(chunk, ctx);
  handleWritingSubagentStart(chunk, ctx);
  handleWritingSubagentDelta(chunk, ctx);
  handleWritingSubagentResult(chunk, ctx);
  handleWritingSubagentDone(chunk, ctx);
  handleOrchestratorRepair(chunk, ctx);

  // 2. 错误终态：合并提示 + cleanup，必须立即停（避免后续分支二次写 state）
  if (handleError(chunk, ctx)) return;

  // 3. 流式正文 / 思考流（无短路）
  handleThinkingDelta(chunk, ctx);
  handleDelta(chunk, ctx);

  // 4. 副作用：派发 DOM 事件（无短路）
  handleChapterContentUpdated(chunk, ctx);
  handleProposedChapterDiff(chunk, ctx);
  handleProposedSettingDiff(chunk, ctx);
  handleChapterCreated(chunk, ctx);
  handleSettingUpdated(chunk, ctx);
  handleCollabLatestParagraph(chunk, ctx);
  handleAgentRunStarted(chunk, ctx);
  handleAgentRunTodosUpdated(chunk, ctx);
  handleAgentRunTodoUpdated(chunk, ctx);
  handleAgentRunTerminal(chunk, ctx);
  handleTaskPlan(chunk, ctx);

  // 5. 工具进度三连：仅当 chunk 只含进度信号时短路（与原行为一致）
  if (handleToolReadCacheMask(chunk, ctx)) return;
  if (handleToolIndexCompleted(chunk, ctx)) return;
  if (handleToolCallCachedIndex(chunk, ctx)) return;

  // 6. 工具批次开始：构造气泡段，吞掉 chunk 后续分支
  if (handleToolCallsInProgress(chunk, ctx)) return;

  // 7. 正常终态
  if (handleDone(chunk, ctx)) return;
}

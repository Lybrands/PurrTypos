import type { AiStreamChunk, ChunkCtx } from "./types";
import {
  handleLongTaskDispatched,
  handleLongTaskProgress,
} from "./durableTask";
import {
  handleChapterCreated,
  handleProposedChapterDiff,
  handleSettingUpdated,
} from "./sideEffects";
import { handleProposedSettingDiff } from "./settingDiff";
import {
  handleDone,
  handleError,
  handleRunResultTerminal,
} from "./terminal";
import { handleCanonicalOutput } from "./canonical";

export type { AiStreamChunk, ChunkCtx, AccState } from "./types";

/**
 * 单个 SSE chunk 的总分发器：保持与原 onAiChunk 完全一致的执行顺序与短路语义。
 *
 * - 多数 handler 静默：返回 void → 继续走后续 if。
 * - 终态 / 缓存独立 chunk：返回 true → 整个 dispatcher 立即 return，模拟原代码的 `return;`。
 * - error / done 由 handler 自身负责清理订阅 + refs（通过 ctx.cleanup()）。
 */
export function dispatchChunk(chunk: AiStreamChunk, ctx: ChunkCtx): void {
  // Canonical PurrA output is the only Agent content path. Transport terminal
  // notices (`done` / `error`) remain outside the journal and are handled below.
  if (handleCanonicalOutput(chunk, ctx)) return;

  if (handleRunResultTerminal(chunk, ctx)) return;

  // 1. 错误终态：合并提示 + cleanup，必须立即停（避免后续分支二次写 state）
  if (handleError(chunk, ctx)) return;

  // Product side effects remain typed host events; Agent output does not.
  handleProposedChapterDiff(chunk, ctx);
  handleProposedSettingDiff(chunk, ctx);
  handleChapterCreated(chunk, ctx);
  handleSettingUpdated(chunk, ctx);
  handleLongTaskDispatched(chunk, ctx);
  handleLongTaskProgress(chunk, ctx);

  // Transport terminal metadata settles the enclosing conversation only.
  if (handleDone(chunk, ctx)) return;
}

import type { ChatMessage } from "../chat.types";
import type { WritingSubagentRole } from "../../pipelineStages";
import type { ChunkHandler } from "./types";

export const handleWritingSubagentStart: ChunkHandler = (chunk, ctx) => {
  if (!chunk.writingSubagentStart || !ctx.isVisibleSession()) return;
  const w = chunk.writingSubagentStart as {
    role?: WritingSubagentRole;
    label?: string;
  };
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...(last as ChatMessage),
      writingSubagentActive: true,
      writingSubagentLabel: w.label,
      writingSubagentRole: w.role,
    };
    return next;
  });
};

export const handleWritingSubagentDelta: ChunkHandler = (chunk, ctx) => {
  const delta = chunk.writingSubagentDelta?.delta;
  if (!delta) return;
  ctx.acc.response += delta;
  if (!ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...last,
      content: (last.content || "") + delta,
    };
    return next;
  });
};

export const handleWritingSubagentResult: ChunkHandler = (chunk, ctx) => {
  if (!chunk.writingSubagentResult) return;
  const wr = chunk.writingSubagentResult as {
    role: WritingSubagentRole;
    payload: unknown;
  };
  ctx.acc.subagentResult = { role: wr.role, payload: wr.payload };

  if (!ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...(last as ChatMessage),
      subagentResult: { role: wr.role, payload: wr.payload },
    };
    return next;
  });
};

export const handleWritingSubagentDone: ChunkHandler = (chunk, ctx) => {
  if (!chunk.writingSubagentDone || !ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...(last as ChatMessage),
      writingSubagentActive: false,
    };
    return next;
  });
};

export const handleOrchestratorRepair: ChunkHandler = (chunk, ctx) => {
  if (!chunk.orchestratorRepair?.repairedRounds) return;
  if (!ctx.isVisibleSession()) return;
  const repairedRounds = chunk.orchestratorRepair.repairedRounds;
  const repairReasons = (chunk.orchestratorRepair?.events || [])
    .map((x) => String(x?.reason || "").trim())
    .filter(Boolean);
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const lastMsg = next[next.length - 1];
    if (lastMsg?.role !== "assistant") return prev;
    const segs = (lastMsg as ChatMessage).toolCallSegments ?? [];
    if (segs.length === 0) return prev;
    const lastSeg = segs[segs.length - 1];
    const nextSegs = [
      ...segs.slice(0, -1),
      {
        ...lastSeg,
        trace: {
          ...(lastSeg.trace ?? {}),
          repairedRounds,
          repairReasons,
        },
      },
    ];
    next[next.length - 1] = {
      ...(lastMsg as ChatMessage),
      toolCallSegments: nextSegs,
    };
    return next;
  });
};

import type React from "react";

import type { AiAgentDelegation } from "../../../../types";
import type {
  AiSubAgentActivity,
  ChatMessage,
} from "../chat.types";
import type {
  AccState,
  AiStreamChunk,
  ChunkCtx,
} from "./types";

type SubRunEnvelope = NonNullable<AiStreamChunk["agentSubRunEvent"]>;
type NestedDispatcher = (chunk: AiStreamChunk, ctx: ChunkCtx) => void;

function activityStatus(
  chunk: AiStreamChunk,
): AiAgentDelegation["status"] {
  if (chunk.agentRunCompleted) return "done";
  if (chunk.agentRunFailed) return "failed";
  if (chunk.agentRunBlocked) return "failed";
  if (chunk.agentRunCanceled) return "canceled";
  return "running";
}

function createChildMessage(envelope: SubRunEnvelope): ChatMessage {
  return {
    role: "assistant",
    content: "",
    agentRunId: envelope.childRunId || undefined,
    turnStartedAt: performance.now(),
  };
}

function createActivity(envelope: SubRunEnvelope): AiSubAgentActivity {
  return {
    delegationId: envelope.delegationId,
    parentRunId: envelope.parentRunId,
    rootRunId: envelope.rootRunId,
    childRunId: envelope.childRunId,
    agentRole: envelope.agentRole,
    agentTitle: envelope.agentTitle,
    objective: envelope.objective,
    status: "running",
    message: createChildMessage(envelope),
  };
}

function upsertActivity(
  activities: AiSubAgentActivity[] | undefined,
  envelope: SubRunEnvelope,
  updater?: (activity: AiSubAgentActivity) => AiSubAgentActivity,
): AiSubAgentActivity[] {
  const current = activities ?? [];
  const index = current.findIndex(
    (item) => item.delegationId === envelope.delegationId,
  );
  const base = index >= 0 ? current[index] : createActivity(envelope);
  const nextActivity = updater ? updater(base) : base;
  const normalized: AiSubAgentActivity = {
    ...nextActivity,
    parentRunId: envelope.parentRunId || nextActivity.parentRunId,
    rootRunId: envelope.rootRunId || nextActivity.rootRunId,
    childRunId: envelope.childRunId || nextActivity.childRunId,
    agentRole: envelope.agentRole || nextActivity.agentRole,
    agentTitle: envelope.agentTitle || nextActivity.agentTitle,
    objective: envelope.objective || nextActivity.objective,
  };
  if (index < 0) return [...current, normalized];
  return current.map((item, itemIndex) =>
    itemIndex === index ? normalized : item,
  );
}

function updateLastAssistantActivity(
  messages: ChatMessage[],
  envelope: SubRunEnvelope,
  updater?: (activity: AiSubAgentActivity) => AiSubAgentActivity,
): ChatMessage[] {
  const next = [...messages];
  const last = next[next.length - 1];
  if (!last || last.role !== "assistant") return messages;
  next[next.length - 1] = {
    ...last,
    subAgentActivities: upsertActivity(
      last.subAgentActivities,
      envelope,
      updater,
    ),
  };
  return next;
}

function childAccumulator(
  ctx: ChunkCtx,
  envelope: SubRunEnvelope,
): AccState {
  const accumulators = ctx.acc.subAgentAccumulators ?? {};
  ctx.acc.subAgentAccumulators = accumulators;
  const existing = accumulators[envelope.delegationId];
  if (existing) {
    existing.agentRunId = envelope.childRunId || existing.agentRunId;
    return existing;
  }
  const created: AccState = {
    response: "",
    commentary: "",
    bookId: ctx.acc.bookId,
    sessionId: ctx.acc.sessionId,
    chapterId: ctx.acc.chapterId,
    needsTitle: false,
    userText: envelope.objective || "",
    model: ctx.acc.model,
    turnStartedAt: performance.now(),
    agentRunId: envelope.childRunId || undefined,
    commentaryBlocks: [],
    commentaryDurationsMs: [],
  };
  accumulators[envelope.delegationId] = created;
  return created;
}

function messageFromAccumulator(
  message: ChatMessage,
  acc: AccState,
): ChatMessage {
  return {
    ...message,
    content: acc.response || acc.pendingFinalResponse || message.content,
    agentRunId: acc.agentRunId || message.agentRunId,
    commentary: acc.commentary || message.commentary,
    commentaryBlocks: acc.commentaryBlocks,
    commentaryDurationsMs: acc.commentaryDurationsMs,
    toolCallSegments: acc.toolCallSegments,
    taskPlan: acc.taskPlan,
    delegations: acc.delegations,
    contextCompaction: acc.contextCompaction,
    contextBudget: acc.contextBudget,
  };
}

function scopedSetConversations(
  ctx: ChunkCtx,
  envelope: SubRunEnvelope,
): React.Dispatch<React.SetStateAction<ChatMessage[]>> {
  return (nextState) => {
    ctx.setConversations((rootMessages) =>
      updateLastAssistantActivity(rootMessages, envelope, (activity) => {
        const childMessages = [activity.message];
        const nextMessages = typeof nextState === "function"
          ? nextState(childMessages)
          : nextState;
        const nextMessage = nextMessages[nextMessages.length - 1];
        const nextActivity = {
          ...activity,
          message: nextMessage?.role === "assistant"
            ? nextMessage
            : activity.message,
        };
        ctx.acc.subAgentActivities = upsertActivity(
          ctx.acc.subAgentActivities,
          envelope,
          () => nextActivity,
        );
        return nextActivity;
      }),
    );
  };
}

function scopedScheduleCommit(
  ctx: ChunkCtx,
  envelope: SubRunEnvelope,
): ChunkCtx["scheduleCommit"] {
  return (updater) => {
    ctx.scheduleCommit((rootMessages) =>
      updateLastAssistantActivity(rootMessages, envelope, (activity) => {
        const nextMessages = updater([activity.message]);
        const nextMessage = nextMessages[nextMessages.length - 1];
        const nextActivity = {
          ...activity,
          message: nextMessage?.role === "assistant"
            ? nextMessage
            : activity.message,
        };
        ctx.acc.subAgentActivities = upsertActivity(
          ctx.acc.subAgentActivities,
          envelope,
          () => nextActivity,
        );
        return nextActivity;
      }),
    );
  };
}

/**
 * Route one canonical child chunk through the ordinary chat reducer while
 * scoping all state writes to that delegation's own activity block.
 */
export function handleAgentSubRunEvent(
  chunk: AiStreamChunk,
  ctx: ChunkCtx,
  dispatchNested: NestedDispatcher,
): boolean {
  const envelope = chunk.agentSubRunEvent;
  if (!envelope?.delegationId || !envelope.chunk) return false;
  const childChunk = envelope.chunk as AiStreamChunk;
  const acc = childAccumulator(ctx, envelope);
  ctx.acc.subAgentActivities = upsertActivity(
    ctx.acc.subAgentActivities,
    envelope,
    (activity) => ({
      ...activity,
      status: activityStatus(childChunk),
    }),
  );

  ctx.scheduleCommit((messages) =>
    updateLastAssistantActivity(messages, envelope, (activity) => ({
      ...activity,
      status: activityStatus(childChunk),
    })),
  );

  const childCtx: ChunkCtx = {
    ...ctx,
    acc,
    setConversations: scopedSetConversations(ctx, envelope),
    scheduleCommit: scopedScheduleCommit(ctx, envelope),
    setLoading: () => undefined,
    persistConversation: false,
    cleanup: () => undefined,
  };
  dispatchNested(childChunk, childCtx);
  const reflectAccumulator = (activity: AiSubAgentActivity) => ({
    ...activity,
    status: activityStatus(childChunk),
    message: messageFromAccumulator(activity.message, acc),
  });
  ctx.acc.subAgentActivities = upsertActivity(
    ctx.acc.subAgentActivities,
    envelope,
    reflectAccumulator,
  );
  // Final-answer deltas are intentionally buffered by the shared reducer.
  // Mirror the child accumulator into its scoped state so it becomes visible
  // when the delegation itself reaches done, while the active timeline gate
  // keeps it hidden during execution.
  ctx.scheduleCommit((messages) =>
    updateLastAssistantActivity(messages, envelope, reflectAccumulator),
  );
  return true;
}

import type React from "react";
import type { App as AntdApp } from "antd";
import type {
  AiModelConfig,
  AiSession,
  ElectronAPI,
  EntityId,
  Outline,
} from "../../../../types";
import type {
  AiTaskPlan,
  ChatMessage,
  ToolCallSegment,
} from "../chat.types";
import type { ConversationUpdater } from "./commitScheduler";

/** 主进程 SSE chunk 的真实类型（直接从 ElectronAPI 接口提取，避免重复声明漂移） */
export type AiStreamChunk = Parameters<
  Parameters<ElectronAPI["onAiChunk"]>[0]
>[0];

/** Antd 的 message API（来自 AntdApp.useApp().message） */
export type AppMessage = ReturnType<typeof AntdApp.useApp>["message"];

/** 单次 handleSubmit 调用期间在 onAiChunk 各分支共享的累加状态 */
export interface AccState {
  response: string;
  thinking: string;
  bookId: EntityId | null | undefined;
  sessionId: number;
  chapterId: EntityId | null | undefined;
  needsTitle: boolean;
  userText: string;
  model: string;
  turnStartedAt: number;
  toolCallSegments?: ToolCallSegment[];
  thinkingBlocks?: string[];
  thinkingDurationsMs?: number[];
  contentAfterToolCalls?: string;
  /** 当前思考块开始时间（performance.now），用于计算 thinkingDurationsMs */
  thinkingBlockStartedAt?: number;
  agentRunId?: string;
  taskPlan?: AiTaskPlan;
}

/**
 * Chunk handler 共享上下文：把原本 onAiChunk 闭包里的所有外部依赖打成显式对象，
 * 便于把每个分支抽成独立可测的纯函数（除 React 副作用外的输入都可注入）。
 */
export interface ChunkCtx {
  acc: AccState;
  sessionId: number;
  cfg: AiModelConfig;
  apiModelName: string;
  writingChapters: { id: EntityId; title: string }[];
  availableOutlines: Outline[];

  // React 写入入口
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  scheduleCommit: (updater: ConversationUpdater) => void;
  flushCommits: () => void;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  setSessions: React.Dispatch<React.SetStateAction<AiSession[]>>;
  appMessage: AppMessage;

  // 跨会话守卫
  isVisibleSession: () => boolean;

  /**
   * 终态（done / error）时调用：取消订阅、清空 running refs、把 loading 关掉等。
   * 由 useChatSubmit 在创建 ctx 时注入，封装住 unsubscribe / *Ref / setLoading 的清理顺序。
   */
  cleanup: () => void;
}

/**
 * Handler 签名：返回 true 表示该 chunk 属于"终态/短路"，主 dispatcher 应立刻 return；
 * 返回 void/false 表示继续走后续 if 分支。
 */
export type ChunkHandler = (
  chunk: AiStreamChunk,
  ctx: ChunkCtx,
) => boolean | void;

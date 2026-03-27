import React from "react";
import { flushSync } from "react-dom";
import { App as AntdApp } from "antd";
import type { AiModelConfig, Outline, AiSession, EntityId } from "../../../types";

function normalizeSubagentStageName(stage?: string): string {
  switch (String(stage || "").trim()) {
    case "analyze":
      return "分析专家";
    case "plan":
      return "规划专家";
    case "draft":
      return "撰稿专家";
    case "review":
      return "审校专家";
    case "polish":
      return "润色专家";
    case "styleUnify":
      return "风格统一专家";
    default:
      return String(stage || "").trim();
  }
}

export type ToolCallLabelOutcome = "ok" | "context_error";

function resolveChapterTitleInCatalog(
  chapterId: EntityId | undefined,
  writingChapters: { id: EntityId; title: string }[],
): string | undefined {
  if (chapterId == null || String(chapterId).trim() === "") return undefined;
  const key = String(chapterId);
  const ch = writingChapters.find((c) => String(c.id) === key);
  const t = ch?.title?.trim();
  return t || undefined;
}

function resolveChapterTitleByIndexInCatalog(
  chapterIndex: unknown,
  writingChapters: { id: EntityId; title: string }[],
): string | undefined {
  const idx = Number(chapterIndex);
  if (!Number.isInteger(idx) || idx <= 0) return undefined;
  const row = writingChapters[idx - 1];
  const t = String(row?.title || "").trim();
  return t || undefined;
}

function resolveOutlineTitleInCatalog(
  outlineId: EntityId | undefined,
  availableOutlines: Outline[],
): string | undefined {
  if (outlineId == null || String(outlineId).trim() === "") return undefined;
  const key = String(outlineId).trim();
  const row = availableOutlines.find((o) => String(o.id) === key);
  const t = row?.title?.trim();
  return t || undefined;
}

/** 工具气泡文案；章节类若在本地章节目录无对应标题则 outcome=context_error（视为参数/上下文有误） */
function toolCallDisplayRow(
  name: string,
  args: Record<string, unknown>,
  writingChapters: { id: EntityId; title: string }[],
  availableOutlines: Outline[],
): { label: string; outcome: ToolCallLabelOutcome } {
  try {
    switch (name) {
      case "getChapterContent": {
        const chapterTitleArg =
          args.chapterTitle != null && String(args.chapterTitle).trim() !== ""
            ? String(args.chapterTitle).trim()
            : "";
        if (chapterTitleArg) {
          return { label: `查看《${chapterTitleArg}》章节内容`, outcome: "ok" };
        }
        const titleByIndex = resolveChapterTitleByIndexInCatalog(
          args.chapterIndex,
          writingChapters,
        );
        if (titleByIndex) {
          return { label: `查看《${titleByIndex}》章节内容`, outcome: "ok" };
        }
        const cid =
          args.chapterId != null && args.chapterId !== ""
            ? String(args.chapterId).trim()
            : "";
        if (!cid) {
          return { label: "查看章节内容", outcome: "ok" };
        }
        const title = resolveChapterTitleInCatalog(cid, writingChapters);
        return { label: title ? `查看《${title}》章节内容` : "查看章节内容", outcome: "ok" };
      }
      case "editChapterContent": {
        const chapterTitleArg =
          args.chapterTitle != null && String(args.chapterTitle).trim() !== ""
            ? String(args.chapterTitle).trim()
            : "";
        if (chapterTitleArg) {
          return { label: `编辑《${chapterTitleArg}》章节内容`, outcome: "ok" };
        }
        const titleByIndex = resolveChapterTitleByIndexInCatalog(
          args.chapterIndex,
          writingChapters,
        );
        if (titleByIndex) {
          return { label: `编辑《${titleByIndex}》章节内容`, outcome: "ok" };
        }
        const cid =
          args.chapterId != null && args.chapterId !== ""
            ? String(args.chapterId).trim()
            : "";
        if (!cid) {
          return { label: "编辑章节内容", outcome: "ok" };
        }
        const title = resolveChapterTitleInCatalog(cid, writingChapters);
        return { label: title ? `编辑《${title}》章节内容` : "编辑章节内容", outcome: "ok" };
      }
      case "batchGetChapterContents": {
        const raw = args.chapterIds;
        if (!Array.isArray(raw) || raw.length === 0) {
          return { label: "查看多章内容", outcome: "ok" };
        }
        const ids = raw
          .map((x) => String(x).trim())
          .filter((s) => s !== "");
        if (ids.length === 0) {
          return {
            label: "查看多章内容",
            outcome: "context_error",
          };
        }
        const bad = ids.filter((id) => !resolveChapterTitleInCatalog(id, writingChapters));
        if (bad.length > 0) {
          return {
            label: "查看多章内容",
            outcome: "context_error",
          };
        }
        if (ids.length === 1) {
          const t0 = resolveChapterTitleInCatalog(ids[0], writingChapters)!;
          return { label: `查看《${t0}》章节内容`, outcome: "ok" };
        }
        const head = ids
          .slice(0, 3)
          .map((id) => `《${resolveChapterTitleInCatalog(id, writingChapters)}》`)
          .join("");
        if (ids.length <= 3) return { label: `查看${head}等多章内容`, outcome: "ok" };
        return { label: `查看${head}等 ${ids.length} 章内容`, outcome: "ok" };
      }
      case "listWritingChapters":
        return { label: "查看章节目录", outcome: "ok" };
      case "createWritingChapter":
        return { label: "创建章节", outcome: "ok" };
      case "getBookCharacters":
        return { label: "查看人物信息", outcome: "ok" };
      case "listBookCharacters":
        return { label: "查看人物列表", outcome: "ok" };
      case "getStoryBackground":
        return { label: "查看小说背景", outcome: "ok" };
      case "getGlobalOutline":
        return { label: "查看总纲", outcome: "ok" };
      case "editGlobalOutline":
        return { label: "编辑总纲", outcome: "ok" };
      case "queryOutline":
        {
          const outlineIdArg =
            args.outlineId != null && String(args.outlineId).trim() !== ""
              ? String(args.outlineId).trim()
              : "";
          if (outlineIdArg) {
            const title = resolveOutlineTitleInCatalog(outlineIdArg, availableOutlines);
            if (title) return { label: `查看《${title}》大纲详情`, outcome: "ok" };
            return { label: "查看大纲详情", outcome: "context_error" };
          }
          if (Array.isArray(args.outlineIds) && args.outlineIds.length > 0) {
            const ids = args.outlineIds
              .map((x) => String(x).trim())
              .filter((s) => s !== "");
            if (ids.length === 1) {
              const title = resolveOutlineTitleInCatalog(ids[0], availableOutlines);
              if (title) return { label: `查看《${title}》大纲详情`, outcome: "ok" };
              return { label: "查看大纲详情", outcome: "context_error" };
            }
            const titles = ids
              .map((id) => resolveOutlineTitleInCatalog(id, availableOutlines))
              .filter((t): t is string => Boolean(t));
            if (titles.length > 0) {
              const head = titles.slice(0, 3).map((t) => `《${t}》`).join("");
              if (titles.length <= 3) return { label: `查看${head}等多条大纲详情`, outcome: "ok" };
              return { label: `查看${head}等 ${titles.length} 条大纲详情`, outcome: "ok" };
            }
          }
          return { label: "查看大纲详情", outcome: "context_error" };
        }
      case "listOutlines":
        return { label: "查看大纲列表", outcome: "ok" };
      case "updateOutline":
        return { label: "更新大纲", outcome: "ok" };
      case "addMemory":
        return { label: "添加长期记忆", outcome: "ok" };
      case "searchMemories":
        return { label: "检索长期记忆", outcome: "ok" };
      case "addForeshadowing":
        return { label: "添加伏笔", outcome: "ok" };
      default:
        return { label: name, outcome: "ok" };
    }
  } catch {
    return { label: name, outcome: "ok" };
  }
}

/** 写作专家模式：助手轮仅有 tool 气泡、正文为空时，拼出可供模型阅读的摘要，避免主会话上下文断裂 */
function synthesizeAssistantTextFromToolSegments(msg: ChatMessage): string {
  const segs = msg.toolCallSegments;
  if (!segs?.length) return "";
  const parts: string[] = [];
  for (const s of segs) {
    const tb = (s.textBefore || "").trim();
    if (tb) parts.push(tb);
    const labels = (s.labels || []).filter(Boolean);
    if (labels.length) parts.push(`[已调用工具] ${labels.join("、")}`);
  }
  const after = (msg.contentAfterToolCalls || "").trim();
  if (after) parts.push(after);
  return parts.join("\n");
}

/** 一段「调用前文案 + 该次调用的正在查看列表」，按调用顺序排列 */
export interface ToolCallSegment {
  textBefore: string;
  labels: string[];
  /** 与 labels 同长度：目录/参数无法与当前书籍对齐时标记 context_error，气泡显示为失败 */
  labelOutcomes?: ToolCallLabelOutcome[];
  /** 与 labels 同长度：该次工具调用是否命中请求内只读缓存 */
  cachedFlags?: boolean[];
  /** 本段内已执行完成的工具数量（与后端 toolIndexCompleted 同步，顺序递增） */
  completedToolCount?: number;
  trace?: {
    insertedByDag?: number;
    insertedSkillNames?: string[];
    plannedToolNames?: string[];
    repairedRounds?: number;
    repairReasons?: string[];
    /** 当前工具执行阶段（如 subagent 的 analyze/plan） */
    stage?: string;
  };
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  isError?: boolean;
  model?: string;
  /** 当前/最后一轮思考（流式时持续追加） */
  thinking?: string;
  /** 多轮思考内容，与 toolCallSegments 交错：思考1、工具1、思考2、工具2… */
  thinkingBlocks?: string[];
  toolCalling?: boolean;
  toolCallSegments?: ToolCallSegment[];
  contentAfterToolCalls?: string;
  /** Subagent：当前阶段 id（如 analyze） */
  subagentStageId?: string;
  /** 写作专家模式：当前阶段展示名（如「分析专家」） */
  subagentStageName?: string;
  /** Subagent：该阶段是否仍在执行（含工具调用） */
  subagentStageWorking?: boolean;
  /** Subagent：最近一次完成阶段的展示名 */
  subagentLastCompletedStageName?: string;
  /** Subagent：按返回顺序记录阶段状态 */
  subagentStages?: Array<{
    id: string;
    name: string;
    status: "running" | "done";
  }>;
  /** 主稿专家正在输出阶段间过渡文案 */
  subagentBridging?: boolean;
  /** 已进入最终主稿专家答复流 */
  subagentMainPresenter?: boolean;
}

export interface UseChatSubmitParams {
  /** 当前选中的模型配置（含 apiKey、baseUrl、name）；为空时无法发送 */
  selectedModelConfig: AiModelConfig | null;
  prompt: string;
  setPrompt: React.Dispatch<React.SetStateAction<string>>;
  loading: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  conversations: ChatMessage[];
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  bookId: EntityId | null | undefined;
  bookTitle?: string;
  chapterId: EntityId | null | undefined;
  activeSessionId: number | null;
  setActiveSessionId: React.Dispatch<React.SetStateAction<number | null>>;
  sessions: AiSession[];
  setSessions: React.Dispatch<React.SetStateAction<AiSession[]>>;
  associatedChapterIds: EntityId[];
  associatedOutlineIds: EntityId[];
  writingChapters: { id: EntityId; title: string }[];
  availableOutlines: Outline[];
  currentChapterTitle?: string;
  selectedModel: string;
  thinkingEnabled: boolean;
  agentEnabled: boolean;
  /** 用于 max_tokens 等；temperature 由选中模型的 AiModelConfig 与思考开关决定 */
  modelConfigs: Record<string, { label?: string; max_tokens?: number }>;
  selectedMemoryIds?: (number | string)[];
  selectedForeshadowingIds?: (number | string)[];
  agentMode?: "legacy" | "subagent";
  /** legacy 下协作共创时传 collab，主进程注入协商提示并限制写入工具 */
  writingMode?: "default" | "collab";
  /** 写作专家管线多选阶段；默认 ['full'] */
  agentActions?: string[];
}

export function useChatSubmit(params: UseChatSubmitParams) {
  const {
    selectedModelConfig,
    prompt,
    setPrompt,
    loading,
    setLoading,
    conversations,
    setConversations,
    bookId,
    bookTitle,
    chapterId,
    activeSessionId,
    setActiveSessionId,
    sessions,
    setSessions,
    associatedChapterIds,
    associatedOutlineIds,
    writingChapters,
    availableOutlines,
    currentChapterTitle,
    selectedModel,
    thinkingEnabled,
    agentEnabled,
    modelConfigs,
    selectedMemoryIds,
    selectedForeshadowingIds,
    agentMode = "legacy",
    writingMode = "default",
    agentActions = ["full"],
  } = params;

  const { message: appMessage } = AntdApp.useApp();
  const unsubscribeRef = React.useRef<(() => void) | null>(null);
  const visibleSessionIdRef = React.useRef<number | null>(activeSessionId);
  const runningSessionIdRef = React.useRef<number | null>(null);
  const runningAccRef = React.useRef<{
    response: string;
    thinking: string;
    userText: string;
    toolCallSegments?: ToolCallSegment[];
    thinkingBlocks?: string[];
  } | null>(null);

  React.useEffect(() => {
    visibleSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  const handleAbort = React.useCallback(() => {
    window.electronAPI.abortAiStream();
    // 不在此处 unsubscribe：须等主进程发来 done（含 aborted），才能合并状态并入库
    setLoading(false);
  }, [setLoading]);

  /** 可选：从某条用户消息重新编辑并发送，会丢弃该条之后的所有消息 */
  const handleSubmit = React.useCallback(
    async (resend?: { editIndex: number; content: string }) => {
      const userText = (resend?.content ?? prompt).trim();
      if (!userText || loading) return;

      const cfg = selectedModelConfig;
      const forceNoThinking = agentMode === "subagent";
      const expectThinking = forceNoThinking
        ? false
        : cfg
          ? cfg.thinkingOnly || (cfg.supportsThinking && thinkingEnabled)
          : thinkingEnabled;
      const assistantPlaceholder = {
        role: "assistant" as const,
        content: "",
        ...(expectThinking ? { thinking: "" } : {}),
      };

      if (!cfg?.apiKey?.trim()) {
        if (resend != null) {
          setConversations((prev) => [
            ...prev.slice(0, resend.editIndex),
            { role: "user", content: resend.content.trim() },
            {
              role: "assistant",
              content: "⚠️ 请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        } else {
          setConversations((prev) => [
            ...prev,
            { role: "user", content: userText },
            {
              role: "assistant",
              content: "⚠️ 请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        }
        setPrompt("");
        return;
      }
      if (bookId == null || chapterId == null) {
        appMessage.warning("请先选择一个章节，再开始对话");
        return;
      }
      if (activeSessionId == null) {
        appMessage.warning("请先点击上方「+」新建对话，或从历史记录打开会话");
        return;
      }
      const sessionId = activeSessionId;

    // 先立刻把用户消息 + 助手占位推到 UI，并进入 loading，再去做拉记忆等异步
    if (resend != null) {
      const nextConversations = [
        ...conversations.slice(0, resend.editIndex),
        { role: "user" as const, content: resend.content.trim() },
        assistantPlaceholder,
      ];
      setConversations(nextConversations);
      setPrompt("");
      setLoading(true);
    } else {
      setConversations((prev) => [
        ...prev,
        { role: "user", content: userText },
        assistantPlaceholder,
      ]);
      setPrompt("");
      setLoading(true);
    }

    const bookName = bookTitle?.trim() || "（未命名）";
    const chapterName = currentChapterTitle?.trim() || "（未选章节）";
    const systemSuffix =
      bookId != null
        ? agentEnabled
          ? `\n\n当前书籍：《${bookName}》；当前写作章节：《${chapterName}》。需要 bookId/chapterId/outlineId 的工具参数由宿主按当前界面自动注入；若需操作**非当前**章节或大纲，只能先读取列表中的真实 id，再传 **chapterId** / **outlineId(outlineIds)**。不支持 chapterTitle/chapterIndex/outlineTitle/outlineIndex。勿猜测数据库 id。向用户回复时使用书名、章节名，不要暴露 id。`
          : `\n\n当前书籍：《${bookName}》；当前写作章节：《${chapterName}》。你无法访问书籍内容，仅能基于用户描述或用户主动提供的信息作答。回复时使用名称，不暴露 id。`
        : "";

    const toHistoryApiMessage = (
      m: ChatMessage | { role: "user" | "assistant"; content: string },
    ): { role: string; content: string } | null => {
      const cm = m as ChatMessage;
      if (cm.isError || cm.role === "system") return null;
      let text = String(cm.content ?? "").trim();
      if (
        !text &&
        agentMode === "subagent" &&
        cm.role === "assistant"
      ) {
        text = synthesizeAssistantTextFromToolSegments(cm).trim();
      }
      if (!text) return null;
      return { role: cm.role, content: text };
    };

    let subagentExtra = "";
    if (agentMode === "subagent" && agentEnabled && bookId != null) {
      const extra: string[] = [];
      if (associatedChapterIds.length > 0 && writingChapters.length > 0) {
        const bits = associatedChapterIds.map((id) => {
          const c = writingChapters.find((w) => w.id === id);
          return c ? `《${c.title}》` : `（未在目录中匹配的关联项）`;
        });
        extra.push(`用户在本轮对话中关联的写作章节：${bits.join("、")}。`);
      }
      if (associatedOutlineIds.length > 0 && availableOutlines.length > 0) {
        const bits = associatedOutlineIds.map((oid) => {
          const o = availableOutlines.find((x) => x.id === oid);
          return o ? `《${o.title}》` : `（未在列表中匹配的关联项）`;
        });
        extra.push(`用户在本轮对话中关联的大纲：${bits.join("、")}。`);
      }
      if (extra.length > 0) {
        subagentExtra = `\n\n【写作专家 — 主会话附加上下文】\n${extra.join("\n")}`;
      }
    }

    let collabExtra = "";
    if (writingMode === "collab" && agentEnabled && bookId != null) {
      const extra: string[] = [];
      if (associatedChapterIds.length > 0 && writingChapters.length > 0) {
        const bits = associatedChapterIds.map((id) => {
          const c = writingChapters.find((w) => w.id === id);
          return c ? `《${c.title}》` : `（未在目录中匹配的关联项）`;
        });
        extra.push(`用户在本轮对话中关联的写作章节：${bits.join("、")}。`);
      }
      if (associatedOutlineIds.length > 0 && availableOutlines.length > 0) {
        const bits = associatedOutlineIds.map((oid) => {
          const o = availableOutlines.find((x) => x.id === oid);
          return o ? `《${o.title}》` : `（未在列表中匹配的关联项）`;
        });
        extra.push(`用户在本轮对话中关联的大纲：${bits.join("、")}。`);
      }
      if (extra.length > 0) {
        collabExtra = `\n\n【协作共创 — 主会话附加上下文】\n${extra.join("\n")}`;
      }
    }

    // 长期记忆由工具调用提供，不再拼入 system
    const systemContent = [systemSuffix, subagentExtra, collabExtra].filter(Boolean).join("");

    let historyMessages: { role: string; content: string }[];
    if (resend != null) {
      const nextConversations = [
        ...conversations.slice(0, resend.editIndex),
        { role: "user" as const, content: resend.content.trim() },
        assistantPlaceholder,
      ];
      historyMessages = nextConversations
        .slice(0, -1)
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null)
        .slice(-50);
      // 从数据库删除「该条之后」的对话记录，与界面截断一致
      const keepTurnCount = Math.floor(resend.editIndex / 2);
      if (sessionId != null && keepTurnCount >= 0) {
        window.electronAPI.deleteConversationsAfterTurn({ sessionId, keepTurnCount }).catch(() => {});
      }
    } else {
      historyMessages = conversations
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null)
        .slice(-50);
    }

    const newMessages = [
      { role: "system", content: systemContent },
      ...historyMessages,
      { role: "user", content: userText },
    ];

    const currentSessionTitle =
      sessions.find((s) => s.id === sessionId)?.title ?? "";
    const hasHistoryBeforeThisQuestion = historyMessages.length > 0;
    const isUntitledSession =
      currentSessionTitle.trim() === "" || currentSessionTitle === "新对话";
    const needsTitle =
      !hasHistoryBeforeThisQuestion && isUntitledSession;
    const acc = {
      response: "",
      thinking: "",
      sessionId,
      chapterId,
      needsTitle,
      userText,
      model: "" as string,
      toolCallSegments: undefined as ToolCallSegment[] | undefined,
      thinkingBlocks: [] as string[],
    };
    runningSessionIdRef.current = sessionId;
    runningAccRef.current = acc;

    const unsubscribe = window.electronAPI.onAiChunk((chunk) => {
      const isVisibleSession = () => visibleSessionIdRef.current === sessionId;
      if (chunk.toolRouterWarning) {
        appMessage.warning(chunk.toolRouterWarning);
      }
      if (chunk.subagentBridging === true || chunk.subagentBridging === false) {
        if (isVisibleSession()) {
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (!last || last.role !== "assistant") return prev;
              next[next.length - 1] = {
                ...(last as ChatMessage),
                subagentBridging: chunk.subagentBridging === true,
              };
              return next;
            });
          });
        }
      }
      if (chunk.subagentMainPresenter) {
        if (isVisibleSession()) {
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (!last || last.role !== "assistant") return prev;
              next[next.length - 1] = {
                ...(last as ChatMessage),
                subagentMainPresenter: true,
              };
              return next;
            });
          });
        }
      }
      const skipSubagentStageUiForPayloadOnly =
        agentMode === "subagent" &&
        chunk.subagentStageStarting !== true &&
        (chunk.subagentPayload !== undefined ||
          chunk.subagentPayloadMeta !== undefined);
      if (
        (chunk.subagentStage || chunk.subagentStageName) &&
        !skipSubagentStageUiForPayloadOnly
      ) {
        if (isVisibleSession()) {
          const stageId = chunk.subagentStage;
          const stageName =
            chunk.subagentStageName ||
            normalizeSubagentStageName(chunk.subagentStage);
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (!last || last.role !== "assistant") return prev;
              const prevStages = (last as ChatMessage).subagentStages ?? [];
              const normalizedId = String(stageId || "").trim();
              const existingIdx = normalizedId
                ? prevStages.findIndex((s) => s.id === normalizedId)
                : -1;
              let nextStages = prevStages.map((s) =>
                s.status === "running" ? { ...s, status: "done" as const } : s,
              );
              if (existingIdx >= 0) {
                nextStages = nextStages.map((s, i) =>
                  i === existingIdx
                    ? {
                        ...s,
                        name: stageName || s.name,
                        status: "running" as const,
                      }
                    : s,
                );
              } else if (normalizedId || stageName) {
                nextStages = [
                  ...nextStages,
                  {
                    id: normalizedId || stageName,
                    name: stageName || normalizedId,
                    status: "running" as const,
                  },
                ];
              }
              next[next.length - 1] = {
                ...(last as ChatMessage),
                subagentStageId: stageId || (last as ChatMessage).subagentStageId,
                subagentStageName:
                  stageName || (last as ChatMessage).subagentStageName,
                subagentStageWorking: true,
                subagentStages: nextStages,
              };
              return next;
            });
          });
        }
      }
      if (chunk.subagentStageDone && chunk.subagentStageName) {
        if (isVisibleSession()) {
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (!last || last.role !== "assistant") return prev;
              const prevStages = (last as ChatMessage).subagentStages ?? [];
              const doneStageId = String(chunk.subagentStageDone || "").trim();
              let found = false;
              let nextStages = prevStages.map((s) => {
                if (
                  (doneStageId && s.id === doneStageId) ||
                  (!doneStageId && s.name === chunk.subagentStageName)
                ) {
                  found = true;
                  return { ...s, status: "done" as const };
                }
                return s;
              });
              if (!found && chunk.subagentStageName) {
                nextStages = [
                  ...nextStages,
                  {
                    id: doneStageId || chunk.subagentStageName,
                    name: chunk.subagentStageName,
                    status: "done" as const,
                  },
                ];
              }
              next[next.length - 1] = {
                ...(last as ChatMessage),
                subagentStageWorking: false,
                subagentLastCompletedStageName: chunk.subagentStageName,
                subagentStages: nextStages,
              };
              return next;
            });
          });
        }
      }
      if (chunk.orchestratorRepair?.repairedRounds) {
        if (isVisibleSession()) {
          const repairedRounds = chunk.orchestratorRepair.repairedRounds;
          const repairReasons = (chunk.orchestratorRepair?.events || [])
            .map((x) => String(x?.reason || "").trim())
            .filter(Boolean);
          flushSync(() => {
            setConversations((prev) => {
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
          });
        }
      }
      if (chunk.error) {
        if (isVisibleSession()) {
          setConversations((prev) => {
            const next = [...prev];
            next[next.length - 1] = {
              role: "assistant",
              content: "本轮已结束，请继续下一条指令。",
              isError: true,
            };
            return next;
          });
          setLoading(false);
        }
        unsubscribe();
        unsubscribeRef.current = null;
        runningSessionIdRef.current = null;
        runningAccRef.current = null;
        return;
      }

      if (chunk.thinkingDelta) {
        acc.thinking += chunk.thinkingDelta;
        const td = chunk.thinkingDelta;
        if (isVisibleSession()) {
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (!last || last.role !== "assistant") return prev;
              next[next.length - 1] = {
                ...last,
                thinking: (last.thinking || "") + td,
                toolCalling: false,
              };
              return next;
            });
          });
        }
      }

      if (chunk.delta) {
        acc.response += chunk.delta;
        const delta = chunk.delta;
        if (isVisibleSession()) {
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (!last || last.role !== "assistant") return prev;
              const segs = (last as ChatMessage).toolCallSegments;
              const hasSegments = segs?.length;
              if (hasSegments) {
                let after = (last.contentAfterToolCalls ?? "") + delta;
                if (agentMode !== "subagent") {
                  const lastSeg = segs[segs.length - 1];
                  if (lastSeg?.textBefore && after.startsWith(lastSeg.textBefore)) {
                    after = after.slice(lastSeg.textBefore.length);
                  }
                }
                const fullContent =
                  segs.map((s) => s.textBefore).join("") + after;
                acc.response = fullContent;
                next[next.length - 1] = {
                  ...last,
                  contentAfterToolCalls: after,
                  content: fullContent,
                  toolCalling: false,
                };
              } else {
                next[next.length - 1] = {
                  ...last,
                  content: last.content + delta,
                  toolCalling: false,
                };
              }
              return next;
            });
          });
        }
      }

      if (chunk.chapterContentUpdated != null) {
        if (isVisibleSession()) {
          window.dispatchEvent(
            new CustomEvent("chapter-content-updated", {
              detail: { chapterId: chunk.chapterContentUpdated },
            }),
          );
        }
      }

      if (chunk.chapterCreated != null) {
        if (isVisibleSession()) {
          window.dispatchEvent(
            new CustomEvent("chapter-created", {
              detail: {
                chapterId: chunk.chapterCreated.chapterId,
                title: chunk.chapterCreated.title,
                parentId: chunk.chapterCreated.parentId ?? null,
              },
            }),
          );
        }
      }

      if (typeof chunk.collabLatestParagraph === "string" && chunk.collabLatestParagraph.trim()) {
        const para = chunk.collabLatestParagraph.trim();
        const wrapped = `\n\n### 最新段落（已写入正文）\n\n\`\`\`text\n${para}\n\`\`\`\n`;
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (!last || last.role !== "assistant") return prev;
            next[next.length - 1] = {
              ...(last as ChatMessage),
              content: ((last.content || "") + wrapped).trim(),
            };
            return next;
          });
        });
      }

      if (Array.isArray(chunk.toolReadCacheMask) && chunk.toolReadCacheMask.length > 0) {
        const mask = chunk.toolReadCacheMask;
        if (!isVisibleSession()) {
          if (
            !chunk.delta &&
            !chunk.thinkingDelta &&
            !chunk.done &&
            !chunk.error &&
            typeof chunk.toolIndexCompleted !== "number"
          ) {
            return;
          }
        }
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const lastMsg = next[next.length - 1];
            if (lastMsg?.role !== "assistant") return prev;
            const segs = (lastMsg as ChatMessage).toolCallSegments ?? [];
            if (segs.length === 0) return prev;
            const lastSeg = segs[segs.length - 1];
            const n = lastSeg.labels.length;
            const flags = [
              ...(lastSeg.cachedFlags ?? new Array(n).fill(false)),
            ];
            const m = Math.min(n, mask.length);
            for (let i = 0; i < m; i++) {
              if (mask[i]) flags[i] = true;
            }
            const nextSegs = [
              ...segs.slice(0, -1),
              { ...lastSeg, cachedFlags: flags },
            ];
            next[next.length - 1] = {
              ...(lastMsg as ChatMessage),
              toolCallSegments: nextSegs,
            };
            return next;
          });
        });
        if (
          !chunk.delta &&
          !chunk.thinkingDelta &&
          !chunk.done &&
          !chunk.error &&
          typeof chunk.toolIndexCompleted !== "number"
        ) {
          return;
        }
      }

      if (typeof chunk.toolIndexCompleted === "number") {
        const idx = chunk.toolIndexCompleted;
        const fromCache = chunk.toolFromCache === true;
        if (!isVisibleSession()) {
          if (
            !chunk.delta &&
            !chunk.thinkingDelta &&
            !chunk.done &&
            !chunk.error
          ) {
            return;
          }
        }
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const lastMsg = next[next.length - 1];
            if (lastMsg?.role !== "assistant") return prev;
            const segs = (lastMsg as ChatMessage).toolCallSegments ?? [];
            if (segs.length === 0) return prev;
            const lastSeg = segs[segs.length - 1];
            const n = lastSeg.labels.length;
            const nextCount = Math.min(idx + 1, n);
            const flags = [
              ...(lastSeg.cachedFlags ?? new Array(n).fill(false)),
            ];
            if (fromCache && idx >= 0 && idx < flags.length) flags[idx] = true;
            const nextSegs = [
              ...segs.slice(0, -1),
              {
                ...lastSeg,
                completedToolCount: nextCount,
                cachedFlags: flags,
              },
            ];
            next[next.length - 1] = {
              ...(lastMsg as ChatMessage),
              toolCallSegments: nextSegs,
            };
            return next;
          });
        });
        if (
          !chunk.delta &&
          !chunk.thinkingDelta &&
          !chunk.done &&
          !chunk.error
        ) {
          return;
        }
      }
      if (typeof chunk.toolCallCachedIndex === "number") {
        const idx = chunk.toolCallCachedIndex;
        if (!isVisibleSession()) {
          if (
            !chunk.delta &&
            !chunk.thinkingDelta &&
            !chunk.done &&
            !chunk.error
          ) {
            return;
          }
        }
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const lastMsg = next[next.length - 1];
            if (lastMsg?.role !== "assistant") return prev;
            const segs = (lastMsg as ChatMessage).toolCallSegments ?? [];
            if (segs.length === 0) return prev;
            const lastSeg = segs[segs.length - 1];
            const flags = [...(lastSeg.cachedFlags ?? new Array(lastSeg.labels.length).fill(false))];
            if (idx >= 0 && idx < flags.length) flags[idx] = true;
            const nextSegs = [
              ...segs.slice(0, -1),
              { ...lastSeg, cachedFlags: flags },
            ];
            next[next.length - 1] = {
              ...(lastMsg as ChatMessage),
              toolCallSegments: nextSegs,
            };
            return next;
          });
        });
        if (
          !chunk.delta &&
          !chunk.thinkingDelta &&
          !chunk.done &&
          !chunk.error
        ) {
          return;
        }
      }

      if (chunk.toolCalls?.length && chunk.toolCallsInProgress) {
        const partialContent = chunk.partialContent ?? "";
        const partialThinking = chunk.partialThinking ?? "";
        const insertedByDag = chunk.orchestratorInfo?.insertedByDag ?? 0;
        const repairReasons = (chunk.orchestratorRepair?.events || [])
          .map((x) => String(x?.reason || "").trim())
          .filter(Boolean);
        const visibleToolCalls = (chunk.toolCalls || []).filter((tc) => {
          const callId = String((tc as { id?: string })?.id || "");
          return !callId.startsWith("repair_") && !callId.startsWith("sys_");
        });
        if (
          visibleToolCalls.length === 0 &&
          !partialContent.trim() &&
          !partialThinking.trim()
        ) {
          return;
        }
        const rows = visibleToolCalls.map(
          (tc: { function?: { name?: string; arguments?: string }; id?: string }) => {
            const fn = tc.function?.name;
            if (!fn) {
              return { label: "（未识别工具）", outcome: "ok" as ToolCallLabelOutcome };
            }
            try {
              const args = JSON.parse(tc.function?.arguments || "{}") as Record<
                string,
                unknown
              >;
              const row = toolCallDisplayRow(
                fn,
                args,
                writingChapters || [],
                availableOutlines || [],
              );
              if (
                (fn === "getChapterContent" || fn === "editChapterContent") &&
                row.outcome === "context_error"
              ) {
              }
              return row;
            } catch {
              const row = toolCallDisplayRow(
                fn,
                {},
                writingChapters || [],
                availableOutlines || [],
              );
              if (
                (fn === "getChapterContent" || fn === "editChapterContent") &&
                row.outcome === "context_error"
              ) {
              }
              return row;
            }
          },
        );
        const taggedLabels = rows.map((row, idx) => {
          const base = row.label;
          const tc = visibleToolCalls[idx];
          const callId = String(tc?.id || "");
          let label = base;
          if (callId.startsWith("repair_")) label = `${base}（自动修复）`;
          else if (callId.startsWith("sys_")) label = `${base}（自动补前置）`;
          return label;
        });
        const labelOutcomes = rows.map((row) => row.outcome);
        let rebuiltAssistantText = "";
        if (isVisibleSession()) {
          flushSync(() => {
            setConversations((prev) => {
              const next = [...prev];
              const lastMsg = next[next.length - 1];
              if (lastMsg?.role === "assistant") {
                const prevSeg = (lastMsg as ChatMessage).toolCallSegments ?? [];
                const prevBlocks = (lastMsg as ChatMessage).thinkingBlocks ?? [];
                const currentThinking = (lastMsg.thinking || "").trim();
                const nextBlocks = currentThinking ? [...prevBlocks, currentThinking] : prevBlocks;
                /** 仅含「主稿专家过渡 / 最终答复」等流式尾稿；新工具批开始前须并入上方片段，否则渲染顺序会变成「新工具段在旧尾稿之上」 */
                const tail = lastMsg.contentAfterToolCalls ?? "";
                const flushTailSegments: ToolCallSegment[] =
                  tail.trim().length > 0
                    ? [{ textBefore: tail, labels: [], cachedFlags: [] }]
                    : [];
                const baseSegs = [...prevSeg, ...flushTailSegments];
                const hasPriorToolRound = prevSeg.some((s) => s.labels.length > 0);
                const textBefore =
                  partialContent && partialContent.trim()
                    ? partialContent
                    : !hasPriorToolRound
                      ? agentMode === "subagent"
                        ? ""
                        : lastMsg.content || acc.response || ""
                      : "";
                const newSegment: ToolCallSegment = {
                  textBefore,
                  labels: taggedLabels,
                  labelOutcomes,
                  cachedFlags: visibleToolCalls.map((tc) =>
                    Boolean((tc as { cached?: boolean }).cached),
                  ),
                  trace: {
                    insertedByDag,
                    insertedSkillNames: chunk.orchestratorInfo?.insertedSkillNames ?? [],
                    plannedToolNames: chunk.orchestratorInfo?.plannedToolNames ?? [],
                    repairedRounds: chunk.orchestratorRepair?.repairedRounds ?? 0,
                    repairReasons,
                    stage: chunk.subagentStage || undefined,
                  },
                };
                const nextSegments = [...baseSegs, newSegment];
                let afterToolCalls =
                  flushTailSegments.length > 0 ? "" : (lastMsg.contentAfterToolCalls ?? "");
                if (
                  agentMode !== "subagent" &&
                  flushTailSegments.length === 0 &&
                  textBefore &&
                  afterToolCalls.startsWith(textBefore)
                ) {
                  afterToolCalls = afterToolCalls.slice(textBefore.length);
                }
                rebuiltAssistantText =
                  nextSegments.map((s) => s.textBefore).join("") + afterToolCalls;
                acc.toolCallSegments = nextSegments;
                acc.thinkingBlocks = nextBlocks;
                next[next.length - 1] = {
                  ...lastMsg,
                  content: rebuiltAssistantText,
                  thinking: "",
                  thinkingBlocks: nextBlocks,
                  toolCalling: true,
                  toolCallSegments: nextSegments,
                  contentAfterToolCalls: afterToolCalls,
                };
              }
              return next;
            });
          });
        } else {
          const prevSeg = acc.toolCallSegments ?? [];
          const prevBlocks = acc.thinkingBlocks ?? [];
          const currentThinking = (acc.thinking || "").trim();
          const nextBlocks = currentThinking ? [...prevBlocks, currentThinking] : prevBlocks;
          const flushTailSegments: ToolCallSegment[] =
            acc.response.trim().length > 0
              ? [{ textBefore: acc.response, labels: [], cachedFlags: [] }]
              : [];
          const baseSegs = [...prevSeg, ...flushTailSegments];
          const hasPriorToolRound = prevSeg.some((s) => s.labels.length > 0);
          const textBefore =
            partialContent && partialContent.trim()
              ? partialContent
              : !hasPriorToolRound
                ? agentMode === "subagent"
                  ? ""
                  : acc.response || ""
                : "";
          const newSegment: ToolCallSegment = {
            textBefore,
            labels: taggedLabels,
            labelOutcomes,
            cachedFlags: visibleToolCalls.map((tc) =>
              Boolean((tc as { cached?: boolean }).cached),
            ),
            trace: {
              insertedByDag,
              insertedSkillNames: chunk.orchestratorInfo?.insertedSkillNames ?? [],
              plannedToolNames: chunk.orchestratorInfo?.plannedToolNames ?? [],
              repairedRounds: chunk.orchestratorRepair?.repairedRounds ?? 0,
              repairReasons,
              stage: chunk.subagentStage || undefined,
            },
          };
          const nextSegments = [...baseSegs, newSegment];
          rebuiltAssistantText = nextSegments.map((s) => s.textBefore).join("");
          acc.toolCallSegments = nextSegments;
          acc.thinkingBlocks = nextBlocks;
        }
        acc.response =
          rebuiltAssistantText ||
          (partialContent && partialContent.trim() ? partialContent : acc.response);
        acc.thinking = partialThinking;
        return;
      }

      if (chunk.done) {
        if (chunk.model) acc.model = chunk.model;
        const finalThinking = (acc.thinking || "").trim();
        const savedThinkingBlocks = finalThinking
          ? [...(acc.thinkingBlocks ?? []), finalThinking]
          : (acc.thinkingBlocks ?? []);
        let resolvedAssistantContent =
          acc.response ||
          synthesizeAssistantTextFromToolSegments({
            role: "assistant",
            content: "",
            toolCallSegments: acc.toolCallSegments,
          } as ChatMessage).trim();
        if (isVisibleSession()) {
          setConversations((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              const blocks = (last as ChatMessage).thinkingBlocks ?? [];
              const thinkingBlocks = finalThinking ? [...blocks, finalThinking] : blocks;
              const cm = last as ChatMessage;
              const stages = cm.subagentStages ?? [];
              const subagentStagesFinalized =
                agentMode === "subagent" && stages.length > 0
                  ? stages.map((s) =>
                      s.status === "running"
                        ? { ...s, status: "done" as const }
                        : s,
                    )
                  : cm.subagentStages;
              const currentContent = String(last.content || "");
              let finalContent = currentContent;
              if (!currentContent.trim()) {
                const synthesized = synthesizeAssistantTextFromToolSegments(
                  cm,
                ).trim();
                if (synthesized) {
                  finalContent = synthesized;
                } else {
                  finalContent =
                    agentMode === "subagent"
                      ? "内容同步中。"
                      : "内容同步中。";
                }
              }
              resolvedAssistantContent = finalContent;
              next[next.length - 1] = {
                ...last,
                content: finalContent,
                model: acc.model || undefined,
                thinking: finalThinking || last.thinking,
                thinkingBlocks: thinkingBlocks.length ? thinkingBlocks : undefined,
                toolCalling: false,
                subagentStageWorking: false,
                ...(agentMode === "subagent"
                  ? {
                      subagentBridging: false,
                      ...(subagentStagesFinalized
                        ? { subagentStages: subagentStagesFinalized }
                        : {}),
                    }
                  : {}),
              };
            }
            return next;
          });
          setLoading(false);
        }
        unsubscribe();
        unsubscribeRef.current = null;
        runningSessionIdRef.current = null;
        runningAccRef.current = null;
        acc.response = resolvedAssistantContent;

        const respTrim = (acc.response || "").trim();
        const thinkTrim = (acc.thinking || "").trim();
        const shouldSave =
          Boolean(acc.sessionId) &&
          Boolean(
            respTrim ||
              thinkTrim ||
              savedThinkingBlocks.length > 0 ||
              (acc.toolCallSegments?.length ?? 0) > 0,
          );

        if (shouldSave) {
          window.electronAPI.saveConversation({
            sessionId: acc.sessionId,
            chapterId: acc.chapterId ?? null,
            prompt: acc.userText,
            response: acc.response || "",
            model: acc.model || undefined,
            thinking: acc.thinking || undefined,
            thinkingBlocks: savedThinkingBlocks.length ? savedThinkingBlocks : undefined,
            toolCallSegments: acc.toolCallSegments?.length
              ? acc.toolCallSegments
              : undefined,
          });
        }

        const titleSource = (acc.response || acc.thinking || "").trim();
        if (acc.needsTitle && acc.sessionId && titleSource) {
          console.log("[AI 对话] 请求生成标题", {
            sessionId: acc.sessionId,
            apiProvider: cfg.apiProvider === "anthropic" ? "anthropic" : "openai",
            model: apiModelName,
          });
          window.electronAPI
            .generateSessionTitle({
              apiKey: cfg.apiKey,
              baseURL: cfg.baseUrl || undefined,
              prompt: `User:\n${acc.userText}\n\nAssistant:\n${titleSource}`.trim(),
              apiProvider:
                cfg.apiProvider === "anthropic"
                  ? "anthropic"
                  : "openai",
              model: apiModelName,
            })
            .then(async (titleRes) => {
              if (!titleRes.success || !titleRes.data?.trim()) {
                console.warn("[AI 对话] 标题生成失败或为空", {
                  success: titleRes.success,
                  error: titleRes.error ?? null,
                  hasData: Boolean(titleRes.data),
                  dataPreview:
                    typeof titleRes.data === "string"
                      ? titleRes.data.slice(0, 40)
                      : null,
                });
                return;
              }
              const nextTitle = titleRes.data.trim();
              await window.electronAPI.updateSessionTitle({
                sessionId: acc.sessionId!,
                title: nextTitle,
              });
              setSessions((prev) =>
                prev.map((s) =>
                  s.id === acc.sessionId
                    ? { ...s, title: nextTitle }
                    : s,
                ),
              );
            })
            .catch((err) => {
              console.warn("[AI 对话] 标题生成请求异常：", err);
            });
        }
      }
    });

    unsubscribeRef.current = unsubscribe;

    const modelConfig = modelConfigs[selectedModel];
    const effectiveThinking =
      agentMode === "subagent"
        ? false
        : Boolean(
            cfg?.thinkingOnly ||
              (cfg?.supportsThinking && thinkingEnabled) ||
              (!cfg && thinkingEnabled),
          );
    const apiModelName = cfg?.name ?? selectedModel;
    /** false：不传 temperature；undefined / true：按配置传 temperature */
    const useConfiguredTemperature =
      cfg?.customizeTemperature === undefined || cfg?.customizeTemperature === true;
    const streamOptions: {
      model: string;
      temperature?: number;
      thinking: { type: "enabled" | "disabled" };
      max_tokens: number;
    } = {
      model: apiModelName,
      ...(useConfiguredTemperature
        ? {
            temperature: effectiveThinking
              ? (cfg?.temperatureThinking ?? 0.6)
              : (cfg?.temperatureNonThinking ?? 0.6),
          }
        : {}),
      thinking: {
        type: (effectiveThinking ? "enabled" : "disabled") as
          | "enabled"
          | "disabled",
      },
      max_tokens: modelConfig?.max_tokens ?? 8192,
    };

    const hasBookContext = bookId != null;
    const useToolRouter = Boolean(hasBookContext && agentEnabled);

    if (import.meta.env.DEV) {
      console.log('[AI 对话] 传入内容:', { messages: newMessages, options: streamOptions, useToolRouter });
    }

    window.electronAPI.aiChatStream({
      apiKey: cfg.apiKey,
      baseURL: cfg.baseUrl || undefined,
      apiProvider:
        cfg.apiProvider === "anthropic" ? "anthropic" : "openai",
      messages: newMessages,
      options: streamOptions,
      tools: [],
      useToolRouter,
      bookId: bookId ?? undefined,
      bookTitle: bookTitle ?? undefined,
      chapterId: chapterId ?? undefined,
      currentChapterTitle: currentChapterTitle ?? undefined,
      writingChapters,
      availableOutlines,
      associatedChapterIds:
        associatedChapterIds.length > 0 ? associatedChapterIds : undefined,
      associatedOutlineIds:
        associatedOutlineIds.length > 0 ? associatedOutlineIds : undefined,
      agentMode,
      chatAgentMode: writingMode === "collab" ? "collab" : (agentMode === "subagent" ? "expert" : (agentEnabled ? "agent" : "ask")),
      ...(writingMode === "collab" ? { writingMode: "collab" as const } : {}),
      ...(agentMode === "subagent"
        ? {
            agentActions:
              agentActions && agentActions.length > 0 ? agentActions : ["full"],
          }
        : {}),
    });
  }, [
    prompt,
    loading,
    selectedModelConfig,
    conversations,
    bookId,
    bookTitle,
    chapterId,
    currentChapterTitle,
    activeSessionId,
    associatedChapterIds,
    associatedOutlineIds,
    writingChapters,
    availableOutlines,
    sessions,
    selectedModel,
    thinkingEnabled,
    agentEnabled,
    modelConfigs,
    setPrompt,
    setLoading,
    setConversations,
    setActiveSessionId,
    setSessions,
    selectedMemoryIds,
    agentEnabled,
    agentMode,
    writingMode,
    agentActions,
    appMessage,
  ]);

  return { handleSubmit, handleAbort, runningSessionIdRef, runningAccRef };
}

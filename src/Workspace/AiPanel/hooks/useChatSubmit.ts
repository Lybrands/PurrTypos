import React from "react";
import { flushSync } from "react-dom";
import { App as AntdApp } from "antd";
import type { AiModelConfig, Outline, AiSession } from "../../../types";

function chapterDisplayTitle(
  chapterId: number | undefined,
  writingChapters: { id: number; title: string }[],
): string {
  if (chapterId == null || !Number.isFinite(chapterId)) return "（未知章节）";
  const ch = writingChapters.find((c) => c.id === chapterId);
  const t = ch?.title?.trim();
  return t || `（章节 ${chapterId}）`;
}

/** 工具在对话里的展示文案；需带章名的工具在此用 chapterDisplayTitle 拼接，记忆/伏笔固定文案不带标题 */
function toolCallDisplayLabel(
  name: string,
  args: Record<string, unknown>,
  writingChapters: { id: number; title: string }[],
): string {
  const title = (cid: number | undefined) => chapterDisplayTitle(cid, writingChapters);
  try {
    switch (name) {
      case "getChapterContent": {
        const cid = args.chapterId != null ? Number(args.chapterId) : undefined;
        return `查看《${title(cid)}》章节内容`;
      }
      case "editChapterContent": {
        const cid = args.chapterId != null ? Number(args.chapterId) : undefined;
        return `编辑《${title(cid)}》章节内容`;
      }
      case "batchGetChapterContents": {
        const raw = args.chapterIds;
        if (!Array.isArray(raw) || raw.length === 0) return "查看多章内容";
        const ids = raw.map((x) => Number(x)).filter((n) => Number.isFinite(n));
        if (ids.length === 0) return "查看多章内容";
        if (ids.length === 1) return `查看《${title(ids[0])}》章节内容`;
        const head = ids
          .slice(0, 3)
          .map((id) => `《${title(id)}》`)
          .join("");
        if (ids.length <= 3) return `查看${head}等多章内容`;
        return `查看${head}等 ${ids.length} 章内容`;
      }
      case "listWritingChapters":
        return "查看章节目录";
      case "getBookCharacters":
        return "查看人物信息";
      case "listBookCharacters":
        return "查看人物列表";
      case "getStoryBackground":
        return "查看小说背景";
      case "getGlobalOutline":
        return "查看总纲";
      case "editGlobalOutline":
        return "编辑总纲";
      case "queryOutline":
        return "查看大纲详情";
      case "listOutlines":
        return "查看大纲列表";
      case "updateOutline":
        return "更新大纲";
      case "addMemory":
        return "添加长期记忆";
      case "searchMemories":
        return "检索长期记忆";
      case "addForeshadowing":
        return "添加伏笔";
      default:
        return name;
    }
  } catch {
    return name;
  }
}

/** 一段「调用前文案 + 该次调用的正在查看列表」，按调用顺序排列 */
export interface ToolCallSegment {
  textBefore: string;
  labels: string[];
  /** 本段内已执行完成的工具数量（与后端 toolIndexCompleted 同步，顺序递增） */
  completedToolCount?: number;
  trace?: {
    insertedByDag?: number;
    insertedSkillNames?: string[];
    plannedToolNames?: string[];
    repairedRounds?: number;
    repairReasons?: string[];
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
}

export interface UseChatSubmitParams {
  /** 当前选中的模型配置（含 apiKey、baseUrl、name）；为空时无法发送 */
  selectedModelConfig: AiModelConfig | null;
  systemPrompt: string;
  prompt: string;
  setPrompt: React.Dispatch<React.SetStateAction<string>>;
  loading: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  conversations: ChatMessage[];
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  bookId: number | null | undefined;
  bookTitle?: string;
  chapterId: number | null | undefined;
  activeSessionId: number | null;
  setActiveSessionId: React.Dispatch<React.SetStateAction<number | null>>;
  sessions: AiSession[];
  setSessions: React.Dispatch<React.SetStateAction<AiSession[]>>;
  associatedChapterIds: number[];
  associatedOutlineIds: number[];
  writingChapters: { id: number; title: string }[];
  availableOutlines: Outline[];
  currentChapterTitle?: string;
  selectedModel: string;
  thinkingEnabled: boolean;
  agentEnabled: boolean;
  /** 用于 max_tokens 等；temperature 由选中模型的 AiModelConfig 与思考开关决定 */
  modelConfigs: Record<string, { label?: string; max_tokens?: number }>;
  selectedMemoryIds?: (number | string)[];
  selectedForeshadowingIds?: (number | string)[];
}

export function useChatSubmit(params: UseChatSubmitParams) {
  const {
    selectedModelConfig,
    systemPrompt,
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
  } = params;

  const { message: appMessage } = AntdApp.useApp();
  const unsubscribeRef = React.useRef<(() => void) | null>(null);

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
      const expectThinking = cfg
        ? (cfg.thinkingOnly || (cfg.supportsThinking && thinkingEnabled))
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
          ? `\n\n当前书籍：ID ${bookId}，《${bookName}》；当前章节：ID ${chapterId ?? "未选"}，《${chapterName}》。调用工具时使用上述 ID；回复用户时请使用书籍名、章节名等名称，不要直接暴露或返回 ID。`
          : `\n\n当前书籍：ID ${bookId}，《${bookName}》；当前章节：ID ${chapterId ?? "未选"}，《${chapterName}》。你无法访问书籍内容，仅能基于用户描述或用户主动提供的信息作答。回复时使用名称，不暴露 ID。`
        : "";

    // 长期记忆由工具调用提供，不再拼入 system
    const systemContent = systemPrompt + systemSuffix;

    let historyMessages: { role: string; content: string }[];
    if (resend != null) {
      const nextConversations = [
        ...conversations.slice(0, resend.editIndex),
        { role: "user" as const, content: resend.content.trim() },
        assistantPlaceholder,
      ];
      historyMessages = nextConversations
        .slice(0, -1)
        .filter(
          (m) => !(m as ChatMessage).isError && m.role !== "system" && m.content.trim() !== "",
        )
        .slice(-50)
        .map((m) => ({ role: m.role, content: m.content }));
      // 从数据库删除「该条之后」的对话记录，与界面截断一致
      const keepTurnCount = Math.floor(resend.editIndex / 2);
      if (sessionId != null && keepTurnCount >= 0) {
        window.electronAPI.deleteConversationsAfterTurn({ sessionId, keepTurnCount }).catch(() => {});
      }
    } else {
      historyMessages = conversations
        .filter(
          (m) => !m.isError && m.role !== "system" && m.content.trim() !== "",
        )
        .slice(-50)
        .map((m) => ({ role: m.role, content: m.content }));
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

    const unsubscribe = window.electronAPI.onAiChunk((chunk) => {
      if (chunk.toolRouterWarning) {
        appMessage.warning(chunk.toolRouterWarning);
      }
      if (chunk.orchestratorRepair?.repairedRounds) {
        appMessage.info(`已自动修复执行路径 ${chunk.orchestratorRepair.repairedRounds} 次`);
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
      if (chunk.error) {
        setConversations((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "assistant",
            content: "请求失败：" + chunk.error,
            isError: true,
          };
          return next;
        });
        setLoading(false);
        unsubscribe();
        unsubscribeRef.current = null;
        return;
      }

      if (chunk.thinkingDelta) {
        acc.thinking += chunk.thinkingDelta;
        const td = chunk.thinkingDelta;
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = {
              ...last,
              thinking: (last.thinking || "") + td,
              toolCalling: false,
            };
            return next;
          });
        });
      }

      if (chunk.delta) {
        acc.response += chunk.delta;
        const delta = chunk.delta;
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            const segs = (last as ChatMessage).toolCallSegments;
            const hasSegments = segs?.length;
            if (hasSegments) {
              let after = (last.contentAfterToolCalls ?? "") + delta;
              const lastSeg = segs[segs.length - 1];
              if (lastSeg?.textBefore && after.startsWith(lastSeg.textBefore)) {
                after = after.slice(lastSeg.textBefore.length);
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

      if (chunk.chapterContentUpdated != null) {
        window.dispatchEvent(
          new CustomEvent("chapter-content-updated", {
            detail: { chapterId: chunk.chapterContentUpdated },
          }),
        );
      }

      if (typeof chunk.toolIndexCompleted === "number") {
        const idx = chunk.toolIndexCompleted;
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
            const nextSegs = [
              ...segs.slice(0, -1),
              { ...lastSeg, completedToolCount: nextCount },
            ];
            next[next.length - 1] = {
              ...(lastMsg as ChatMessage),
              toolCallSegments: nextSegs,
            };
            return next;
          });
        });
        return;
      }

      if (chunk.toolCalls?.length && chunk.toolCallsInProgress) {
        const partialContent = chunk.partialContent ?? "";
        const partialThinking = chunk.partialThinking ?? "";
        const insertedByDag = chunk.orchestratorInfo?.insertedByDag ?? 0;
        const repairReasons = (chunk.orchestratorRepair?.events || [])
          .map((x) => String(x?.reason || "").trim())
          .filter(Boolean);
        const rawLabels = (chunk.toolCalls || [])
          .map((tc: { function?: { name?: string; arguments?: string } }) => {
            const name = tc.function?.name;
            if (!name) return "";
            try {
              const args = JSON.parse(tc.function?.arguments || "{}") as Record<
                string,
                unknown
              >;
              return toolCallDisplayLabel(name, args, writingChapters || []);
            } catch {
              return toolCallDisplayLabel(name, {}, writingChapters || []);
            }
          }) as string[];
        const taggedLabels = (chunk.toolCalls || []).map((tc, idx) => {
          const base = rawLabels[idx] || "";
          if (!base) return "";
          const callId = String(tc.id || "");
          if (callId.startsWith("repair_")) return `${base}（自动修复）`;
          if (callId.startsWith("sys_")) return `${base}（自动补前置）`;
          return base;
        }).filter(Boolean) as string[];
        if (insertedByDag > 0) {
          appMessage.info(`DAG 已自动补齐 ${insertedByDag} 个前置步骤`);
        }
        flushSync(() => {
          setConversations((prev) => {
            const next = [...prev];
            const lastMsg = next[next.length - 1];
            if (lastMsg?.role === "assistant") {
              const prevSeg = (lastMsg as ChatMessage).toolCallSegments ?? [];
              const prevBlocks = (lastMsg as ChatMessage).thinkingBlocks ?? [];
              const currentThinking = (lastMsg.thinking || "").trim();
              const nextBlocks = currentThinking ? [...prevBlocks, currentThinking] : prevBlocks;
              const isFirstBlock = prevSeg.length === 0;
              const textBefore =
                partialContent && partialContent.trim()
                  ? partialContent
                  : isFirstBlock
                    ? lastMsg.content || acc.response || ""
                    : "";
              const newSegment: ToolCallSegment = {
                textBefore,
                labels: taggedLabels,
                trace: {
                  insertedByDag,
                  insertedSkillNames: chunk.orchestratorInfo?.insertedSkillNames ?? [],
                  plannedToolNames: chunk.orchestratorInfo?.plannedToolNames ?? [],
                  repairedRounds: chunk.orchestratorRepair?.repairedRounds ?? 0,
                  repairReasons,
                },
              };
              const nextSegments = [...prevSeg, newSegment];
              let afterToolCalls = lastMsg.contentAfterToolCalls ?? "";
              if (textBefore && afterToolCalls.startsWith(textBefore)) {
                afterToolCalls = afterToolCalls.slice(textBefore.length);
              }
              acc.toolCallSegments = nextSegments;
              acc.thinkingBlocks = nextBlocks;
              next[next.length - 1] = {
                ...lastMsg,
                content:
                  nextSegments.map((s) => s.textBefore).join("") + afterToolCalls,
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
        acc.response =
          partialContent && partialContent.trim() ? partialContent : acc.response;
        acc.thinking = partialThinking;
        return;
      }

      if (chunk.done) {
        if (chunk.model) acc.model = chunk.model;
        const finalThinking = (acc.thinking || "").trim();
        const savedThinkingBlocks = finalThinking
          ? [...(acc.thinkingBlocks ?? []), finalThinking]
          : (acc.thinkingBlocks ?? []);
        setConversations((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            const blocks = (last as ChatMessage).thinkingBlocks ?? [];
            const thinkingBlocks = finalThinking ? [...blocks, finalThinking] : blocks;
            next[next.length - 1] = {
              ...last,
              model: acc.model || undefined,
              thinking: finalThinking || last.thinking,
              thinkingBlocks: thinkingBlocks.length ? thinkingBlocks : undefined,
              toolCalling: false,
            };
          }
          return next;
        });
        setLoading(false);
        unsubscribe();
        unsubscribeRef.current = null;

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
      cfg?.thinkingOnly || (cfg?.supportsThinking && thinkingEnabled) || (!cfg && thinkingEnabled);
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
      chapterId: chapterId ?? undefined,
      currentChapterTitle: currentChapterTitle ?? undefined,
      writingChapters,
      availableOutlines,
    });
  }, [
    prompt,
    loading,
    selectedModelConfig,
    conversations,
    systemPrompt,
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
    appMessage,
  ]);

  return { handleSubmit, handleAbort };
}

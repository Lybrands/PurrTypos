import {
  CheckIcon,
  CopyIcon,
  StarIcon,
} from '@/purr-components';
import { PurrButton, PurrDropdown, PurrTooltip, usePurrToast } from '@/purr-components';
import React from "react";
import AgentUserMessageBody from "@/components/AgentConversation/UserMessageBody";
import { markdownToPlainText } from "../../../../utils/markdown";
import { formatModelName } from "../../utils";
import { type ChatMessage } from "../../hooks";
import { getAssistantRenderableMarkdown } from "../../rendering";
import MessageEditor from "../MessageEditor";
import AssistantMessageBody from "./AssistantMessageBody";
import ErrorReportNotice from "./ErrorReportNotice";
import type { ChatMessageListProps } from "./index";

export interface ChatMessageBubbleProps
  extends Pick<
    ChatMessageListProps,
    | "loading"
    | "bookId"
    | "chapterId"
    | "contextBar"
    | "editingMessageIndex"
    | "setEditingMessageIndex"
    | "editingMessageDraftRef"
    | "editTextareaRef"
    | "onEditSend"
    | "modelConfigs"
    | "modelSelection"
    | "onAbort"
    | "onAddFavorite"
    | "onStructuredAnswer"
    | "setScrolledUpByReason"
  > {
  index: number;
  convIndex: number;
  message: ChatMessage;
  isLast: boolean;
  prevUserContent: string;
}

function ChatMessageBubbleInner({
  index,
  convIndex,
  message,
  isLast,
  prevUserContent,
  loading,
  bookId,
  chapterId,
  contextBar,
  editingMessageIndex,
  setEditingMessageIndex,
  editingMessageDraftRef,
  editTextareaRef,
  onEditSend,
  modelConfigs,
  modelSelection,
  onAbort,
  onAddFavorite,
  onStructuredAnswer,
  setScrolledUpByReason,
}: ChatMessageBubbleProps) {
  const appMessage = usePurrToast();
  const [copiedFormat, setCopiedFormat] = React.useState<
    "plain" | "markdown" | null
  >(null);
  const copiedTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const hasCommentaryBlocks = (message.commentaryBlocks?.length ?? 0) > 0;
  const hasAnyCommentary =
    hasCommentaryBlocks ||
    (message.commentary !== undefined && message.commentary !== "");
  const isEmpty =
    !message.content &&
    !message.toolCallSegments?.length &&
    !message.delegations?.length &&
    !message.contextCompaction &&
    !message.error &&
    !hasAnyCommentary;
  const isLastAssistant =
    isLast && message.role === "assistant" && !message.isError;
  const showPlaceholder = isLastAssistant && isEmpty;
  const copyMarkdown = React.useMemo(
    () =>
      message.role === "assistant"
        ? getAssistantRenderableMarkdown(message).trim()
        : "",
    [message],
  );
  const copyText = React.useMemo(
    () => markdownToPlainText(copyMarkdown),
    [copyMarkdown],
  );

  React.useEffect(
    () => () => {
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
    },
    [],
  );

  const copyAnswer = React.useCallback(async (
    value: string,
    format: "plain" | "markdown",
  ) => {
    if (!value) {
      appMessage.info("本轮没有可复制的输出");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setCopiedFormat(format);
      appMessage.success(
        format === "plain" ? "已复制纯文本" : "已复制 Markdown",
      );
      if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = setTimeout(() => setCopiedFormat(null), 1600);
    } catch (error) {
      console.error("[AiPanel] clipboard write failed:", error);
      appMessage.error("复制失败，请稍后重试");
    }
  }, [appMessage]);

  const handleCopyPlainText = React.useCallback(
    () => copyAnswer(copyText, "plain"),
    [copyAnswer, copyText],
  );

  const handleCopyMarkdown = React.useCallback(
    () => copyAnswer(copyMarkdown, "markdown"),
    [copyAnswer, copyMarkdown],
  );

  if (message.role === "assistant" && isEmpty && !(isLast && loading)) {
    return null;
  }

  return (
    <div
      className={`chat-bubble ${message.role} ${message.isError ? "error" : ""} ${
        message.role === "user" &&
        convIndex >= 0 &&
        editingMessageIndex === convIndex
          ? "chat-bubble--editing"
          : ""
      }`}
    >
      {message.role === "user" &&
        convIndex >= 0 &&
        editingMessageIndex === convIndex && (
          <MessageEditor
            bookId={bookId}
            chapterId={chapterId}
            contextBar={contextBar}
            modelConfigs={modelConfigs}
            modelSelection={modelSelection}
            editingMessageIndex={editingMessageIndex}
            editingMessageDraftRef={editingMessageDraftRef}
            editTextareaRef={editTextareaRef}
            onSend={(content) => onEditSend(convIndex, content)}
            onCancel={() => {
              setEditingMessageIndex(null);
              editingMessageDraftRef.current = "";
            }}
            onAbort={onAbort}
          />
        )}
      {message.role === "user" &&
        (convIndex < 0 || editingMessageIndex !== convIndex) && (
          <AgentUserMessageBody
            content={message.content}
            sentAt={message.sentAt}
            onEdit={!loading && convIndex >= 0 ? () => {
              editingMessageDraftRef.current = message.content ?? "";
              setEditingMessageIndex(convIndex);
            } : undefined}
          />
        )}
      {message.role === "assistant" && message.isError && (
        <ErrorReportNotice
          message={String(message.content || "")}
          report={message.errorReport}
        />
      )}
      {message.role === "assistant" && !message.isError && (
        <AssistantMessageBody
          index={index}
          message={message}
          loading={loading}
          isLastAssistant={isLastAssistant}
          showPlaceholder={showPlaceholder}
          setScrolledUpByReason={setScrolledUpByReason}
          onStructuredAnswer={onStructuredAnswer}
        />
      )}
      {message.role === "assistant" &&
        !message.isError &&
        !showPlaceholder &&
        !(isLastAssistant && loading) && (
          <div className="bubble-footer">
            {message.model && (
              <span className="bubble-model-tag">
                {formatModelName(message.model, modelConfigs)}
              </span>
            )}
            {copyText && (
              <PurrDropdown
                trigger={["contextMenu"]}
                menu={{
                  items: [
                    {
                      key: "copy-plain",
                      label: "复制纯文本",
                      icon: <CopyIcon />,
                      onClick: handleCopyPlainText,
                    },
                    {
                      key: "copy-markdown",
                      label: "复制 Markdown",
                      icon: <CopyIcon />,
                      onClick: handleCopyMarkdown,
                    },
                  ],
                }}
              >
                <PurrTooltip
                  title={copiedFormat
                    ? `已复制${copiedFormat === "plain" ? "纯文本" : " Markdown"}`
                    : "复制纯文本 · 右键选择格式"}
                >
                  <PurrButton
                    type="text"
                    size="small"
                    icon={
                      copiedFormat ? (
                        <CheckIcon style={{ fontSize: 12 }} />
                      ) : (
                        <CopyIcon style={{ fontSize: 12 }} />
                      )
                    }
                    className={`bubble-copy-btn${copiedFormat ? " is-copied" : ""}`}
                    onClick={handleCopyPlainText}
                    aria-label="复制回复纯文本"
                  />
                </PurrTooltip>
              </PurrDropdown>
            )}
            <PurrTooltip title="收藏">
              <PurrButton
                type="text"
                size="small"
                icon={<StarIcon style={{ fontSize: 12 }} />}
                className="bubble-bookmark-btn"
                onClick={() => onAddFavorite(prevUserContent, message.content)}
              />
            </PurrTooltip>
          </div>
        )}
    </div>
  );
}

function bubblePropsEqual(
  prev: ChatMessageBubbleProps,
  next: ChatMessageBubbleProps,
): boolean {
  if (prev.message !== next.message) return false;
  if (prev.loading !== next.loading) return false;
  if (prev.isLast !== next.isLast) return false;
  if (prev.index !== next.index) return false;
  if (prev.convIndex !== next.convIndex) return false;
  if (prev.editingMessageIndex !== next.editingMessageIndex) return false;
  if (prev.prevUserContent !== next.prevUserContent) return false;
  if (prev.chapterId !== next.chapterId) return false;
  return true;
}

const ChatMessageBubble = React.memo(ChatMessageBubbleInner, bubblePropsEqual);
export default ChatMessageBubble;

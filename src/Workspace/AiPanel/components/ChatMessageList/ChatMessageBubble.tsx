import {
  CheckOutlined,
  CopyOutlined,
  EditOutlined,
  StarOutlined,
} from "../../../../ui";
import { Button, Dropdown, Tooltip, useToast } from "../../../../ui";
import React from "react";
import { markdownToPlainText } from "../../../../utils/markdown";
import { formatModelName } from "../../utils";
import { type ChatMessage } from "../../hooks";
import { getAssistantRenderableMarkdown } from "../../rendering";
import MessageEditor from "../MessageEditor";
import AssistantMessageBody from "./AssistantMessageBody";
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
  setScrolledUpByReason,
}: ChatMessageBubbleProps) {
  const appMessage = useToast();
  const [copiedFormat, setCopiedFormat] = React.useState<
    "plain" | "markdown" | null
  >(null);
  const copiedTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const hasThinkingBlocks = (message.thinkingBlocks?.length ?? 0) > 0;
  const hasAnyThinking =
    hasThinkingBlocks ||
    (message.thinking !== undefined && message.thinking !== "");
  const isEmpty =
    !message.content &&
    !message.toolCallSegments?.length &&
    !message.taskPlan &&
    !message.delegations?.length &&
    !message.contextCompaction &&
    !hasAnyThinking;
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

  if (message.role === "assistant" && isEmpty && !isLast) {
    return (
      <div className="chat-bubble assistant">
        <div className="bubble-content">内容同步中。</div>
      </div>
    );
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
            onSend={() => onEditSend(convIndex)}
            onCancel={() => {
              setEditingMessageIndex(null);
              editingMessageDraftRef.current = "";
            }}
            onAbort={onAbort}
          />
        )}
      {message.role === "user" &&
        (convIndex < 0 || editingMessageIndex !== convIndex) && (
          <>
            {message.content ? (
              <div className="bubble-content">{message.content}</div>
            ) : null}
            {!loading && convIndex >= 0 && (
              <div className="bubble-user-actions">
                <Tooltip title="编辑提问">
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined style={{ fontSize: 12 }} />}
                    className="bubble-edit-btn"
                    onClick={() => {
                      editingMessageDraftRef.current = message.content ?? "";
                      setEditingMessageIndex(convIndex);
                    }}
                  />
                </Tooltip>
              </div>
            )}
          </>
        )}
      {message.role === "assistant" && message.isError && (
        <div className="bubble-content bubble-content--error">
          {String(message.content || "")}
        </div>
      )}
      {message.role === "assistant" && !message.isError && (
        <AssistantMessageBody
          index={index}
          message={message}
          loading={loading}
          isLastAssistant={isLastAssistant}
          showPlaceholder={showPlaceholder}
          setScrolledUpByReason={setScrolledUpByReason}
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
              <Dropdown
                trigger={["contextMenu"]}
                menu={{
                  items: [
                    {
                      key: "copy-plain",
                      label: "复制纯文本",
                      icon: <CopyOutlined />,
                      onClick: handleCopyPlainText,
                    },
                    {
                      key: "copy-markdown",
                      label: "复制 Markdown",
                      icon: <CopyOutlined />,
                      onClick: handleCopyMarkdown,
                    },
                  ],
                }}
              >
                <Tooltip
                  title={copiedFormat
                    ? `已复制${copiedFormat === "plain" ? "纯文本" : " Markdown"}`
                    : "复制纯文本 · 右键选择格式"}
                >
                  <Button
                    type="text"
                    size="small"
                    icon={
                      copiedFormat ? (
                        <CheckOutlined style={{ fontSize: 12 }} />
                      ) : (
                        <CopyOutlined style={{ fontSize: 12 }} />
                      )
                    }
                    className={`bubble-copy-btn${copiedFormat ? " is-copied" : ""}`}
                    onClick={handleCopyPlainText}
                    aria-label="复制回复纯文本"
                  />
                </Tooltip>
              </Dropdown>
            )}
            <Tooltip title="收藏">
              <Button
                type="text"
                size="small"
                icon={<StarOutlined style={{ fontSize: 12 }} />}
                className="bubble-bookmark-btn"
                onClick={() => onAddFavorite(prevUserContent, message.content)}
              />
            </Tooltip>
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

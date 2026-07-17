import { EditOutlined, StarOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import React from "react";
import { formatModelName } from "../../utils";
import { type ChatMessage } from "../../hooks";
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
  const hasThinkingBlocks = (message.thinkingBlocks?.length ?? 0) > 0;
  const hasAnyThinking =
    hasThinkingBlocks ||
    (message.thinking !== undefined && message.thinking !== "");
  const isEmpty =
    !message.content &&
    !message.toolCallSegments?.length &&
    !message.taskPlan &&
    !hasAnyThinking;
  const isLastAssistant =
    isLast && message.role === "assistant" && !message.isError;
  const showPlaceholder = isLastAssistant && isEmpty;

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

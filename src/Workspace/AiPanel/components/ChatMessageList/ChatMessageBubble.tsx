import { EditOutlined, StarOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import { formatModelName } from "../../utils";
import { type ChatMessage } from "../../hooks";
import MessageEditor from "../MessageEditor";
import AssistantMessageBody from "./AssistantMessageBody";
import type { ChatMessageListProps } from "./index";

export interface ChatMessageBubbleProps
  extends Pick<
    ChatMessageListProps,
    | "combinedData"
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
  dataIndex: number;
  convIndex: number;
  message: ChatMessage;
}

/** 单条消息气泡：按 role 分发为用户消息（含编辑态）、错误消息、助手消息体。 */
export default function ChatMessageBubble({
  index,
  dataIndex,
  convIndex,
  message,
  combinedData,
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
  const hasSubagentProgress = Boolean(
    message.writingSubagentActive ||
      message.subagentResult ||
      message.subagentStages?.length ||
      message.subagentStageName ||
      message.subagentStageId ||
      message.subagentBridging ||
      message.subagentMainPresenter,
  );
  const isEmpty =
    !message.content &&
    !message.toolCallSegments?.length &&
    !hasAnyThinking &&
    !hasSubagentProgress &&
    !(message.subagentPipelineDigest || "").trim();
  const isLast = dataIndex === combinedData.length - 1;
  const isLastAssistant =
    isLast && message.role === "assistant" && !message.isError;
  const showPlaceholder = isLastAssistant && isEmpty;

  if (message.role === "assistant" && isEmpty && !isLast) {
    return (
      <div className="chat-bubble assistant">
        <div className="bubble-label">AI</div>
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
      <div className="bubble-label">{message.role === "user" ? "你" : "AI"}</div>
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
          chapterId={chapterId}
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
                onClick={() =>
                  onAddFavorite(
                    combinedData[dataIndex - 1]?.content ?? "",
                    message.content,
                  )
                }
              />
            </Tooltip>
          </div>
        )}
    </div>
  );
}

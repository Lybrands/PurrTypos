import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { EntityId } from "../../../../types";
import { getAssistantRenderableMarkdown } from "../../rendering";
import { type ChatMessage } from "../../hooks";
import ToolCallStatus from "../ToolCallStatus";
import SettingDiffCard from "../SettingDiffCard";
import ThinkingRegion from "../ThinkingRegion";
import SubagentResultCard from "../SubagentResultCard";

export interface AssistantMessageBodyProps {
  index: number;
  message: ChatMessage;
  loading: boolean;
  isLastAssistant: boolean;
  showPlaceholder: boolean;
  chapterId: EntityId | null | undefined;
  setScrolledUpByReason: (nextValue: boolean, reason: string) => void;
}

/** 助手消息体：思考区（流式/历史分段）、工具调用状态、正文 markdown、子专家进度与结果卡片。 */
export default function AssistantMessageBody({
  index,
  message,
  loading,
  isLastAssistant,
  showPlaceholder,
  chapterId,
  setScrolledUpByReason,
}: AssistantMessageBodyProps) {
  const segments = message.toolCallSegments ?? [];
  const blocks = message.thinkingBlocks ?? [];
  const isStreaming = loading && isLastAssistant;
  const currentThinking = message.thinking ?? "";
  const assistantMarkdownRaw = getAssistantRenderableMarkdown(message);
  const assistantMarkdown = message.subagentResult ? "" : assistantMarkdownRaw;
  const hasGeneratedContent = Boolean(
    assistantMarkdown.trim() ||
      (message.subagentPipelineDigest || "").trim() ||
      message.subagentResult,
  );
  const handleWheelUp = () =>
    setScrolledUpByReason(true, "thinking-region-wheel-up");

  return (
    <div className="bubble-assistant-body">
      {showPlaceholder && (
        <div className="bubble-content bubble-content--thinking-placeholder">
          <span className="bubble-placeholder-text">正在思考</span>
          <span className="a-blink-dots">...</span>
        </div>
      )}
      {!showPlaceholder && (
        <>
          {(message.subagentPipelineDigest || "").trim() ? (
            <div className="bubble-content bubble-content--subagent-digest">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {(message.subagentPipelineDigest || "").trim()}
              </ReactMarkdown>
            </div>
          ) : null}
          {segments.map((seg, segIdx) => {
            const isToolLive =
              isLastAssistant &&
              loading &&
              segIdx === segments.length - 1 &&
              Boolean(message.toolCalling) &&
              seg.labels.length > 0;
            const toolCompletedCount = isToolLive
              ? (seg.completedToolCount ?? 0)
              : seg.labels.length;
            return (
              <React.Fragment key={segIdx}>
                {blocks[segIdx]?.trim() && (
                  <ThinkingRegion
                    key={`${index}-seg-${segIdx}`}
                    regionKey={`${index}-seg-${segIdx}`}
                    content={blocks[segIdx]}
                    streaming={false}
                    defaultOpen={false}
                    onWheelUp={handleWheelUp}
                  />
                )}
                <div className="bubble-content">
                  {seg.textBefore && (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {seg.textBefore}
                    </ReactMarkdown>
                  )}
                  {seg.labels.length > 0 ? (
                    <ToolCallStatus
                      labels={seg.labels}
                      labelOutcomes={seg.labelOutcomes}
                      cachedFlags={seg.cachedFlags}
                      completedToolCount={toolCompletedCount}
                      trace={seg.trace}
                    />
                  ) : null}
                </div>
              </React.Fragment>
            );
          })}
          {isStreaming &&
            currentThinking !== undefined &&
            currentThinking !== "" &&
            !hasGeneratedContent && (
              <ThinkingRegion
                key={`${index}-stream-th`}
                regionKey={`${index}-stream-main`}
                content={currentThinking}
                streaming
                streamingHeader
                showCursor={!(message.content || message.contentAfterToolCalls)}
                defaultOpen
                onWheelUp={handleWheelUp}
              />
            )}
          {isStreaming &&
            currentThinking !== undefined &&
            currentThinking !== "" &&
            hasGeneratedContent && (
              <ThinkingRegion
                key={`${index}-stream-pa`}
                regionKey={`${index}-stream-main`}
                content={currentThinking}
                streaming
                defaultOpen={false}
                onWheelUp={handleWheelUp}
              />
            )}
          {!isStreaming && segments.length > 0 && blocks[segments.length]?.trim() && (
            <ThinkingRegion
              key={`${index}-tail-main`}
              regionKey={`${index}-stream-main`}
              content={blocks[segments.length]}
              streaming={false}
              defaultOpen={false}
              onWheelUp={handleWheelUp}
            />
          )}
          {!isStreaming && segments.length === 0 && blocks[0]?.trim() && (
            <ThinkingRegion
              key={`${index}-tail-main`}
              regionKey={`${index}-stream-main`}
              content={blocks[0]}
              streaming={false}
              defaultOpen={false}
              onWheelUp={handleWheelUp}
            />
          )}
          {assistantMarkdown && (
            <div className="bubble-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {assistantMarkdown}
              </ReactMarkdown>
            </div>
          )}
          {!showPlaceholder && isLastAssistant && loading && (
            <div className="bubble-content bubble-content--waiting-dots">
              <span className="a-blink-dots">...</span>
            </div>
          )}
        </>
      )}
      {message.writingSubagentActive ? (
        <div className="bubble-content bubble-content--waiting-dots">
          <span className="a-blink-dots">
            {message.writingSubagentLabel || "子专家"}处理中…
          </span>
        </div>
      ) : null}
          {message.subagentResult ? (
        <SubagentResultCard
          role={message.subagentResult.role}
          payload={message.subagentResult.payload}
          chapterId={chapterId}
        />
      ) : null}
      {(message.settingDiffCards || []).map((card) => (
        <SettingDiffCard key={card.sessionKey} card={card} />
      ))}
    </div>
  );
}

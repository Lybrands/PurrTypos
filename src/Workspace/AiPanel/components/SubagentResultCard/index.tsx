import React from "react";
import { message as antdMessage } from "antd";
import type { EntityId } from "../../../../types";
import type { WritingSubagentRole } from "../../pipelineStages";
import ContinuationPlanResult from "./ContinuationPlanResult";
import PolishResult from "./PolishResult";
import ReviewResult from "./ReviewResult";
import StyleUnifyResult from "./StyleUnifyResult";
import "./index.scss";

export interface SubagentResultCardProps {
  role: WritingSubagentRole;
  payload: unknown;
  chapterId: EntityId | null | undefined;
}

export default function SubagentResultCard({
  role,
  payload,
  chapterId,
}: SubagentResultCardProps) {
  const applyChapter = React.useCallback(
    async (content: string) => {
      if (!chapterId) {
        antdMessage.warning("未选择章节，无法写入");
        return;
      }
      if (!content?.trim()) {
        antdMessage.warning("无正文可写入");
        return;
      }

      try {
        const response = await window.electronAPI.getArticle({ chapterId });
        const beforeText =
          response?.success && response.data ? response.data.content || "" : "";
        window.dispatchEvent(
          new CustomEvent("ai-propose-chapter-diff", {
            detail: {
              chapterId,
              beforeText,
              proposedText: content,
              source: `subagent_${role}`,
            },
          }),
        );
        antdMessage.success("已生成 diff，请到写作区接受/拒绝");
      } catch (error) {
        console.error("[SubagentResultCard] propose diff failed", error);
        antdMessage.error("生成 diff 失败");
      }
    },
    [chapterId, role],
  );

  if (role === "review") {
    return <ReviewResult payload={payload} />;
  }
  if (role === "continuation_plan") {
    return <ContinuationPlanResult payload={payload} />;
  }
  if (role === "polish") {
    return <PolishResult payload={payload} applyChapter={applyChapter} />;
  }
  return <StyleUnifyResult payload={payload} applyChapter={applyChapter} />;
}

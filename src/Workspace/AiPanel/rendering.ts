export interface AssistantRenderableMessage {
  content?: string;
  contentAfterToolCalls?: string;
  toolCallSegments?: Array<{ textBefore: string; labels: string[] }>;
}

export interface AssistantErrorRenderResult
  extends Pick<
    AssistantRenderableMessage,
    "content" | "contentAfterToolCalls"
  > {
  isError?: boolean;
}

export function getAssistantRenderableMarkdown(
  msg: AssistantRenderableMessage,
): string {
  if ((msg.toolCallSegments?.length ?? 0) > 0) {
    return msg.contentAfterToolCalls ?? msg.content ?? "";
  }
  return msg.content ?? "";
}

export function appendAssistantTailMarkdown(
  msg: AssistantRenderableMessage,
  markdown: string,
): Pick<AssistantRenderableMessage, "content" | "contentAfterToolCalls"> {
  if (!markdown) {
    return {
      content: msg.content ?? "",
      contentAfterToolCalls: msg.contentAfterToolCalls,
    };
  }

  if ((msg.toolCallSegments?.length ?? 0) > 0) {
    const prefix = (msg.toolCallSegments ?? [])
      .map((segment) => segment.textBefore || "")
      .join("");
    const nextAfter = (msg.contentAfterToolCalls ?? "") + markdown;
    return {
      content: `${prefix}${nextAfter}`,
      contentAfterToolCalls: nextAfter,
    };
  }

  return {
    content: (msg.content ?? "") + markdown,
    contentAfterToolCalls: msg.contentAfterToolCalls,
  };
}

export function mergeAssistantErrorNotice(
  msg: AssistantRenderableMessage,
  errorText?: string,
): AssistantErrorRenderResult {
  const visible = getAssistantRenderableMarkdown(msg).trim();
  if (!visible) {
    const detail = String(errorText || "").trim();
    const fallback = detail
      ? `生成失败：${detail}`
      : "本轮已结束，请继续下一条指令。";
    return {
      content: fallback,
      contentAfterToolCalls: msg.contentAfterToolCalls,
      isError: true,
    };
  }

  const detail = String(errorText || "").trim();
  const notice = detail ? `\n\n> 生成中断：${detail}` : "\n\n> 生成中断。";
  return appendAssistantTailMarkdown(msg, notice);
}

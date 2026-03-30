export interface AssistantRenderableMessage {
  content?: string;
  contentAfterToolCalls?: string;
  toolCallSegments?: Array<{ textBefore: string; labels: string[] }>;
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

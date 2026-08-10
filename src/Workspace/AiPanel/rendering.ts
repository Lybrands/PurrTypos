export interface AssistantRenderableMessage {
  content?: string;
}

export interface AssistantErrorRenderResult extends AssistantRenderableMessage {
  isError?: boolean;
}

export function getAssistantRenderableMarkdown(
  msg: AssistantRenderableMessage,
): string {
  return msg.content ?? "";
}

export function appendAssistantTailMarkdown(
  msg: AssistantRenderableMessage,
  markdown: string,
): Pick<AssistantRenderableMessage, "content"> {
  if (!markdown) {
    return { content: msg.content ?? "" };
  }

  return { content: (msg.content ?? "") + markdown };
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
      isError: true,
    };
  }

  const detail = String(errorText || "").trim();
  const notice = detail ? `\n\n> 生成中断：${detail}` : "\n\n> 生成中断。";
  return appendAssistantTailMarkdown(msg, notice);
}

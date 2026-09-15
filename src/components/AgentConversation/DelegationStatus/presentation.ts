import type { AgentConversationMessage } from "../../../agent-runtime/contracts.ts";
import type { AiSubAgentConversation } from "../../../types.ts";
import type { AiAgentDelegation } from "../../../types.ts";

export function collapseSubAgentDelegations(
  items: readonly AiAgentDelegation[],
): AiAgentDelegation[] {
  const collapsed: AiAgentDelegation[] = [];
  const indexes = new Map<string, number>();
  for (const item of items) {
    const unitId = String(item.unitId || "").trim();
    const agentId = String(item.agentId || "").trim();
    const key = unitId
      ? `unit:${unitId}`
      : agentId
        ? `agent:${agentId}`
        : `delegation:${item.delegationId}`;
    const index = indexes.get(key);
    if (index == null) {
      indexes.set(key, collapsed.length);
      collapsed.push(item);
      continue;
    }
    const current = collapsed[index];
    collapsed[index] = {
      ...current,
      ...item,
      delegationId: current.delegationId,
      startedAt: current.startedAt ?? item.startedAt,
    };
  }
  return collapsed;
}
const PREFERRED_TEXT_KEYS = [
  "summaryMarkdown", "answer", "message", "summary", "analysis",
  "bodyMarkdown", "content", "text", "profile_md", "finding",
] as const;

/** Keep Child protocol payloads behind the conversation projection boundary. */
export function presentSubAgentResponse(
  value: string | undefined,
): string {
  const content = String(value || "").trim();
  if (!content) return "";
  const unfenced = structuredCandidate(content);
  try {
    return projectStructuredValue(JSON.parse(unfenced))
      || "已完成处理，结果已交付主 Agent。";
  } catch {
    if (unfenced.startsWith("{") || unfenced.startsWith("[")) {
      return extractJsonStringField(unfenced, "summaryMarkdown")
        || "该次结构化结果未能完整解析，主 Agent 将重新执行此步骤。";
    }
    return content;
  }
}

function extractJsonStringField(value: string, field: string): string {
  const marker = `"${field}"`;
  const fieldIndex = value.indexOf(marker);
  if (fieldIndex < 0) return "";
  const colonIndex = value.indexOf(":", fieldIndex + marker.length);
  if (colonIndex < 0) return "";
  const start = value.indexOf('"', colonIndex + 1);
  if (start < 0) return "";
  let escaped = false;
  for (let index = start + 1; index < value.length; index += 1) {
    const char = value[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char !== '"') continue;
    try {
      return String(JSON.parse(value.slice(start, index + 1)) || "").trim();
    } catch {
      return "";
    }
  }
  return "";
}

function projectStructuredValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) return projectItems(value);
  if (!value || typeof value !== "object") return "";
  const record = value as Record<string, unknown>;
  const primary = PREFERRED_TEXT_KEYS
    .map((key) => record[key])
    .find((text): text is string => typeof text === "string" && Boolean(text.trim()))
    ?.trim() || "";
  const sections = ([
    ["findings", "分析结果", ""],
    ["conflicts", "需核对内容", ""],
    ["facts", "创作资料", "subjectKey"],
    ["craftCards", "写作技法", ""],
    ["techniques", "写作技法", ""],
  ] as const).flatMap(([key, title, titleKey]) => {
    const body = projectItems(record[key] as unknown[], titleKey || undefined);
    return body ? [`## ${title}\n\n${body}`] : [];
  });
  if (primary || sections.length) {
    return [...new Set([primary, ...sections].filter(Boolean))].join("\n\n");
  }
  return [...new Set(Object.values(record)
    .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    .map((item) => item.trim()))].join("\n\n");
}

function projectItems(value: unknown, titleKey?: string): string {
  if (!Array.isArray(value)) return "";
  return value.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      const body = projectStructuredValue(item);
      return body ? [body] : [];
    }
    const record = item as Record<string, unknown>;
    const title = [titleKey, "subject", "title", "name"]
      .filter((key): key is string => Boolean(key))
      .map((key) => String(record[key] || "").trim())
      .find(Boolean) || "";
    const body = titleKey
      ? projectStructuredValue(record.value)
      : projectStructuredValue(record);
    if (!body) return [];
    return [title && title !== body ? `### ${title}\n\n${body}` : body];
  }).join("\n\n");
}

function structuredCandidate(value: string): string {
  const fenced = value.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  return (fenced?.[1] || value).trim();
}

export function buildSubAgentConversationMessages(input: {
  turns: AiSubAgentConversation["turns"];
  messagesByRunId?: ReadonlyMap<string, AgentConversationMessage>;
  error?: string | null;
}): AgentConversationMessage[] {
  return input.turns.flatMap((turn, index) => {
    const message = input.messagesByRunId?.get(turn.runId);
    const response = presentSubAgentResponse(
      turn.finalResponse || message?.content || message?.streamingContent,
    ) || (turn.status === "done"
      ? "已完成处理，结果已交付主 Agent。"
      : "");
    const assistant: AgentConversationMessage = {
      ...(message ?? { content: "" }),
      role: "assistant",
      agentRunId: turn.runId,
      content: response,
      streamingContent: message?.streamingContent && !turn.finalResponse
        ? message.streamingContent
        : undefined,
      ...(turn.status === "done" ? {
        error: undefined,
        isError: false,
        termination: undefined,
      } : {}),
    };
    if (index === input.turns.length - 1 && input.error && !response) {
      assistant.isError = true;
      assistant.error = input.error;
    }
    return [
      { role: "user", content: turn.prompt, agentRunId: turn.runId },
      assistant,
    ];
  });
}

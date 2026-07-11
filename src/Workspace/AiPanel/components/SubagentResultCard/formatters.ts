const TITLE_KEYS = ["name", "title", "label", "key", "subject"] as const;
const DESCRIPTION_KEYS = [
  "reason",
  "description",
  "desc",
  "detail",
  "content",
  "purpose",
  "note",
] as const;

function isObjectLike(value: unknown): value is Record<string, unknown> {
  return (
    value !== null && (typeof value === "object" || typeof value === "function")
  );
}

function readProperty(value: unknown, key: string): unknown {
  if (!isObjectLike(value)) return undefined;
  try {
    return value[key];
  } catch {
    return undefined;
  }
}

export function toSafeString(value: unknown): string {
  if (value == null) return "";
  try {
    return String(value);
  } catch {
    return "";
  }
}

function toTrimmedString(value: unknown): string {
  return toSafeString(value).trim();
}

function toTrimmedText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** 把对象数组中的常见标题、描述字段拍成可读字符串。 */
export function stringifyItem(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (!isObjectLike(value)) return "";

  const titleKey = TITLE_KEYS.find((key) => toTrimmedText(readProperty(value, key)));
  const descriptionKey = DESCRIPTION_KEYS.find((key) =>
    toTrimmedText(readProperty(value, key)),
  );
  const title = titleKey ? toTrimmedText(readProperty(value, titleKey)) : "";
  const description = descriptionKey
    ? toTrimmedText(readProperty(value, descriptionKey))
    : "";

  if (title && description) return `${title}：${description}`;
  if (title) return title;
  if (description) return description;

  try {
    return Object.entries(value)
      .filter(([, fieldValue]) =>
        typeof fieldValue === "string" && fieldValue.trim(),
      )
      .map(([key, fieldValue]) => `${key}=${(fieldValue as string).trim()}`)
      .join("；");
  } catch {
    return "";
  }
}

function getStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(stringifyItem).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

/** 把 continuation_plan 的 blueprint JSON 拍成纯文本，方便用户复制。 */
export function formatContinuationPlanText(payload: unknown): string {
  const nestedBlueprint = readProperty(payload, "blueprint");
  const blueprint = isObjectLike(nestedBlueprint) ? nestedBlueprint : payload;
  const lines: string[] = [];
  const goal = toTrimmedString(readProperty(blueprint, "chapterGoal"));
  const tone = toTrimmedString(readProperty(blueprint, "tone"));
  const constraints = toTrimmedString(readProperty(blueprint, "constraints"));
  const materials = getStringList(readProperty(blueprint, "requiredMaterials"));
  const beatsRaw = readProperty(blueprint, "beats");
  const beats = Array.isArray(beatsRaw) ? beatsRaw.filter(isObjectLike) : [];

  if (goal) {
    lines.push("【本章目标】", goal, "");
  }

  if (beats.length > 0) {
    lines.push("【节拍编排】");
    beats.forEach((beat, index) => {
      const beatId = readProperty(beat, "beatId");
      const id = beatId == null ? String(index + 1) : toSafeString(beatId);
      const type = toTrimmedString(readProperty(beat, "type"));
      const estimatedWords = toTrimmedString(readProperty(beat, "estimatedWords"));
      const heading = [`#${id}`, type, estimatedWords ? `约 ${estimatedWords} 字` : ""]
        .filter(Boolean)
        .join(" · ");
      lines.push(heading);

      const content = toTrimmedString(readProperty(beat, "content"));
      if (content) lines.push(`  内容：${content}`);
      const purpose = toTrimmedString(readProperty(beat, "purpose"));
      if (purpose) lines.push(`  目的：${purpose}`);

      const keyPointsRaw = readProperty(beat, "keyPoints");
      const keyPoints = Array.isArray(keyPointsRaw)
        ? keyPointsRaw.map(stringifyItem).filter(Boolean)
        : [];
      if (keyPoints.length > 0) {
        lines.push("  关键点：");
        keyPoints.forEach((keyPoint) => lines.push(`    · ${keyPoint}`));
      }
      lines.push("");
    });
  }

  if (tone) {
    lines.push("【语气 / 文风】", tone, "");
  }
  if (constraints) {
    lines.push("【硬性约束】", constraints, "");
  }
  if (materials.length > 0) {
    lines.push("【需要的素材】");
    materials.forEach((material) => lines.push(`· ${material}`));
    lines.push("");
  }

  return lines.join("\n").trimEnd();
}

export interface FormattedReviewIssue {
  severity: string;
  issueType: string;
  span: string;
  suggestion: string;
  context: string;
  hasContext: boolean;
}

export function formatReviewIssues(payload: unknown): FormattedReviewIssue[] {
  const issues = readProperty(payload, "issues");
  if (!Array.isArray(issues)) return [];

  return issues.map((issue) => {
    const contextValue = readProperty(issue, "context");
    return {
      severity: toSafeString(readProperty(issue, "severity")),
      issueType: toSafeString(readProperty(issue, "issueType")),
      span: toSafeString(readProperty(issue, "span")),
      suggestion: toSafeString(readProperty(issue, "suggestion")),
      context: toSafeString(contextValue),
      hasContext: Boolean(contextValue),
    };
  });
}

export function formatReviewIssuesForClipboard(issues: FormattedReviewIssue[]): string {
  return issues
    .map((issue) => `- [${issue.severity}] ${issue.span}\n  ${issue.suggestion}`)
    .join("\n");
}

export function stringifyPayload(payload: unknown): string {
  try {
    const serialized = JSON.stringify(payload ?? {}, null, 2);
    return typeof serialized === "string" ? serialized : "";
  } catch {
    return toSafeString(payload);
  }
}

export interface FormattedPolishPayload {
  body: string;
  summary: string;
  rawDump: string;
}

export function formatPolishPayload(payload: unknown): FormattedPolishPayload {
  const body = toTrimmedText(readProperty(payload, "finalText"));
  return {
    body,
    summary: toTrimmedText(readProperty(payload, "changeSummary")),
    rawDump: body ? "" : stringifyPayload(payload),
  };
}

export interface FormattedStyleUnifyPayload {
  body: string;
  summary: string;
  anchors: string;
}

export function formatStyleUnifyPayload(payload: unknown): FormattedStyleUnifyPayload {
  return {
    body: toTrimmedText(readProperty(payload, "content")),
    summary: toTrimmedText(readProperty(payload, "changeSummary")),
    anchors: toTrimmedText(readProperty(payload, "styleAnchors")),
  };
}

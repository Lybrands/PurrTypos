export interface StructuredQuestion {
  question: string;
  options: string[];
}

const MAX_QUESTIONS = 5;
const MAX_OPTIONS = 10;
const MAX_QUESTION_LENGTH = 500;
const MAX_OPTION_LENGTH = 100;

function unwrapJsonFence(value: string): string {
  const trimmed = value.trim();
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return match ? match[1].trim() : trimmed;
}

function normalizeQuestion(value: unknown): StructuredQuestion | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const question = typeof raw.question === "string" ? raw.question.trim() : "";
  if (!question || question.length > MAX_QUESTION_LENGTH) return null;
  if (
    !Array.isArray(raw.options) ||
    raw.options.length < 2 ||
    raw.options.length > MAX_OPTIONS
  ) {
    return null;
  }
  const options = raw.options.map((option) =>
    typeof option === "string" ? option.trim() : "",
  );
  if (
    options.some(
      (option) => !option || option.length > MAX_OPTION_LENGTH,
    )
  ) {
    return null;
  }
  const uniqueOptions = [...new Set(options)];
  if (uniqueOptions.length < 2) return null;
  return { question, options: uniqueOptions };
}

/**
 * 仅把“整段都是问答 JSON”的模型输出识别为问题卡片。
 * 不从正文、代码示例或混合 Markdown 中搜索 JSON，避免误判用户内容。
 */
export function parseStructuredQuestions(
  markdown: string,
): StructuredQuestion[] | null {
  const source = unwrapJsonFence(markdown);
  if (!source || (!source.startsWith("[") && !source.startsWith("{"))) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return null;
  }
  const rows = Array.isArray(parsed) ? parsed : [parsed];
  if (rows.length === 0 || rows.length > MAX_QUESTIONS) return null;
  const questions = rows.map(normalizeQuestion);
  if (questions.some((question) => question == null)) return null;
  return questions as StructuredQuestion[];
}

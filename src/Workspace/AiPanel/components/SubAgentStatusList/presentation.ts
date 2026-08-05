export function presentableStructuredResponse(
  value: string | undefined,
): string {
  const content = String(value || "").trim();
  if (!content) return "";
  const unfenced = content
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();
  try {
    const payload = JSON.parse(unfenced) as Record<string, unknown>;
    if (Array.isArray(payload.scenes)) {
      return String(payload.assistantResponse || "").trim();
    }
  } catch {
    // Partial structured candidates must not flash protocol JSON in chat.
    if (unfenced.startsWith("{") || /^```json/i.test(content)) {
      return "";
    }
  }
  return content;
}

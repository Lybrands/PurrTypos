/**
 * On-demand writing sub-experts (matches backend `subagentRole`).
 * Replaces the former multi-select pipeline stages.
 */

export type WritingSubagentRole =
  | "review"
  | "polish"
  | "continuation_plan"
  | "style_unify";

export const WRITING_SUBAGENT_OPTIONS: {
  value: WritingSubagentRole;
  label: string;
}[] = [
  { value: "review", label: "审校专家" },
  { value: "polish", label: "润色专家" },
  { value: "continuation_plan", label: "续写规划" },
  { value: "style_unify", label: "风格统一" },
];

/** Parse leading slash command: /review /polish /plan /style */
export function parseWritingSlashCommand(text: string): {
  stripped: string;
  role?: WritingSubagentRole;
} {
  const m = text.match(/^\s*\/(review|polish|plan|style)\b(?:\s|\n|$)/i);
  if (!m) return { stripped: text };
  const cmd = m[1].toLowerCase();
  const map: Record<string, WritingSubagentRole> = {
    review: "review",
    polish: "polish",
    plan: "continuation_plan",
    style: "style_unify",
  };
  const role = map[cmd];
  const stripped = text.slice(m[0].length).trim();
  return role ? { stripped, role } : { stripped: text };
}

/**
 * Historical writing sub-expert roles kept for replaying old conversation chunks.
 */

export type WritingSubagentRole =
  | "review"
  | "polish"
  | "continuation_plan"
  | "style_unify";

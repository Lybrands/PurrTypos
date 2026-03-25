/** 写作专家管线阶段（与 electron/subagentConfig EXEC_ACTIONS 对齐，不含 full） */
export const PIPELINE_STAGE_ORDER = [
  "analyze",
  "plan",
  "draft",
  "styleUnify",
  "review",
  "polish",
] as const;

export type PipelineStageId = (typeof PIPELINE_STAGE_ORDER)[number] | "full";

/** 多选下拉选项（全流程与其它阶段互斥） */
export const PIPELINE_SELECT_OPTIONS: { value: PipelineStageId; label: string }[] = [
  { value: "full", label: "全流程" },
  { value: "analyze", label: "分析" },
  { value: "plan", label: "规划" },
  { value: "draft", label: "撰稿" },
  { value: "styleUnify", label: "风格统一" },
  { value: "review", label: "审校" },
  { value: "polish", label: "润色" },
];

/**
 * 多选归一：空 → 全流程；选「全流程」则仅保留全流程；否则按管线顺序去重。
 */
export function normalizePipelineSelection(selected: string[]): PipelineStageId[] {
  const raw = Array.isArray(selected) ? selected.map(String) : [];
  if (raw.length === 0) return ["full"];
  if (raw.includes("full")) return ["full"];
  const set = new Set(raw);
  const ordered = PIPELINE_STAGE_ORDER.filter((k) => set.has(k));
  return ordered.length > 0 ? ordered : ["full"];
}

/** 气泡内 Checkbox 切换：全流程与其余阶段互斥 */
export function applyPipelineCheckboxToggle(
  current: PipelineStageId[],
  key: PipelineStageId,
  checked: boolean,
): PipelineStageId[] {
  if (key === "full") {
    if (checked) return ["full"];
    const rest = current.filter((x) => x !== "full");
    return normalizePipelineSelection(rest.length > 0 ? rest : ["analyze"]);
  }
  if (checked) {
    const base = current.filter((x) => x !== "full");
    return normalizePipelineSelection([...base, key]);
  }
  return normalizePipelineSelection(current.filter((x) => x !== key));
}

export function pipelineIsNonDefaultFull(stages: PipelineStageId[]): boolean {
  return !(stages.length === 1 && stages[0] === "full");
}

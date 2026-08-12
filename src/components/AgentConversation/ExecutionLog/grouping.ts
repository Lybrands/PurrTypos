import type { AssistantTimelinePart } from "../AssistantOutput/timeline.ts";

export type ExecutionLogTimelineItem = AssistantTimelinePart;

export function groupConsecutiveWorkSteps(
  parts: AssistantTimelinePart[],
  _groupKeyPrefix: string,
): ExecutionLogTimelineItem[] {
  return parts.filter((part) => {
    if (
      part.type === "tools"
      && part.segment.labels.every(
        (_label, labelIndex) => part.segment.cachedFlags?.[labelIndex],
      )
    ) {
      return false;
    }
    return true;
  });
}

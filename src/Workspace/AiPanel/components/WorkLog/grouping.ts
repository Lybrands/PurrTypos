import type { AssistantTimelinePart } from "../ChatMessageList/assistantTimeline.ts";

export type WorkLogTimelineItem = AssistantTimelinePart;

export function groupConsecutiveWorkSteps(
  parts: AssistantTimelinePart[],
  _groupKeyPrefix: string,
): WorkLogTimelineItem[] {
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

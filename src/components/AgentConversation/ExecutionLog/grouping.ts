import type {
  AssistantTimelinePart,
  TimelineOperationPart,
} from "../AssistantOutput/timeline.ts";

export type ExecutionLogTimelineItem =
  | AssistantTimelinePart
  | {
      type: "stepGroup";
      groupKey: string;
      parts: TimelineOperationPart[];
    };

function isOperationPart(
  part: AssistantTimelinePart,
): part is TimelineOperationPart {
  return part.type === "tools"
    || part.type === "operation"
    || part.type === "delegations"
    || part.type === "contextCompaction";
}

export function groupConsecutiveWorkSteps(
  parts: AssistantTimelinePart[],
  groupKeyPrefix: string,
): ExecutionLogTimelineItem[] {
  const items: ExecutionLogTimelineItem[] = [];
  let stepParts: TimelineOperationPart[] = [];
  let groupStartIndex = 0;

  const flushSteps = () => {
    if (stepParts.length === 0) return;
    items.push(
      stepParts.length === 1
        ? stepParts[0]
        : {
            type: "stepGroup",
            groupKey: `${groupKeyPrefix}-work-steps-${groupStartIndex}`,
            parts: stepParts,
          },
    );
    stepParts = [];
  };

  parts.forEach((part, partIndex) => {
    if (
      part.type === "tools"
      && part.segment.labels.every(
        (_label, labelIndex) => part.segment.cachedFlags?.[labelIndex],
      )
    ) {
      return;
    }
    if (isOperationPart(part)) {
      if (stepParts.length === 0) groupStartIndex = partIndex;
      stepParts.push(part);
      return;
    }
    flushSteps();
    items.push(part);
  });
  flushSteps();
  return items;
}

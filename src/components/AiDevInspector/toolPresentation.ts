export type ToolPresentationGroup = {
  key: string;
  label: string;
};

export type ToolPresentationItem<T> =
  | { type: "tool"; tool: T }
  | {
      type: "group";
      groupKey: string;
      label: string;
      tools: T[];
    };

export function groupToolPresentation<
  T extends { presentationGroup?: ToolPresentationGroup },
>(tools: readonly T[]): ToolPresentationItem<T>[] {
  const items: ToolPresentationItem<T>[] = [];
  const groups = new Map<string, Extract<ToolPresentationItem<T>, { type: "group" }>>();
  for (const tool of tools) {
    const key = tool.presentationGroup?.key.trim() || "";
    const label = tool.presentationGroup?.label.trim() || "";
    if (!key || !label) {
      items.push({ type: "tool", tool });
      continue;
    }
    const existing = groups.get(key);
    if (existing) {
      existing.tools.push(tool);
      continue;
    }
    const group: Extract<ToolPresentationItem<T>, { type: "group" }> = {
      type: "group",
      groupKey: key,
      label,
      tools: [tool],
    };
    groups.set(key, group);
    items.push(group);
  }
  return items;
}

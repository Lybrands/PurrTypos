let fallbackSequence = 0;

export function createAiStreamId(scope: string): string {
  const normalizedScope = scope.replace(/[^a-zA-Z0-9_-]/g, "-");
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${normalizedScope}-${crypto.randomUUID()}`;
  }
  fallbackSequence += 1;
  return `${normalizedScope}-${Date.now()}-${fallbackSequence}`;
}

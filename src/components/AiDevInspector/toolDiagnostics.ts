import type { AiToolCallDiagnostic } from '../../types';

/** IO may arrive on a later journal page, or before its start record. */
export function mergeToolDiagnostics(
  current: AiToolCallDiagnostic[],
  incoming: AiToolCallDiagnostic[],
): AiToolCallDiagnostic[] {
  const calls = new Map(current.map((call) => [JSON.stringify([call.runId, call.toolCallId]), call]));
  for (const call of incoming) {
    const key = JSON.stringify([call.runId, call.toolCallId]);
    const previous = calls.get(key);
    calls.set(key, {
      ...previous,
      ...call,
      eventRowId: Math.min(previous?.eventRowId ?? call.eventRowId, call.eventRowId),
    });
  }
  return [...calls.values()].sort((left, right) => left.eventRowId - right.eventRowId);
}

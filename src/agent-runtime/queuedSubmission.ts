export interface QueuedSubmissionEdit { content?: string; editing?: boolean }

/** Only mutate items still owned by the pending queue, preserving their frozen context. */
export function changeQueuedSubmission<T extends { id: string; content: string; editing?: boolean }>(
  queue: T[], id: string, patch: QueuedSubmissionEdit | null, owns: (item: T) => boolean,
): T[] {
  const index = queue.findIndex(item => item.id === id && owns(item))
  if (index < 0 || (patch?.content !== undefined && !patch.content.trim())) return queue
  if (patch === null) return queue.filter((_, i) => i !== index)
  return queue.map((item, i) => i === index ? { ...item, ...patch,
    ...(patch.content === undefined ? {} : { content: patch.content.trim() }),
  } : item)
}

export interface WorkLogOpenState {
  open: boolean;
  manuallySet: boolean;
}

export const WORK_LOG_OPEN_STATE_LIMIT = 200;

export function getInitialWorkLogOpenState(
  stored: WorkLogOpenState | undefined,
  autoOpen: boolean,
): WorkLogOpenState {
  if (stored?.manuallySet) return stored;
  return { open: autoOpen, manuallySet: false };
}

export function applyWorkLogAutoOpen(
  state: WorkLogOpenState,
  autoOpen: boolean,
): WorkLogOpenState {
  if (state.manuallySet || state.open === autoOpen) return state;
  return { open: autoOpen, manuallySet: false };
}

export function toggleWorkLogOpenState(
  state: WorkLogOpenState,
): WorkLogOpenState {
  return { open: !state.open, manuallySet: true };
}

export function readWorkLogOpenState(
  cache: Map<string, WorkLogOpenState>,
  key: string,
): WorkLogOpenState | undefined {
  const state = cache.get(key);
  if (!state) return undefined;
  cache.delete(key);
  cache.set(key, state);
  return state;
}

export function writeWorkLogOpenState(
  cache: Map<string, WorkLogOpenState>,
  key: string,
  state: WorkLogOpenState,
  limit = WORK_LOG_OPEN_STATE_LIMIT,
): void {
  cache.delete(key);
  cache.set(key, state);
  while (cache.size > limit) {
    const oldest = cache.keys().next();
    if (oldest.done) return;
    cache.delete(oldest.value);
  }
}

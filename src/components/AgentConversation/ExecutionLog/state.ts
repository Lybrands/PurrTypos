export interface ExecutionLogOpenState {
  open: boolean;
  manuallySet: boolean;
}

export const EXECUTION_LOG_OPEN_STATE_LIMIT = 200;

export function getInitialExecutionLogOpenState(
  stored: ExecutionLogOpenState | undefined,
  autoOpen: boolean,
): ExecutionLogOpenState {
  if (stored?.manuallySet) return stored;
  return { open: autoOpen, manuallySet: false };
}

export function applyExecutionLogAutoOpen(
  state: ExecutionLogOpenState,
  autoOpen: boolean,
): ExecutionLogOpenState {
  if (state.manuallySet || state.open === autoOpen) return state;
  return { open: autoOpen, manuallySet: false };
}

export function toggleExecutionLogOpenState(
  state: ExecutionLogOpenState,
): ExecutionLogOpenState {
  return { open: !state.open, manuallySet: true };
}

export function readExecutionLogOpenState(
  cache: Map<string, ExecutionLogOpenState>,
  key: string,
): ExecutionLogOpenState | undefined {
  const state = cache.get(key);
  if (!state) return undefined;
  cache.delete(key);
  cache.set(key, state);
  return state;
}

export function writeExecutionLogOpenState(
  cache: Map<string, ExecutionLogOpenState>,
  key: string,
  state: ExecutionLogOpenState,
  limit = EXECUTION_LOG_OPEN_STATE_LIMIT,
): void {
  cache.delete(key);
  cache.set(key, state);
  while (cache.size > limit) {
    const oldest = cache.keys().next();
    if (oldest.done) return;
    cache.delete(oldest.value);
  }
}

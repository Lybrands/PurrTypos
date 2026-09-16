export type AgentOperationMode = 'request_approval' | 'auto_approve' | 'full_access'

export const AGENT_OPERATION_MODE_OPTIONS = [
  { value: 'request_approval', label: '请求批准' },
  { value: 'auto_approve', label: '帮我批准' },
  { value: 'full_access', label: '完全访问' },
] satisfies Array<{ value: AgentOperationMode; label: string }>

export const DEFAULT_AGENT_OPERATION_MODE: AgentOperationMode = 'request_approval'
const STORAGE_KEY = 'purr-typos:agent-operation-mode'
const EVENT_NAME = 'purr-typos:agent-operation-mode-change'

export function normalizeAgentOperationMode(value: unknown): AgentOperationMode {
  return AGENT_OPERATION_MODE_OPTIONS.some(option => option.value === value)
    ? value as AgentOperationMode
    : DEFAULT_AGENT_OPERATION_MODE
}

export function getAgentOperationMode(): AgentOperationMode {
  try { return normalizeAgentOperationMode(localStorage.getItem(STORAGE_KEY)) }
  catch { return DEFAULT_AGENT_OPERATION_MODE }
}

export function setAgentOperationMode(value: AgentOperationMode): void {
  const normalized = normalizeAgentOperationMode(value)
  try { localStorage.setItem(STORAGE_KEY, normalized) } catch {}
  globalThis.dispatchEvent?.(new CustomEvent(EVENT_NAME, { detail: normalized }))
}

export function subscribeAgentOperationMode(listener: (value: AgentOperationMode) => void): () => void {
  const handle = (event: Event) => listener(normalizeAgentOperationMode((event as CustomEvent).detail))
  globalThis.addEventListener?.(EVENT_NAME, handle)
  return () => globalThis.removeEventListener?.(EVENT_NAME, handle)
}

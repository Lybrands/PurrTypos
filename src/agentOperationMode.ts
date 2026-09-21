import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { loggedJsonStorage } from './stores/persistStorage'

export type AgentOperationMode = 'request_approval' | 'auto_approve' | 'full_access'

export const AGENT_OPERATION_MODE_OPTIONS = [
  { value: 'request_approval', label: '请求批准' },
  { value: 'auto_approve', label: '帮我批准' },
  { value: 'full_access', label: '完全访问' },
] satisfies Array<{ value: AgentOperationMode; label: string }>

export const DEFAULT_AGENT_OPERATION_MODE: AgentOperationMode = 'request_approval'

interface AgentOperationModeState {
  mode: AgentOperationMode
  setMode(mode: AgentOperationMode): void
}

export const useAgentOperationModeStore = create<AgentOperationModeState>()(
  persist(
    (set) => ({
      mode: DEFAULT_AGENT_OPERATION_MODE,
      setMode: (mode) => set({ mode }),
    }),
    {
      name: 'purr-ty-pos:agent-operation-mode',
      storage: loggedJsonStorage,
      partialize: (state) => ({ mode: state.mode }),
      merge: (persisted, current) => ({
        ...current,
        mode: normalizeAgentOperationMode(
          (persisted as { mode?: unknown } | undefined)?.mode,
        ),
      }),
    },
  ),
)

export function normalizeAgentOperationMode(value: unknown): AgentOperationMode {
  return AGENT_OPERATION_MODE_OPTIONS.some(option => option.value === value)
    ? value as AgentOperationMode
    : DEFAULT_AGENT_OPERATION_MODE
}

export function getAgentOperationMode(): AgentOperationMode {
  return useAgentOperationModeStore.getState().mode
}

export function setAgentOperationMode(value: AgentOperationMode): void {
  useAgentOperationModeStore.getState().setMode(normalizeAgentOperationMode(value))
}

export function subscribeAgentOperationMode(listener: (value: AgentOperationMode) => void): () => void {
  return useAgentOperationModeStore.subscribe((state) => {
    listener(state.mode)
  })
}

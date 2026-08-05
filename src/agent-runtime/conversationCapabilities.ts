export type AgentSubmitMode = 'send' | 'queue'

export interface AgentConversationCapabilities {
  inputDisabled: boolean
  sessionNavigationDisabled: boolean
  submitMode: AgentSubmitMode
}

/**
 * Shared interaction policy for every Agent conversation surface.
 *
 * A running model call must not make the composer or conversation list
 * read-only. It changes submission into queuing; only an explicitly read-only
 * domain disables typing, while session loading blocks navigation briefly.
 */
export function getAgentConversationCapabilities(params: {
  running: boolean
  readOnly: boolean
  sessionLoading: boolean
}): AgentConversationCapabilities {
  return {
    inputDisabled: params.readOnly,
    sessionNavigationDisabled: params.sessionLoading,
    submitMode: params.running ? 'queue' : 'send',
  }
}

export type AgentSubmitMode = 'send' | 'queue'

export interface AgentConversationCapabilities {
  inputDisabled: boolean
  submitMode: AgentSubmitMode
}

export function getAgentConversationCapabilities(params: {
  running: boolean
  readOnly: boolean
}): AgentConversationCapabilities {
  return {
    inputDisabled: params.readOnly,
    submitMode: params.running ? 'queue' : 'send',
  }
}

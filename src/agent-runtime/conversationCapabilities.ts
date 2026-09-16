export type AgentSubmitMode = 'send' | 'queue'

export interface AgentConversationCapabilities {
  inputDisabled: boolean
  submitMode: AgentSubmitMode
  /**
   * 无会话时是否允许直接发送（发送路径负责自动创建会话）。
   * 默认允许；发送路径无法自动建会话的面板需显式传 false 关闭。
   */
  sessionlessSend: boolean
}

export function getAgentConversationCapabilities(params: {
  running: boolean
  readOnly: boolean
  sessionlessSend?: boolean
}): AgentConversationCapabilities {
  return {
    inputDisabled: params.readOnly,
    submitMode: params.running ? 'queue' : 'send',
    sessionlessSend: params.sessionlessSend !== false,
  }
}

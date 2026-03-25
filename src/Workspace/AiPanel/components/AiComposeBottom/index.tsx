import React from 'react'
import { Divider, Select, Switch, Tooltip } from 'antd'
import './index.scss'
import type { AiModelConfig } from '../../../../types'
import type { ChatAgentMode } from '../../utils'

export interface AiComposeBottomProps {
  /** 模型列表来自设置；为空时下拉无选项，需先在设置中添加模型 */
  modelConfigs: AiModelConfig[]
  chatAgentMode: ChatAgentMode
  setChatAgentMode: (v: ChatAgentMode) => void
  selectedModel: string
  setSelectedModel: (v: string) => void
  thinkingEnabled: boolean
  setThinkingEnabled: (v: boolean) => void
  /** 必须开启思考、不可关闭的模型 id 列表 */
  thinkingOnlyModelIds: string[]
  loading: boolean
  onAbort: () => void
  /** 右侧按钮区域：主输入区传 Stop/Send，编辑气泡传 取消+发送 */
  rightContent: React.ReactNode
}

export default function AiComposeBottom({
  modelConfigs,
  chatAgentMode,
  setChatAgentMode,
  selectedModel,
  setSelectedModel,
  thinkingEnabled,
  setThinkingEnabled,
  thinkingOnlyModelIds,
  loading,
  onAbort,
  rightContent,
}: AiComposeBottomProps) {
  const modelOptions = React.useMemo(
    () => modelConfigs.map((c) => ({ label: (c.nickname?.trim() || c.name) || '未命名', value: c.id })),
    [modelConfigs]
  )

  const thinkingOnly = thinkingOnlyModelIds.includes(selectedModel)

  const agentModeSelectStyles = React.useMemo(() => {
    if (chatAgentMode === 'legacy') {
      return {
        root: {
          border: 'none',
          boxShadow: '0 0 2px var(--accent), 0 0 2px var(--accent)',
          background: 'var(--accent-dim)',
          color: 'var(--accent)',
        },
        suffix: { color: 'var(--accent)' },
      } as const
    }
    if (chatAgentMode === 'subagent') {
      return {
        root: {
          border: 'none',
          boxShadow: '0 0 2px var(--warning), 0 0 2px var(--warning)',
          background: 'color-mix(in srgb, var(--warning) 18%, var(--bg-surface))',
          color: 'var(--warning)',
        },
        suffix: { color: 'var(--warning)' },
      } as const
    }
    return {
      root: {
        border: 'none',
        boxShadow: '0 0 2px var(--success), 0 0 2px var(--success)',
        background: 'var(--success-dim)',
        color: 'var(--success)',
      },
      suffix: { color: 'var(--success)' },
    } as const
  }, [chatAgentMode])

  return (
    <div className="chat-input-bottom">
      <div className="chat-input-bottom-left">
        <Select
          className={`ai-agent-select ${chatAgentMode === 'legacy' ? 'ai-agent-select--on' : ''} ${chatAgentMode === 'subagent' ? 'ai-agent-select--subagent' : ''}`}
          size="small"
          value={chatAgentMode}
          onChange={setChatAgentMode}
          options={[
            { value: 'subagent', label: '写作专家' },
            { value: 'legacy', label: '智能体' },
            { value: 'ask', label: '问答' },
          ]}
          variant="filled"
          popupMatchSelectWidth={false}
          styles={agentModeSelectStyles}
        />
        <Select
          className="ai-model-select"
          size="small"
          value={modelOptions.length ? selectedModel : undefined}
          onChange={(v) => setSelectedModel(v)}
          options={modelOptions}
          placeholder={modelOptions.length ? undefined : '请先在设置中添加模型'}
          variant="borderless"
          popupMatchSelectWidth={false}
          popupRender={(menu) =>
            chatAgentMode === 'subagent' ? (
              menu
            ) : (
              <>
                {menu}
                <Divider style={{ margin: '4px 0' }} />
                <div
                  className="ai-model-dropdown-footer"
                  onMouseDown={(e) => e.preventDefault()}
                >
                  <span className="ai-model-dropdown-label">思考模式</span>
                  <Tooltip
                    title={thinkingOnly ? '该模型不可关闭思考模式' : ''}
                  >
                    <Switch
                      size="small"
                      checked={thinkingEnabled || thinkingOnly}
                      disabled={thinkingOnly}
                      onChange={setThinkingEnabled}
                    />
                  </Tooltip>
                </div>
              </>
            )
          }
        />
      </div>
      {rightContent}
    </div>
  )
}

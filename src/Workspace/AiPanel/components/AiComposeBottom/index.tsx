import React from 'react'
import { Divider, Select, Switch, Tooltip } from 'antd'
import './index.scss'
import StopCircleIcon from '../../../../icons/StopCircleIcon'
import type { AiModelConfig } from '../../../../types'

export interface AiComposeBottomProps {
  /** 模型列表来自设置；为空时下拉无选项，需先在设置中添加模型 */
  modelConfigs: AiModelConfig[]
  agentEnabled: boolean
  setAgentEnabled: (v: boolean) => void
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
  agentEnabled,
  setAgentEnabled,
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

  return (
    <div className="chat-input-bottom">
      <div className="chat-input-bottom-left">
        <Select
          className={`ai-agent-select ${agentEnabled ? 'ai-agent-select--on' : ''}`}
          size="small"
          value={agentEnabled}
          onChange={setAgentEnabled}
          options={[
            { value: true, label: 'Agent' },
            { value: false, label: 'Ask' },
          ]}
          variant="filled"
          popupMatchSelectWidth={false}
          styles={{
            root: {
              border: 'none',
              boxShadow: agentEnabled
                ? '0 0 2px var(--accent), 0 0 2px var(--accent)'
                : '0 0 2px var(--success), 0 0 2px var(--success)',
              background: agentEnabled ? 'var(--accent-dim)' : 'var(--success-dim)',
              color: agentEnabled ? 'var(--accent)' : 'var(--success)',
            },
            suffix: {
              color: agentEnabled ? 'var(--accent)' : 'var(--success)',
            },
          }}
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
          popupRender={(menu) => (
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
          )}
        />
      </div>
      {rightContent}
    </div>
  )
}

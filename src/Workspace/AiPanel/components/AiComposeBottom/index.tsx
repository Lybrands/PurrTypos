import React from 'react'
import { Select } from '../../../../ui'
import ModelPicker, { type ModelRuntimeConfigPatch } from '../ModelPicker'
import './index.scss'
import type { AiModelConfig, ChatAgentMode } from '../../../../types'

/**
 * 模型选择的绑定集合 —— 对话模式、所选模型及其 setter。
 *
 * 这一组 prop 既用于主输入区，也会透传到编辑气泡里的 AiComposeBottom。
 * 打成一个对象后沿途只传一个 prop，叶子处用 `{...modelSelection}` 展开。
 */
export interface ModelSelectionBindings {
  chatAgentMode: ChatAgentMode
  setChatAgentMode: (v: ChatAgentMode) => void
  selectedModel: string
  setSelectedModel: (v: string) => void
  updateModelConfig?: (id: string, patch: ModelRuntimeConfigPatch) => void
}

export interface AiComposeBottomProps extends ModelSelectionBindings {
  /** 模型列表来自设置；为空时下拉无选项，需先在设置中添加模型 */
  modelConfigs: AiModelConfig[]
  loading: boolean
  onAbort: () => void
  /** 左侧模型选择前的附加操作区：主输入区传关联上下文/提示词模板入口。 */
  leftContent?: React.ReactNode
  /** 右侧按钮区域：主输入区传 Stop/Send，编辑气泡传 取消+发送 */
  rightContent: React.ReactNode
}

export default function AiComposeBottom({
  modelConfigs,
  chatAgentMode,
  setChatAgentMode,
  selectedModel,
  setSelectedModel,
  updateModelConfig,
  loading,
  onAbort,
  leftContent,
  rightContent,
}: AiComposeBottomProps) {
  return (
    <div className="chat-input-bottom">
      <div className="chat-input-bottom-left">
        <Select
          className={`ai-agent-select ${chatAgentMode === 'agent' ? 'ai-agent-select--on' : ''}`}
          size="small"
          value={chatAgentMode}
          onChange={setChatAgentMode}
          options={[
            { value: 'agent', label: '智能体' },
            { value: 'ask', label: '问答' },
          ]}
        />
        <ModelPicker
          modelConfigs={modelConfigs}
          selectedModelId={selectedModel}
          onModelChange={setSelectedModel}
          onUpdateModelConfig={updateModelConfig}
        />
        {leftContent}
      </div>
      {rightContent}
    </div>
  )
}

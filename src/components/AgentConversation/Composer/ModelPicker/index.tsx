import React from 'react'
import { CheckIcon, EditIcon } from '@/purr-components'
import { PurrDivider, PurrPopover, PurrSelect } from '@/purr-components'
import type { AiModelConfig } from '../../../../types'
import {
  AI_CONTEXT_WINDOW_LABELS,
  AI_REASONING_EFFORT_LABELS,
  getDefaultModelContextWindow,
  getModelContextWindowOptions,
  getModelReasoningEffort,
  getModelReasoningEffortOptions,
  isModelThinkingEnabled,
} from '../../../../modelCatalog'
import './index.scss'

export type ModelRuntimeConfigPatch = Partial<
  Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled' | 'reasoningEffort' | 'modelPreferences'>
>

interface ModelPickerProps {
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  onModelChange: (id: string) => void
  /** 提供时，下拉每行右侧出现编辑入口，点击弹出 Context / Thinking 配置浮层 */
  onUpdateModelConfig?: (id: string, patch: ModelRuntimeConfigPatch) => void
  disabled?: boolean
  className?: string
}

const displayModelName = (model: AiModelConfig | null | undefined): string =>
  model ? (model.nickname?.trim() || model.name) || '未命名' : ''

/**
 * 模型选择器：一个普通的模型名称下拉框。
 *
 * 关键点 —— 配置与选择完全解耦：
 * - 下拉框本身只负责「选模型」，配置入口保持为独立浮层。
 * - 每行 hover 出现「编辑」入口，点击后弹出**独立** PurrPopover 配置 Context / Thinking。
 * - 编辑浮层浮在上层（portal 到 body），不在下拉里占位、不改变下拉布局。
 * - 编辑浮层打开时用 ref 守卫，避免它的外部点击把模型下拉一起关掉。
 */
export default function ModelPicker({
  modelConfigs,
  selectedModelId,
  onModelChange,
  onUpdateModelConfig,
  disabled,
  className,
}: ModelPickerProps) {
  const [open, setOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<string | null>(null)
  // 同步镜像 editingId，供 PurrSelect.onOpenChange 即时读取（避免闭包拿到旧值）
  const editingRef = React.useRef<string | null>(null)

  const setEditing = React.useCallback((id: string | null) => {
    editingRef.current = id
    setEditingId(id)
  }, [])

  const options = React.useMemo(
    () =>
      modelConfigs.map((model) => ({
        value: model.id,
        label: displayModelName(model),
        config: model,
      })),
    [modelConfigs],
  )

  return (
    <PurrSelect
      className={`model-picker ${className ?? ''}`.trim()}
      classNames={{ popup: { root: 'model-picker-popup' } }}
      size="small"
      variant="borderless"
      popupMatchSelectWidth={false}
      disabled={disabled}
      value={options.length ? selectedModelId : undefined}
      placeholder={options.length ? undefined : '无模型配置'}
      open={open}
      onOpenChange={(next) => {
        // 编辑浮层开着时，忽略「关闭下拉」的请求，防止配置浮层连带把下拉关掉
        if (!next && editingRef.current) return
        setOpen(next)
        if (!next) setEditing(null)
      }}
      onChange={(value) => {
        onModelChange(value)
        setEditing(null)
        setOpen(false)
      }}
      options={options}
      optionRender={(option) => {
        const config = option.data.config as AiModelConfig | undefined
        if (!config) return option.label
        if (!onUpdateModelConfig) {
          return <span className="model-picker-option-name">{option.label}</span>
        }
        return (
          <div className="model-picker-option">
            <span className="model-picker-option-name">{option.label}</span>
            <PurrPopover
              trigger="click"
              nativeButton={false}
              placement="right"
              arrow={false}
              // 编辑入口在下拉行最右侧，向右再推一段，确保浮层不与下拉框重叠
              align={{ offset: [20, 0] }}
              rootClassName="model-picker-config-popover"
              open={editingId === config.id}
              onOpenChange={(next) => setEditing(next ? config.id : null)}
              content={
                <ModelRuntimeConfig
                  model={config}
                  onPatch={(patch) => onUpdateModelConfig(config.id, patch)}
                />
              }
            >
              <span
                role="button"
                aria-label="编辑模型配置"
                className={`model-picker-option-edit ${
                  editingId === config.id ? 'model-picker-option-edit--active' : ''
                }`}
                onMouseDown={(event) => {
                  // 阻止 PurrSelect 把这次点击当成「选中该项」
                  event.preventDefault()
                  event.stopPropagation()
                }}
                onClick={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                }}
              >
                <EditIcon style={{ fontSize: 12 }} />
              </span>
            </PurrPopover>
          </div>
        )
      }}
    />
  )
}

function ModelRuntimeConfig({
  model,
  onPatch,
}: {
  model: AiModelConfig
  onPatch: (patch: ModelRuntimeConfigPatch) => void
}) {
  const configuredContextWindow = getDefaultModelContextWindow(model)
  const configuredThinkingEnabled = isModelThinkingEnabled(model)
  const configuredReasoningEffort = getModelReasoningEffort(model)
  const [contextWindow, setContextWindow] = React.useState(configuredContextWindow)
  const [thinkingEnabled, setThinkingEnabled] = React.useState(configuredThinkingEnabled)
  const [reasoningEffort, setReasoningEffort] = React.useState(configuredReasoningEffort)

  React.useEffect(() => {
    setContextWindow(configuredContextWindow)
  }, [model.id, configuredContextWindow])

  React.useEffect(() => {
    setThinkingEnabled(configuredThinkingEnabled)
  }, [model.id, configuredThinkingEnabled])

  React.useEffect(() => {
    setReasoningEffort(configuredReasoningEffort)
  }, [model.id, configuredReasoningEffort])

  const contextItems = getModelContextWindowOptions(model)
  const reasoningEffortItems = getModelReasoningEffortOptions(model)
  const thinkingItems: [boolean, string][] = model.thinkingOnly
    ? [[true, '思考模式']]
    : [
        [false, '普通模式'],
        [true, '思考模式'],
      ]

  return (
    <div className="model-picker-config">
      <div className="model-picker-config-group">
        <div className="model-picker-config-title">Context</div>
        {contextItems.map((value) => (
          <button
            key={value}
            type="button"
            className="model-picker-config-item"
            aria-pressed={contextWindow === value}
            onClick={() => {
              setContextWindow(value)
              onPatch({ contextWindow: value })
            }}
          >
            <span>{AI_CONTEXT_WINDOW_LABELS[value]}</span>
            {contextWindow === value ? <CheckIcon style={{ fontSize: 12 }} /> : null}
          </button>
        ))}
      </div>
      <PurrDivider style={{ margin: '6px 0' }} />
      <div className="model-picker-config-group">
        <div className="model-picker-config-title">推理模式</div>
        {(['inherit', 'provider_default'] as const).map(state => (
          <button key={state} type="button" className="model-picker-config-item"
            aria-pressed={model.modelPreferences?.reasoning_mode?.state === state}
            onClick={() => { setThinkingEnabled(undefined); onPatch({ thinkingEnabled: undefined, modelPreferences: { reasoning_mode: { state } } }) }}>
            <span>{state === 'inherit' ? '继承任务与模型默认' : '服务商默认'}</span>
            {model.modelPreferences?.reasoning_mode?.state === state ? <CheckIcon style={{ fontSize: 12 }} /> : null}
          </button>
        ))}
        {thinkingItems.map(([value, label]) => (
          <button
            key={String(value)}
            type="button"
            className="model-picker-config-item"
            aria-pressed={thinkingEnabled === value}
            onClick={() => {
              setThinkingEnabled(value)
              onPatch({ thinkingEnabled: value })
            }}
          >
            <span>{label}</span>
            {thinkingEnabled === value ? <CheckIcon style={{ fontSize: 12 }} /> : null}
          </button>
        ))}
      </div>
      {thinkingEnabled !== false && reasoningEffortItems.length > 0 ? (
        <>
          <PurrDivider style={{ margin: '6px 0' }} />
          <div className="model-picker-config-group">
            <div className="model-picker-config-title">思考强度</div>
            <button type="button" className="model-picker-config-item"
              aria-pressed={model.modelPreferences?.reasoning_effort?.state === 'inherit'}
              onClick={() => { setReasoningEffort(undefined); onPatch({ reasoningEffort: undefined, modelPreferences: { reasoning_effort: { state: 'inherit' } } }) }}>
              <span>继承任务默认</span>
              {model.modelPreferences?.reasoning_effort?.state === 'inherit' ? <CheckIcon style={{ fontSize: 12 }} /> : null}
            </button>
            <button
              type="button"
              className="model-picker-config-item"
              aria-pressed={reasoningEffort === undefined && model.modelPreferences?.reasoning_effort?.state !== 'inherit'}
              onClick={() => {
                setReasoningEffort(undefined)
                onPatch({ reasoningEffort: undefined })
              }}
            >
              <span>服务商默认</span>
              {reasoningEffort === undefined && model.modelPreferences?.reasoning_effort?.state !== 'inherit' ? <CheckIcon style={{ fontSize: 12 }} /> : null}
            </button>
            {reasoningEffortItems.map((value) => (
              <button
                key={value}
                type="button"
                className="model-picker-config-item"
                aria-pressed={reasoningEffort === value}
                onClick={() => {
                  setReasoningEffort(value)
                  onPatch({ reasoningEffort: value })
                }}
              >
                <span>{AI_REASONING_EFFORT_LABELS[value]}</span>
                {reasoningEffort === value ? <CheckIcon style={{ fontSize: 12 }} /> : null}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  )
}

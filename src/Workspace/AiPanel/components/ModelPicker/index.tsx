import React from 'react'
import { CheckOutlined, EditOutlined } from '@ant-design/icons'
import { Divider, Popover, Select } from 'antd'
import type { AiContextWindow, AiModelConfig } from '../../../../types'
import './index.scss'

export type ModelRuntimeConfigPatch = Partial<
  Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>
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
 * - 下拉框本身只负责「选模型」，结构是标准 antd Select，不被配置 UI 改造。
 * - 每行 hover 出现「编辑」入口，点击后弹出**独立** Popover 配置 Context / Thinking。
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
  // 同步镜像 editingId，供 Select.onOpenChange 即时读取（避免闭包拿到旧值）
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
    <Select
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
            <Popover
              trigger="click"
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
                  // 阻止 Select 把这次点击当成「选中该项」
                  event.preventDefault()
                  event.stopPropagation()
                }}
                onClick={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                }}
              >
                <EditOutlined style={{ fontSize: 12 }} />
              </span>
            </Popover>
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
  const contextWindow = model.contextWindow ?? '200k'
  const thinkingEnabled = model.thinkingEnabled ?? model.thinkingOnly ?? false

  const contextItems: [AiContextWindow, string][] = [
    ['32k', '32K'],
    ['64k', '64K'],
    ['128k', '128K'],
    ['200k', '200K'],
    ['300k', '300K'],
    ['1m', '1M'],
  ]
  const thinkingItems: [boolean, string][] = [
    [false, '关闭'],
    [true, '开启'],
  ]

  return (
    <div className="model-picker-config">
      <div className="model-picker-config-group">
        <div className="model-picker-config-title">Context</div>
        {contextItems.map(([value, label]) => (
          <button
            key={value}
            type="button"
            className="model-picker-config-item"
            onClick={() => onPatch({ contextWindow: value })}
          >
            <span>{label}</span>
            {contextWindow === value ? <CheckOutlined style={{ fontSize: 12 }} /> : null}
          </button>
        ))}
      </div>
      <Divider style={{ margin: '6px 0' }} />
      <div className="model-picker-config-group">
        <div className="model-picker-config-title">Thinking</div>
        {thinkingItems.map(([value, label]) => (
          <button
            key={String(value)}
            type="button"
            className="model-picker-config-item"
            onClick={() => onPatch({ thinkingEnabled: value })}
          >
            <span>{label}</span>
            {thinkingEnabled === value ? <CheckOutlined style={{ fontSize: 12 }} /> : null}
          </button>
        ))}
      </div>
    </div>
  )
}

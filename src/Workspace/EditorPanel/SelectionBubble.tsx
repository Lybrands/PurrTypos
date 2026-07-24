import React from 'react'
import { Button, Tooltip } from '../../ui'
import { BulbOutlined } from '../../ui'

// ── 预设改写指令 ──────────────────────────────────────────────────
export const PRESETS: { id: string; label: string; prompt: string }[] = [
  {
    id: 'polish',
    label: '润色',
    prompt: '对下面这段话进行润色，让文字更流畅、生动，保持原意不变，不要拉长篇幅。',
  },
  {
    id: 'shorten',
    label: '精简',
    prompt: '把下面这段话改得更精简凝练，保留核心信息与语气，去除冗余与废话。',
  },
  {
    id: 'expand',
    label: '扩写',
    prompt: '对下面这段话进行合理扩写，增加画面感与细节描写，保持原风格与语气一致。',
  },
]

/** 选区上方浮出的快捷改写工具条；上方空间不足时翻转到下方。 */
export default function SelectionBubble({
  rect,
  onPreset,
  onCustom,
}: {
  rect: DOMRect
  onPreset: (p: (typeof PRESETS)[number]) => void
  onCustom: () => void
}) {
  const style = React.useMemo<React.CSSProperties>(() => {
    const GAP = 8
    const BUBBLE_HEIGHT = 36
    const top =
      rect.top - BUBBLE_HEIGHT - GAP > 8
        ? rect.top - BUBBLE_HEIGHT - GAP
        : rect.bottom + GAP
    const centerX = rect.left + rect.width / 2
    return {
      position: 'fixed',
      top,
      left: centerX,
      transform: 'translateX(-50%)',
    }
  }, [rect])

  return (
    <div
      className="inline-edit-toolbar"
      style={style}
      onMouseDown={(e) => {
        // 阻止 mousedown 抢走 editor 焦点，否则会导致选区闪失。
        e.preventDefault()
      }}
    >
      {PRESETS.map((p) => (
        <Tooltip key={p.id} title={p.prompt}>
          <Button
            type="text"
            size="small"
            className="inline-edit-toolbar-btn"
            onClick={() => onPreset(p)}
          >
            {p.label}
          </Button>
        </Tooltip>
      ))}
      <div className="inline-edit-toolbar-sep" />
      <Tooltip title="自定义指令改写">
        <Button
          type="text"
          size="small"
          className="inline-edit-toolbar-btn"
          icon={<BulbOutlined />}
          onClick={onCustom}
        >
          自定义
        </Button>
      </Tooltip>
    </div>
  )
}

import React from 'react'
import { Input, Select, Tooltip, App as AntdApp, Spin } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import { useWorkspace } from '../WorkspaceContext'
import type { BookStyle } from '../../types'

const { TextArea } = Input

const POV_PRESETS = [
  '第一人称',
  '第三人称限知',
  '第三人称全知',
  '第二人称',
  '多视角切换',
]

const TONE_PRESETS = [
  '冷峻沉郁',
  '幽默调侃',
  '轻松日常',
  '史诗厚重',
  '悬疑紧张',
  '热血激昂',
  '文艺细腻',
]

const PACE_PRESETS = [
  '紧凑快节奏',
  '徐缓铺陈',
  '章节大起大伏',
  '爽点密集',
  '舒缓+爆发交替',
]

interface StyleFormState {
  pov: string
  tone: string
  pace: string
  banned_rules: string
  reference_chapter_ids: string[]
  free_notes: string
}

const EMPTY_FORM: StyleFormState = {
  pov: '',
  tone: '',
  pace: '',
  banned_rules: '',
  reference_chapter_ids: [],
  free_notes: '',
}

function parseRefIds(raw: string | null | undefined): string[] {
  if (!raw) return []
  try {
    const v = JSON.parse(raw)
    return Array.isArray(v) ? v.map((x) => String(x)) : []
  } catch {
    return []
  }
}

function rowFromRemote(row: BookStyle | null): StyleFormState {
  if (!row) return EMPTY_FORM
  return {
    pov: row.pov || '',
    tone: row.tone || '',
    pace: row.pace || '',
    banned_rules: row.banned_rules || '',
    reference_chapter_ids: parseRefIds(row.reference_chapter_ids),
    free_notes: row.free_notes || '',
  }
}

function isAllEmpty(f: StyleFormState): boolean {
  return (
    !f.pov && !f.tone && !f.pace && !f.banned_rules && !f.free_notes &&
    f.reference_chapter_ids.length === 0
  )
}

export interface StyleFormStatus {
  /** 远端是否已存在风格基调 */
  hasRemote: boolean
  /** 是否正在保存 */
  saving: boolean
}

export interface StyleFormProps {
  /** 状态变化时回调（用于外层显示「已配置/保存中/未配置」标签） */
  onStatusChange?: (status: StyleFormStatus) => void
}

/**
 * 风格基调表单：自带「加载 / 防抖保存 / 远端状态」三件套，
 * 由外层（如 Modal）决定布局与是否展示。
 */
export default function StyleForm({ onStatusChange }: StyleFormProps) {
  const { message: appMessage } = AntdApp.useApp()
  const { bookId, writingChapters } = useWorkspace()
  const [form, setForm] = React.useState<StyleFormState>(EMPTY_FORM)
  const [loading, setLoading] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [hasRemote, setHasRemote] = React.useState(false)
  const saveTimerRef = React.useRef<number | null>(null)
  const lastSavedJsonRef = React.useRef<string>('')

  React.useEffect(() => {
    onStatusChange?.({ hasRemote, saving })
  }, [hasRemote, saving, onStatusChange])

  React.useEffect(() => {
    let cancelled = false
    if (bookId == null) {
      setForm(EMPTY_FORM)
      setHasRemote(false)
      return
    }
    setLoading(true)
    window.electronAPI
      .getBookStyle({ bookId })
      .then((res) => {
        if (cancelled) return
        if (res?.success && res.data) {
          const next = rowFromRemote(res.data)
          setForm(next)
          setHasRemote(true)
          lastSavedJsonRef.current = JSON.stringify(next)
        } else {
          setForm(EMPTY_FORM)
          setHasRemote(false)
          lastSavedJsonRef.current = JSON.stringify(EMPTY_FORM)
        }
      })
      .catch((e) => {
        if (!cancelled) appMessage.error(`加载风格基调失败：${(e as Error).message}`)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [bookId, appMessage])

  const scheduleSave = React.useCallback((next: StyleFormState) => {
    if (bookId == null) return
    if (saveTimerRef.current != null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(async () => {
      saveTimerRef.current = null
      const json = JSON.stringify(next)
      if (json === lastSavedJsonRef.current) return
      setSaving(true)
      try {
        const res = await window.electronAPI.saveBookStyle({
          bookId,
          pov: next.pov,
          tone: next.tone,
          pace: next.pace,
          banned_rules: next.banned_rules,
          reference_chapter_ids: JSON.stringify(next.reference_chapter_ids),
          free_notes: next.free_notes,
        })
        if (res?.success) {
          lastSavedJsonRef.current = json
          setHasRemote(!isAllEmpty(next))
        } else {
          appMessage.error('保存风格基调失败')
        }
      } catch (e) {
        appMessage.error(`保存风格基调失败：${(e as Error).message}`)
      } finally {
        setSaving(false)
      }
    }, 600)
  }, [bookId, appMessage])

  const update = React.useCallback(<K extends keyof StyleFormState>(key: K, value: StyleFormState[K]) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value }
      scheduleSave(next)
      return next
    })
  }, [scheduleSave])

  React.useEffect(() => () => {
    if (saveTimerRef.current != null) {
      window.clearTimeout(saveTimerRef.current)
    }
  }, [])

  const chapterOptions = React.useMemo(
    () => writingChapters.map((c) => ({
      label: c.title || '未命名章节',
      value: String(c.id),
    })),
    [writingChapters],
  )

  if (bookId == null) {
    return (
      <div className="notebook-placeholder">
        <p className="notebook-placeholder-desc">请先打开一本书籍</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="notebook-placeholder">
        <Spin size="small" />
      </div>
    )
  }

  return (
    <div className="style-form">
      <div className="style-form-row">
        <label className="style-form-label">视角</label>
        <Select
          size="small"
          allowClear
          showSearch
          placeholder="例如：第三人称限知"
          value={form.pov || undefined}
          onChange={(v) => update('pov', v ?? '')}
          options={POV_PRESETS.map((p) => ({ label: p, value: p }))}
          mode="tags"
          maxCount={1}
          style={{ width: '100%' }}
        />
      </div>

      <div className="style-form-row">
        <label className="style-form-label">语调</label>
        <Select
          size="small"
          allowClear
          showSearch
          placeholder="例如：冷峻沉郁"
          value={form.tone || undefined}
          onChange={(v) => update('tone', v ?? '')}
          options={TONE_PRESETS.map((p) => ({ label: p, value: p }))}
          mode="tags"
          maxCount={1}
          style={{ width: '100%' }}
        />
      </div>

      <div className="style-form-row">
        <label className="style-form-label">节奏</label>
        <Select
          size="small"
          allowClear
          showSearch
          placeholder="例如：紧凑快节奏"
          value={form.pace || undefined}
          onChange={(v) => update('pace', v ?? '')}
          options={PACE_PRESETS.map((p) => ({ label: p, value: p }))}
          mode="tags"
          maxCount={1}
          style={{ width: '100%' }}
        />
      </div>

      <div className="style-form-row">
        <label className="style-form-label">
          禁忌
          <span className="style-form-hint">每行一条，AI 严禁违反</span>
        </label>
        <TextArea
          size="small"
          rows={3}
          value={form.banned_rules}
          onChange={(e) => update('banned_rules', e.target.value)}
          placeholder={'例如：\n禁用网络流行语\n禁止上帝视角点评\n人物不得 OOC'}
        />
      </div>

      <div className="style-form-row">
        <label className="style-form-label">
          参考章节
          <span className="style-form-hint">AI 会以这些章节的语感为模板</span>
        </label>
        <Select
          size="small"
          mode="multiple"
          allowClear
          placeholder="选择体现你想要风格的代表章节"
          value={form.reference_chapter_ids}
          onChange={(v) => update('reference_chapter_ids', v as string[])}
          options={chapterOptions}
          maxTagCount="responsive"
          style={{ width: '100%' }}
          filterOption={(input, opt) =>
            String(opt?.label ?? '').toLowerCase().includes(input.toLowerCase())
          }
        />
      </div>

      <div className="style-form-row">
        <label className="style-form-label">作者备注</label>
        <TextArea
          size="small"
          rows={2}
          value={form.free_notes}
          onChange={(e) => update('free_notes', e.target.value)}
          placeholder="给 AI 的自由说明，例如：本书核心冲突在于…"
        />
      </div>

      <Tooltip title="所有非空字段都会作为「强制规则」自动拼入写作专家的 system prompt（无需勾选）">
        <div className="style-form-footer">
          <ThunderboltOutlined /> 已自动强制注入 AI 写作专家
        </div>
      </Tooltip>
    </div>
  )
}

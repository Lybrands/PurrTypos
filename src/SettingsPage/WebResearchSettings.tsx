import React from 'react'
import { PurrSwitch } from '@/purr-components'
import { services } from '../services'
import { useAppFeedback } from '../hooks/useAppFeedback'

export default function WebResearchSettings() {
  const { message } = useAppFeedback()
  const [enabled, setEnabled] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  const [loadFailed, setLoadFailed] = React.useState(false)

  React.useEffect(() => {
    let active = true
    void services.settings.getSettings().then((result) => {
      if (!result.success || !result.data) throw new Error('settings unavailable')
      if (active) setEnabled(result.data.web_search_enabled === true)
    }).catch(() => {
      if (active) setLoadFailed(true)
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [])

  const save = async (value: boolean) => {
    setSaving(true)
    try {
      const result = await services.settings.setSettings({ web_search_enabled: value })
      if (!result.success) throw new Error('save failed')
      setEnabled(value)
    } catch {
      message.error('联网搜索设置未保存，请检查连接后重试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="settings-preference-row">
      <div className="settings-preference-copy">
        <strong>允许 Agent 联网查资料</strong>
        <p>允许 Agent 搜索和读取公开网页资料，可能增加模型用量和响应时间。</p>
        {loadFailed && <p role="alert">联网搜索设置读取失败，请重新打开设置页。</p>}
      </div>
      <PurrSwitch
        checked={enabled}
        disabled={loading || saving || loadFailed}
        onChange={(value) => { void save(value) }}
        aria-label="允许 Agent 联网查资料"
      />
    </div>
  )
}

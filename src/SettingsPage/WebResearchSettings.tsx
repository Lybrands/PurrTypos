import React from 'react'
import { PurrButton, PurrInput, PurrSelect, PurrSwitch } from '@/purr-components'
import { services } from '../services'
import { useAppFeedback } from '../hooks/useAppFeedback'
import type { GeneralSettings } from '../types'

export default function WebResearchSettings() {
  const { message } = useAppFeedback()
  const [enabled, setEnabled] = React.useState(false)
  const [provider, setProvider] = React.useState<'deepseek' | 'free'>('deepseek')
  const [keyConfigured, setKeyConfigured] = React.useState(false)
  const [keyDraft, setKeyDraft] = React.useState('')
  const [loading, setLoading] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  const [loadFailed, setLoadFailed] = React.useState(false)
  const pending = React.useRef(false)

  React.useEffect(() => {
    let active = true
    void services.settings.getSettings().then((result) => {
      if (!result.success || !result.data) throw new Error('settings unavailable')
      if (active) {
        setEnabled(result.data.web_search_enabled === true)
        setProvider(result.data.web_search_provider === 'free' ? 'free' : 'deepseek')
        setKeyConfigured(result.data.web_search_deepseek_api_key_configured === true)
      }
    }).catch(() => {
      if (active) setLoadFailed(true)
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [])

  const save = async (patch: Partial<GeneralSettings>, apply: () => void) => {
    if (pending.current) return
    pending.current = true
    setSaving(true)
    try {
      const result = await services.settings.setSettings(patch)
      if (!result.success) throw new Error('save failed')
      apply()
    } catch {
      message.error('联网搜索设置未保存，请检查连接后重试')
    } finally {
      pending.current = false
      setSaving(false)
    }
  }
  const saveKey = (value: string) => {
    void save({ web_search_deepseek_api_key: value }, () => {
      setKeyConfigured(Boolean(value))
      setKeyDraft('')
    })
  }
  const disabled = loading || saving || loadFailed

  return (
    <div className="settings-web-research">
      <div className="settings-preference-row">
        <div className="settings-preference-copy">
          <strong>允许 Agent 联网查资料</strong>
          <p>搜索和读取公开网页资料，可能增加模型用量和响应时间。</p>
          {loadFailed && <p role="alert">联网搜索设置读取失败，请重新打开设置页。</p>}
        </div>
        <PurrSwitch
          checked={enabled}
          disabled={disabled}
          onChange={(value) => { void save({ web_search_enabled: value }, () => setEnabled(value)) }}
          aria-label="允许 Agent 联网查资料"
        />
      </div>
      {enabled && !loading && !loadFailed && (
        <div className="settings-web-research-options">
          <div className="settings-field">
            <span className="settings-field-label">搜索服务</span>
            <PurrSelect
              aria-label="搜索服务"
              value={provider}
              disabled={disabled}
              options={[{ value: 'deepseek', label: 'DeepSeek 优先' }, { value: 'free', label: '免费搜索' }]}
              onChange={(value: 'deepseek' | 'free') => {
                void save({ web_search_provider: value }, () => { setProvider(value); setKeyDraft('') })
              }}
            />
          </div>
          {provider === 'deepseek' && (
            <div className="settings-field">
              <label className="settings-field-label" htmlFor="web-search-deepseek-key">DeepSeek API Key</label>
              <PurrInput.Password
                id="web-search-deepseek-key"
                aria-label="DeepSeek 搜索 API Key"
                autoComplete="off"
                spellCheck={false}
                maxLength={512}
                disabled={disabled}
                value={keyDraft}
                placeholder={keyConfigured ? '已保存，输入新 Key 可替换' : '输入 DeepSeek API Key'}
                onChange={(event) => setKeyDraft(event.target.value)}
                onPressEnter={() => { if (keyDraft.trim() && !disabled) saveKey(keyDraft.trim()) }}
              />
              <div className="settings-web-research-key-actions">
                <PurrButton size="small" disabled={disabled || !keyDraft.trim()} onClick={() => saveKey(keyDraft.trim())}>
                  {keyConfigured ? '更新 API Key' : '保存 API Key'}
                </PurrButton>
                {keyConfigured && <PurrButton size="small" disabled={disabled} onClick={() => saveKey('')}>删除 API Key</PurrButton>}
              </div>
              <p className="settings-web-research-hint">DeepSeek 搜索按服务商规则计费。</p>
            </div>
          )}
          <p className="settings-web-research-hint" role="status">
            {provider === 'deepseek' && keyConfigured
              ? '当前使用 DeepSeek 搜索。'
              : provider === 'deepseek'
                ? '未配置 API Key，当前使用免费搜索。'
                : '当前使用免费搜索。'}
          </p>
        </div>
      )}
    </div>
  )
}

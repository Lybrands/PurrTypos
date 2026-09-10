import React from 'react'
import { useNavigate } from 'react-router-dom'
import { PurrButton, usePurrToast } from '@/purr-components'
import { writingTechniques } from '../services/writingTechniques'

export default function SourceTechniqueResults({ analysisId, canAdd }: { analysisId: string; canAdd: boolean }) {
  const navigate = useNavigate()
  const toast = usePurrToast()
  const [items, setItems] = React.useState<Array<{id: string; status: 'active' | 'archived'; name: string}>>([])
  const [loading, setLoading] = React.useState(Boolean(analysisId))
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState('')
  const [retry, setRetry] = React.useState(0)
  React.useEffect(() => {
    if (!analysisId) return
    let current = true
    setLoading(true)
    void writingTechniques.analysisLibrary(analysisId).then(result => {
      if (!result.success || !result.data) throw new Error(result.error || '技法库状态读取失败')
      if (current) { setItems(result.data); setError('') }
    }).catch(error => { if (current) setError((error as Error).message) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [analysisId, retry])
  const add = async () => {
    setSaving(true)
    try {
      const result = await writingTechniques.saveAnalysis(analysisId, `analysis-library:${analysisId}`)
      if (!result.success || !result.data?.techniqueId) throw new Error(result.error || '加入技法库失败')
      setItems([{id: result.data.techniqueId, status: 'active', name: ''}])
      toast.success('已加入技法库')
    } catch (error) { toast.error((error as Error).message) }
    finally { setSaving(false) }
  }
  const item = items.find(item => item.status === 'active') || items[0]
  return <div className="novel-technique-tab-toolbar" aria-label="技法库状态">
    <span role="status">{!analysisId ? '保存分析后可加入技法库' : loading ? '正在读取技法库状态…' : error || (item ? item.status === 'archived' ? '已在技法库 · 已归档' : '已在技法库' : '可将已保存的版本加入技法库')}</span>
    {analysisId && !loading && (error ? <PurrButton size="small" onClick={() => setRetry(value => value + 1)}>重试</PurrButton>
      : item ? <PurrButton size="small" onClick={() => navigate('/writing-methods', {state: {locateTechniqueId: item.id}})}>在技法库中查看</PurrButton>
      : <PurrButton size="small" disabled={!canAdd} loading={saving} onClick={() => void add()}>加入技法库</PurrButton>)}
  </div>
}

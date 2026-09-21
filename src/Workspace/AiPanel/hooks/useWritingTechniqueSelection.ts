import React from 'react'
import { services } from '@/services'
import { usePurrToast } from '@/purr-components'
import type { EntityId } from '../../../types'
import type { TechniqueRef, WritingTechniqueChoice, WritingTechniqueSelection } from '../../../services/writingTechniques'

export function useWritingTechniqueSelection(bookId: EntityId | null | undefined, sessionId: number | null) {
  const toast = usePurrToast()
  const [selection, setSelection] = React.useState<WritingTechniqueSelection>({ mode: 'manual', refs: [] })
  const [choices, setChoices] = React.useState<WritingTechniqueChoice[]>([])
  const [ready, setReady] = React.useState(false)
  const sessionRef = React.useRef(sessionId)
  const persistent = React.useRef<TechniqueRef[]>([])
  const continuation = React.useRef(false)
  const selectionRef = React.useRef(selection)
  selectionRef.current = selection
  sessionRef.current = sessionId
  React.useEffect(() => {
    let current = true
    setReady(false); setSelection({ mode: 'manual', refs: [] }); setChoices([]); persistent.current = []
    if (bookId == null) return
    void Promise.all([
      services.writingTechniques.getMode(sessionId == null ? 'book' : 'session', String(sessionId ?? bookId)),
      services.writingTechniques.getSelection(sessionId == null ? 'book' : 'session', String(sessionId ?? bookId)),
      services.writingTechniques.getSelection('book', String(bookId)),
      services.writingTechniques.list('technique'), services.writingTechniques.list('scheme'),
    ]).then(([mode, saved, defaults, techniques, schemes]) => {
      if (!current) return
      if (!mode.success || !saved.success || !defaults.success || !techniques.success || !schemes.success) { toast.error('读取写作技法配置失败'); return }
      persistent.current = saved.data || []
      continuation.current = !!defaults.data?.length || !!saved.data?.length
      setSelection({ mode: mode.data || 'manual', refs: persistent.current })
      const objects = [...(techniques.data || []), ...(schemes.data || [])]
      const pinned = [...persistent.current, ...(defaults.data || [])]
      const values = objects.filter(item => item.publishedHead).map(item => ({
        ref: pinned.find(ref => ref.kind === item.kind && ref.id === item.id) || { kind: item.kind, id: item.id, versionId: item.publishedHead! },
        name: item.metadata?.name || '未命名技法', description: item.metadata?.description || '',
      }))
      for (const ref of pinned) if (!values.some(item => item.ref.id === ref.id)) values.push({ref, name: '来源技法（当前不可用）', description: '取消选择或恢复资源后继续'})
      setChoices(values); setReady(sessionId != null)
    }).catch(() => { if (current) toast.error('读取写作技法配置失败') })
    return () => { current = false }
  }, [bookId, sessionId, toast])
  const setMode = React.useCallback(async (mode: 'manual' | 'auto') => {
    if (sessionId == null) return
    const result = await services.writingTechniques.setMode('session', String(sessionId), mode)
    if (sessionRef.current !== sessionId) return
    if (result.success) setSelection(previous => ({ ...previous, mode }))
    else toast.error(result.error || '保存模式失败')
  }, [sessionId, toast])
  const toggle = React.useCallback(async (id: string) => {
    const choice = choices.find(item => item.ref.id === id)
    if (!choice || sessionId == null || !ready) return
    const previous = selectionRef.current
    const refs = previous.refs.some(ref => ref.id === id) ? previous.refs.filter(ref => ref.id !== id) : [...previous.refs, choice.ref]
    if (continuation.current && !choice.uploaded) {
      setReady(false)
      const saved = refs.filter(ref => !choices.some(item => item.ref.id === ref.id && item.uploaded))
      const result = await services.writingTechniques.setSelection('session', String(sessionId), saved)
      if (sessionRef.current !== sessionId) return
      setReady(true)
      if (!result.success) { toast.error(result.error || '保存选择失败'); return }
      persistent.current = saved
    }
    setSelection({...previous, refs})
  }, [choices, sessionId, ready, toast])
  const accepted = React.useCallback((submitted: WritingTechniqueSelection) => {
    if (sessionRef.current !== sessionId) return
    setSelection(previous => JSON.stringify(previous.refs) === JSON.stringify(submitted.refs) ? { ...previous, refs: [...persistent.current] } : previous)
  }, [sessionId])
  const addUpload = React.useCallback((choice: WritingTechniqueChoice) => {
    if (sessionRef.current !== sessionId) return
    setChoices(previous => [...previous.filter(item => item.ref.id !== choice.ref.id), {...choice, uploaded: true}])
    setSelection(previous => ({ ...previous, refs: [...previous.refs.filter(ref => ref.id !== choice.ref.id), choice.ref] }))
  }, [sessionId])
  return { selection, choices, ready, setMode, toggle, accepted, addUpload }
}

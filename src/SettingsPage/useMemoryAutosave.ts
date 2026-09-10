import React from 'react'
import type { MemoryEmbeddingConfig } from '../types'

export type MemoryConfigurationPatch = {
  modelId?: string
  embedding?: MemoryEmbeddingConfig | null
}

const emptyEmbedding: MemoryEmbeddingConfig = {
  apiProvider: 'openai', model: '', apiKey: '', baseUrl: '', dimensions: 0,
}

export function useMemoryAutosave(
  modelId: string,
  embedding: MemoryEmbeddingConfig | null,
  save: (patch: MemoryConfigurationPatch) => Promise<void>,
) {
  const [model, setModel] = React.useState(modelId)
  const [enabled, setEnabled] = React.useState(Boolean(embedding))
  const [draft, setDraft] = React.useState(embedding ?? emptyEmbedding)
  const [status, setStatus] = React.useState('')
  const edited = React.useRef(false)
  const revision = React.useRef(0)
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = React.useRef<MemoryConfigurationPatch | null>(null)
  const saveRef = React.useRef(save)
  saveRef.current = save

  React.useEffect(() => {
    if (edited.current) return
    setModel(modelId)
    setEnabled(Boolean(embedding))
    setDraft(embedding ?? emptyEmbedding)
  }, [modelId, embedding])

  const persist = React.useCallback((patch: MemoryConfigurationPatch) => {
    const version = ++revision.current
    const label = patch.modelId !== undefined ? '提炼与评审模型' : 'Embedding 配置'
    setStatus(`正在保存${label}…`)
    void saveRef.current(patch).then(() => {
      if (revision.current === version) setStatus(`${label}已自动保存`)
    }).catch(() => {
      if (revision.current === version) setStatus('保存失败，请检查连接后重新修改。')
    })
  }, [])

  const flush = React.useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    const patch = pending.current
    pending.current = null
    if (patch) persist(patch)
  }, [persist])

  React.useEffect(() => () => { flush() }, [flush])

  const changeEmbedding = (next: MemoryEmbeddingConfig) => {
    edited.current = true
    setDraft(next)
    if (timer.current) clearTimeout(timer.current)
    pending.current = null
    ++revision.current
    const normalized = { ...next, model: next.model.trim(), apiKey: next.apiKey.trim(), baseUrl: next.baseUrl.trim() }
    if (!normalized.model || !normalized.apiKey || !normalized.baseUrl
      || !Number.isInteger(normalized.dimensions) || normalized.dimensions < 1 || normalized.dimensions > 65536) {
      setStatus('Embedding 配置未完整填写，尚未保存。')
      return
    }
    setStatus('等待保存…')
    pending.current = { embedding: normalized }
    timer.current = setTimeout(flush, 400)
  }

  return {
    model, enabled, draft, status, flush,
    changeModel(value: string) {
      edited.current = true
      setModel(value)
      flush()
      persist({ modelId: value })
    },
    changeEmbedding,
    toggle(value: boolean) {
      edited.current = true
      setEnabled(value)
      if (value) changeEmbedding(draft)
      else {
        if (timer.current) clearTimeout(timer.current)
        pending.current = null
        persist({ embedding: null })
      }
    },
  }
}

import React from 'react'
import { useNavigate } from 'react-router-dom'
import { services } from '@/services'
import { PurrButton, PurrSpin, usePurrToast } from '@/purr-components'
import type {
  BookWritingMethodBinding,
  EntityId,
  WritingMethod,
  WritingScheme,
} from '../types'

export default function WritingMethodBindingsPanel({ bookId }: { bookId: EntityId | null }) {
  const navigate = useNavigate()
  const appMessage = usePurrToast()
  const [bindings, setBindings] = React.useState<BookWritingMethodBinding[]>([])
  const [methods, setMethods] = React.useState<WritingMethod[]>([])
  const [schemes, setSchemes] = React.useState<WritingScheme[]>([])
  const [selection, setSelection] = React.useState('')
  const [loading, setLoading] = React.useState(true)
  const notifyChanged = React.useCallback(() => {
    if (bookId == null) return
    window.dispatchEvent(new CustomEvent('writing-method-bindings-changed', {
      detail: { bookId },
    }))
  }, [bookId])

  const reload = React.useCallback(async () => {
    if (bookId == null) return
    setLoading(true)
    try {
      const [bindingResult, methodResult, schemeResult] = await Promise.all([
        services.writingMethods.listBookBindings({ bookId }),
        services.writingMethods.listMethods(),
        services.writingMethods.listSchemes(),
      ])
      if (!bindingResult.success || !methodResult.success || !schemeResult.success) {
        throw new Error('加载作品写作方法失败')
      }
      setBindings(bindingResult.data ?? [])
      setMethods(methodResult.data ?? [])
      setSchemes(schemeResult.data ?? [])
    } catch (error) {
      appMessage.error((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [appMessage, bookId])

  React.useEffect(() => { void reload() }, [reload])

  const add = async () => {
    if (bookId == null || !selection) return
    const [bindingType, revisionId] = selection.split(':', 2) as ['method' | 'scheme', string]
    const result = await services.writingMethods.bindBook({ bookId, bindingType, revisionId })
    if (!result.success) return appMessage.error(result.error || '绑定失败')
    setSelection('')
    await reload()
    notifyChanged()
  }

  const remove = async (bindingId: string) => {
    if (bookId == null) return
    const result = await services.writingMethods.unbindBook({ bookId, bindingId })
    if (!result.success) return appMessage.error(result.error || '解除绑定失败')
    await reload()
    notifyChanged()
  }

  const move = async (index: number, offset: number) => {
    if (bookId == null) return
    const target = index + offset
    if (target < 0 || target >= bindings.length) return
    const ordered = bindings.map((item) => item.id)
    ;[ordered[index], ordered[target]] = [ordered[target], ordered[index]]
    const result = await services.writingMethods.reorderBookBindings({ bookId, bindingIds: ordered })
    if (!result.success) return appMessage.error(result.error || '排序失败')
    setBindings(result.data ?? [])
    notifyChanged()
  }

  const upgrade = async (binding: BookWritingMethodBinding, revisionId: string) => {
    if (bookId == null) return
    const result = await services.writingMethods.upgradeBookBinding({
      bookId, bindingId: binding.id, revisionId,
    })
    if (!result.success) return appMessage.error(result.error || '升级失败')
    await reload()
    notifyChanged()
  }

  if (bookId == null) return <div className="notebook-placeholder">请先打开一本作品</div>
  if (loading) return <div className="workspace-utility-loading"><PurrSpin size="small" /></div>

  const available = [
    ...methods.filter((item) => item.current_published_revision_id).map((item) => ({
      value: `method:${item.current_published_revision_id}`,
      label: `方法 · ${item.name}`,
    })),
    ...schemes.filter((item) => item.current_published_revision_id).map((item) => ({
      value: `scheme:${item.current_published_revision_id}`,
      label: `方案 · ${item.name}`,
    })),
  ]

  return <div className="writing-method-bindings-panel">
    <div className="writing-method-bindings-header">
      <div><h2>写作方法</h2><p>作品固定引用准确的已发布版本，不会自动升级。</p></div>
      <PurrButton onClick={() => navigate('/writing-methods')}>打开方法库</PurrButton>
    </div>
    <div className="writing-method-bindings-add">
      <select value={selection} onChange={(event) => setSelection(event.target.value)}>
        <option value="">选择已发布的方法或完整方案</option>
        {available.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
      </select>
      <PurrButton type="primary" disabled={!selection} onClick={() => void add()}>绑定</PurrButton>
    </div>
    <div className="writing-method-binding-list">
      {bindings.length === 0 ? <div className="notebook-placeholder">尚未绑定写作方法</div> : bindings.map((binding, index) => {
        const revision = binding.revision
        const owner = binding.binding_type === 'method'
          ? methods.find((item) => item.id === ('method_id' in revision ? revision.method_id : ''))
          : schemes.find((item) => item.id === ('scheme_id' in revision ? revision.scheme_id : ''))
        const nextRevisionId = owner?.current_published_revision_id
        const boundRevisionId = binding.method_revision_id ?? binding.scheme_revision_id
        return <article key={binding.id}>
          <div className="writing-method-binding-order">
            <button disabled={index === 0} onClick={() => void move(index, -1)}>↑</button>
            <button disabled={index === bindings.length - 1} onClick={() => void move(index, 1)}>↓</button>
          </div>
          <div className="writing-method-binding-copy">
            <strong>{binding.binding_type === 'scheme' ? '写作方案' : '写作方法'} · {revision.name}</strong>
            <span>v{revision.version_no} · {boundRevisionId}</span>
            {'members' in revision ? <small>{revision.members.map((item) => item.name).join(' → ')}</small> : null}
          </div>
          <div className="writing-method-binding-actions">
            {nextRevisionId && nextRevisionId !== boundRevisionId ? (
              <PurrButton size="small" onClick={() => void upgrade(binding, nextRevisionId)}>手动升级</PurrButton>
            ) : null}
            <PurrButton size="small" type="text" onClick={() => void remove(binding.id)}>解除</PurrButton>
          </div>
        </article>
      })}
    </div>
  </div>
}

import React from 'react'
import { PurrButton, PurrModal, usePurrToast } from '@/purr-components'
import { services } from '@/services'
import { techniqueOperationId, type TechniqueDeletionPreview, type TechniqueObject } from '../services/writingTechniques'
import { requireTechniqueData } from './operations'

export default function DeleteLibraryObject({ object, disabled, onDeleted }: {object: TechniqueObject; disabled?: boolean; onDeleted: () => Promise<void>}) {
  const toast = usePurrToast()
  const [preview, setPreview] = React.useState<TechniqueDeletionPreview | null>(null)
  const [busy, setBusy] = React.useState(false)
  const operation = React.useRef('')
  const label = object.kind === 'technique' ? '技法' : '方案'
  return <>
    <PurrButton danger disabled={disabled || busy} onClick={async () => {
      setBusy(true)
      try { setPreview(requireTechniqueData(await services.writingTechniques.deletionPreview(object))); operation.current = techniqueOperationId() }
      catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
    }}>永久删除</PurrButton>
    <PurrModal open={Boolean(preview)} title={`永久删除写作${label}`} onCancel={() => { if (!busy) setPreview(null) }} okText="永久删除" confirmLoading={busy} okButtonProps={{danger: true, disabled: !preview?.canDelete}} onOk={async () => {
      if (!preview?.canDelete) return
      setBusy(true)
      try {
        requireTechniqueData(await services.writingTechniques.deleteObject(object, preview.revisionToken, operation.current))
        setPreview(null); toast.success(`${label}已删除`); await onDeleted()
      } catch (error) { toast.error((error as Error).message) } finally { setBusy(false) }
    }}>
      <p>将永久删除“{object.metadata?.name || `此${label}`}”及其 {preview?.draftCount ?? 0} 份草稿、{preview?.versionCount ?? 0} 个历史版本。此操作无法撤销。</p>
      <p>历史对话中的引用记录会保留，但将无法再读取已删除的文件。</p>
      {object.kind === 'scheme' && <p>成员技法仍保留在技法库中。</p>}
      {Boolean(preview?.references.length) && <><p role="alert">以下方案仍引用此技法，暂时无法删除。请先修改相关草稿；封存稿或历史版本的引用需要删除所属方案后解除。仅归档方案不会解除引用。</p><ul>{preview?.references.map(ref => <li key={ref.id}>{ref.name}（{ref.sources.join('、')}）</li>)}</ul></>}
    </PurrModal>
  </>
}

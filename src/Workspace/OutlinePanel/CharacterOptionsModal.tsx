import { services } from '@/services'
import React from 'react'
import { DeleteIcon, PlusIcon, EditIcon, CheckIcon, CloseIcon } from '@/purr-components'
import { PurrButton, PurrInput, PurrModal, PurrTooltip } from '@/purr-components'
import type { CharacterOption } from '../../types'
import { useAppFeedback } from '../../hooks/useAppFeedback'

interface Props {
  open: boolean
  onClose: () => void
  onOptionsChange?: () => void
}

/** 人物档案 Markdown 化后，结构化选项只剩「标签」（性格已并入档案正文） */
type Category = 'tag'

function OptionList({
  category,
  onOptionsChange,
}: {
  category: Category
  onOptionsChange?: () => void
}) {
  const { message } = useAppFeedback()
  const [options, setOptions] = React.useState<CharacterOption[]>([])
  const [addingValue, setAddingValue] = React.useState('')
  const [isAdding, setIsAdding] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [editingValue, setEditingValue] = React.useState('')
  const addInputRef = React.useRef<any>(null)
  const editInputRef = React.useRef<any>(null)

  const load = React.useCallback(async () => {
    const res = await services.characters.getCharacterOptions({ category })
    if (res.success && res.data) setOptions(res.data)
  }, [category])

  React.useEffect(() => { load() }, [load])

  React.useEffect(() => {
    if (isAdding) setTimeout(() => addInputRef.current?.focus(), 50)
  }, [isAdding])

  React.useEffect(() => {
    if (editingId != null) setTimeout(() => editInputRef.current?.focus(), 50)
  }, [editingId])

  const handleAdd = React.useCallback(async () => {
    const val = addingValue.trim()
    if (!val) { setIsAdding(false); return }
    const res = await services.characters.addCharacterOption({ category, value: val })
    if (res.success) {
      setAddingValue('')
      setIsAdding(false)
      load()
      onOptionsChange?.()
    } else {
      message.error(res.error || '添加失败')
    }
  }, [category, addingValue, load, onOptionsChange, message])

  const handleStartEdit = React.useCallback((opt: CharacterOption) => {
    setEditingId(opt.id)
    setEditingValue(opt.value)
  }, [])

  const handleConfirmEdit = React.useCallback(async () => {
    if (editingId == null) return
    const val = editingValue.trim()
    if (!val) { setEditingId(null); return }
    const res = await services.characters.updateCharacterOption({ id: editingId, value: val })
    if (res.success) {
      setEditingId(null)
      load()
      onOptionsChange?.()
    } else {
      message.error(res.error || '保存失败')
    }
  }, [editingId, editingValue, load, onOptionsChange, message])

  const handleDelete = React.useCallback(async (id: number) => {
    const res = await services.characters.deleteCharacterOption({ id })
    if (res.success) {
      load()
      onOptionsChange?.()
    } else {
      message.error(res.error || '删除失败')
    }
  }, [load, onOptionsChange, message])

  return (
    <div className="char-options-list">
      {options.map((opt) => (
        <div key={opt.id} className="char-option-item">
          {editingId === opt.id ? (
            <PurrInput
              ref={editInputRef}
              size="small"
              value={editingValue}
              onChange={(e) => setEditingValue(e.target.value)}
              onPressEnter={handleConfirmEdit}
              onBlur={handleConfirmEdit}
              style={{ flex: 1 }}
            />
          ) : (
            <span className="char-option-value">{opt.value}</span>
          )}
          <div className="char-option-actions">
            {editingId === opt.id ? (
              <>
                <PurrTooltip title="确认">
                  <PurrButton type="text" size="small" icon={<CheckIcon />} onClick={handleConfirmEdit} />
                </PurrTooltip>
                <PurrTooltip title="取消">
                  <PurrButton type="text" size="small" icon={<CloseIcon />} onClick={() => setEditingId(null)} />
                </PurrTooltip>
              </>
            ) : (
              <>
                <PurrTooltip title="编辑">
                  <PurrButton type="text" size="small" icon={<EditIcon />} onClick={() => handleStartEdit(opt)} />
                </PurrTooltip>
                <PurrTooltip title="删除">
                  <PurrButton type="text" size="small" icon={<DeleteIcon />} onClick={() => handleDelete(opt.id)} />
                </PurrTooltip>
              </>
            )}
          </div>
        </div>
      ))}

      {isAdding ? (
        <div className="char-option-item char-option-add-row">
          <PurrInput
            ref={addInputRef}
            size="small"
            placeholder="输入新值，回车确认"
            value={addingValue}
            onChange={(e) => setAddingValue(e.target.value)}
            onPressEnter={handleAdd}
            onBlur={handleAdd}
            style={{ flex: 1 }}
          />
          <PurrTooltip title="取消">
            <PurrButton type="text" size="small" icon={<CloseIcon />} onClick={() => { setIsAdding(false); setAddingValue('') }} />
          </PurrTooltip>
        </div>
      ) : (
        <PurrTooltip title="添加选项">
          <PurrButton
            type="dashed"
            size="small"
            icon={<PlusIcon />}
            onClick={() => setIsAdding(true)}
            block
            style={{ marginTop: 8 }}
          >
            添加
          </PurrButton>
        </PurrTooltip>
      )}
    </div>
  )
}

export default function CharacterOptionsModal({ open, onClose, onOptionsChange }: Props) {
  return (
    <PurrModal
      title="标签选项"
      open={open}
      onCancel={onClose}
      footer={null}
      width={400}
      destroyOnHidden
      styles={{ body: { overflow: 'hidden' } }}
    >
      <div className="char-options-tab-content">
        <OptionList category="tag" onOptionsChange={onOptionsChange} />
      </div>
    </PurrModal>
  )
}

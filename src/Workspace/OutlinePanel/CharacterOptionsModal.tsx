import React from 'react'
import { DeleteOutlined, PlusOutlined, EditOutlined, CheckOutlined, CloseOutlined } from '../../ui'
import { Button, Input, Modal, Tooltip } from '../../ui'
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
    const res = await window.electronAPI.getCharacterOptions({ category })
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
    const res = await window.electronAPI.addCharacterOption({ category, value: val })
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
    const res = await window.electronAPI.updateCharacterOption({ id: editingId, value: val })
    if (res.success) {
      setEditingId(null)
      load()
      onOptionsChange?.()
    } else {
      message.error(res.error || '保存失败')
    }
  }, [editingId, editingValue, load, onOptionsChange, message])

  const handleDelete = React.useCallback(async (id: number) => {
    const res = await window.electronAPI.deleteCharacterOption({ id })
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
            <Input
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
                <Tooltip title="确认">
                  <Button type="text" size="small" icon={<CheckOutlined />} onClick={handleConfirmEdit} />
                </Tooltip>
                <Tooltip title="取消">
                  <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => setEditingId(null)} />
                </Tooltip>
              </>
            ) : (
              <>
                <Tooltip title="编辑">
                  <Button type="text" size="small" icon={<EditOutlined />} onClick={() => handleStartEdit(opt)} />
                </Tooltip>
                <Tooltip title="删除">
                  <Button type="text" size="small" icon={<DeleteOutlined />} onClick={() => handleDelete(opt.id)} />
                </Tooltip>
              </>
            )}
          </div>
        </div>
      ))}

      {isAdding ? (
        <div className="char-option-item char-option-add-row">
          <Input
            ref={addInputRef}
            size="small"
            placeholder="输入新值，回车确认"
            value={addingValue}
            onChange={(e) => setAddingValue(e.target.value)}
            onPressEnter={handleAdd}
            onBlur={handleAdd}
            style={{ flex: 1 }}
          />
          <Tooltip title="取消">
            <Button type="text" size="small" icon={<CloseOutlined />} onClick={() => { setIsAdding(false); setAddingValue('') }} />
          </Tooltip>
        </div>
      ) : (
        <Tooltip title="添加选项">
          <Button
            type="dashed"
            size="small"
            icon={<PlusOutlined />}
            onClick={() => setIsAdding(true)}
            block
            style={{ marginTop: 8 }}
          >
            添加
          </Button>
        </Tooltip>
      )}
    </div>
  )
}

export default function CharacterOptionsModal({ open, onClose, onOptionsChange }: Props) {
  return (
    <Modal
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
    </Modal>
  )
}

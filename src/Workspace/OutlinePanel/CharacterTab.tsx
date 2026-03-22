import React from 'react'
import { PlusOutlined, UserOutlined, DeleteOutlined, EditOutlined, SettingOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { Button, Modal, Form, Input, InputNumber, Radio, Select, Tag, Tooltip, Empty } from 'antd'
import type { Character, CharacterOption } from '../../types'
import { useAntdApp } from '../../hooks/useAntdApp'
import { getBookCharacters } from '../utils'
import CharacterOptionsModal from './CharacterOptionsModal'
import './CharacterTab.scss'

const { TextArea } = Input

interface CharacterFormValues {
  name: string
  gender: string
  age: number | null
  height: string
  occupation: string
  appearance: string
  origin: string
  personality: string[]
  background: string
  biography: string
  remark?: string
  tags: string[]
}

function splitToArray(str?: string): string[] {
  if (!str) return []
  return str.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
}

function toFormValues(c: Character | null): Partial<CharacterFormValues> {
  if (!c) return {}
  return {
    name: c.name ?? '',
    gender: c.gender ?? '',
    age: c.age ? parseInt(c.age) || null : null,
    height: c.height ?? '',
    occupation: c.occupation ?? '',
    appearance: c.appearance ?? '',
    origin: c.origin ?? '',
    personality: splitToArray(c.personality),
    background: c.background ?? '',
    biography: c.biography ?? '',
    remark: c.remark ?? '',
    tags: splitToArray(c.tags),
  }
}

function toSaveData(values: CharacterFormValues): Partial<Character> {
  return {
    name: values.name?.trim() ?? '',
    gender: values.gender ?? '',
    age: values.age != null ? String(values.age) : '',
    height: values.height?.trim() ?? '',
    occupation: values.occupation?.trim() ?? '',
    appearance: values.appearance?.trim() ?? '',
    origin: values.origin?.trim() ?? '',
    personality: (values.personality ?? []).join(', '),
    background: values.background?.trim() ?? '',
    biography: values.biography?.trim() ?? '',
    remark: values.remark?.trim() ?? '',
    tags: (values.tags ?? []).join(', '),
  }
}

interface CharacterTabProps {
  bookId: number | null
}

export default function CharacterTab({ bookId }: CharacterTabProps) {
  const { message } = useAntdApp()
  const [characters, setCharacters] = React.useState<Character[]>([])
  const [createModalOpen, setCreateModalOpen] = React.useState(false)
  const [editTarget, setEditTarget] = React.useState<Character | null>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<Character | null>(null)
  const [configOpen, setConfigOpen] = React.useState(false)

  const [personalityOptions, setPersonalityOptions] = React.useState<CharacterOption[]>([])
  const [tagOptions, setTagOptions] = React.useState<CharacterOption[]>([])

  const [form] = Form.useForm<CharacterFormValues>()

  const loadCharacters = React.useCallback(async () => {
    if (bookId == null) return
    const list = await getBookCharacters(bookId)
    setCharacters(list)
  }, [bookId])

  const loadOptions = React.useCallback(async () => {
    const [pRes, tRes] = await Promise.all([
      window.electronAPI.getCharacterOptions({ category: 'personality' }),
      window.electronAPI.getCharacterOptions({ category: 'tag' }),
    ])
    if (pRes.success && pRes.data) setPersonalityOptions(pRes.data)
    if (tRes.success && tRes.data) setTagOptions(tRes.data)
  }, [])

  React.useEffect(() => {
    loadCharacters()
    loadOptions()
  }, [loadCharacters, loadOptions])

  React.useEffect(() => {
    if (createModalOpen) {
      form.setFieldsValue(toFormValues(editTarget))
    }
  }, [createModalOpen, editTarget, form])

  const openCreate = React.useCallback(() => {
    setEditTarget(null)
    setCreateModalOpen(true)
  }, [])

  const openEdit = React.useCallback((c: Character) => {
    setEditTarget(c)
    setCreateModalOpen(true)
  }, [])

  const handleFormSubmit = React.useCallback(async () => {
    try {
      const values = await form.validateFields()
      const data = toSaveData(values)
      if (bookId == null) return

      if (editTarget) {
        const res = await window.electronAPI.updateCharacter({ id: editTarget.id, data })
        if (res.success) {
          message.success('已保存')
          setCreateModalOpen(false)
          setEditTarget(null)
          form.resetFields()
          loadCharacters()
        } else {
          message.error(res.error || '保存失败')
        }
      } else {
        const res = await window.electronAPI.createCharacter({ bookId, data })
        if (res.success) {
          message.success('人物已创建')
          setCreateModalOpen(false)
          form.resetFields()
          loadCharacters()
        } else {
          message.error(res.error || '创建失败')
        }
      }
    } catch {
      // 校验失败，Form 会展示错误
    }
  }, [bookId, editTarget, form, loadCharacters, message])

  const handleDelete = React.useCallback(async () => {
    if (!deleteTarget) return
    const res = await window.electronAPI.deleteCharacter({ id: deleteTarget.id })
    if (res.success) {
      message.success('已删除')
      setDeleteTarget(null)
      loadCharacters()
    } else {
      message.error(res.error || '删除失败')
    }
  }, [deleteTarget, loadCharacters, message])

  const closeModal = React.useCallback(() => {
    setCreateModalOpen(false)
    setEditTarget(null)
    form.resetFields()
  }, [form])

  if (bookId == null) {
    return (
      <div className="character-tab character-tab-empty">
        <Empty description="请先选择书籍" />
      </div>
    )
  }

  return (
    <div className="character-tab">
      <div className="character-tab-header">
        <span className="character-tab-title">人物列表</span>
        <div style={{ display: 'flex', gap: 2 }}>
          <Tooltip title="配置选项">
            <Button
              type="text"
              size="small"
              icon={<SettingOutlined style={{ fontSize: 14 }} />}
              onClick={() => setConfigOpen(true)}
              className="character-add-btn"
            />
          </Tooltip>
          <Tooltip title="新建人物">
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined style={{ fontSize: 14 }} />}
              onClick={openCreate}
              className="character-add-btn"
            />
          </Tooltip>
        </div>
      </div>

      <div className="character-tab-list">
        {characters.length === 0 ? (
          <div className="character-tab-empty-card">
            <UserOutlined className="character-tab-empty-icon" />
            <p>暂无人物</p>
            <small>点击「新建人物」添加角色</small>
          </div>
        ) : (
          characters.map((c, index) => (
            <div key={c.id} className="character-card">
              <div className="character-card-main">
                <div className="character-card-name-row">
                  <span className="character-card-index">{index + 1}.</span>
                  <span className="character-card-name">{c.name}</span>
                  <Tooltip
                    title={
                      <div className="character-info-tooltip">
                        <div>性别：{c.gender || '-'}</div>
                        <div>年龄：{c.age || '-'}</div>
                        <div>身高：{c.height || '-'}</div>
                        <div>职业：{c.occupation || '-'}</div>
                        <div>性格：{c.personality || '-'}</div>
                      </div>
                    }
                  >
                    <span className="character-info-btn" onClick={(e) => e.stopPropagation()}>
                      <InfoCircleOutlined />
                    </span>
                  </Tooltip>
                </div>
                {c.tags && (
                  <div className="character-card-tags">
                    {splitToArray(c.tags).map((t, i) => (
                      <Tag key={i} variant="filled" color="default">{t}</Tag>
                    ))}
                  </div>
                )}
              </div>
              <div className="character-card-actions">
                <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(c)} title="编辑" />
                <Button type="text" size="small" icon={<DeleteOutlined />} onClick={() => setDeleteTarget(c)} title="删除" className="character-add-btn" />
              </div>
            </div>
          ))
        )}
      </div>

      <Modal
        title={editTarget ? '编辑人物' : '新建人物'}
        open={createModalOpen}
        onOk={handleFormSubmit}
        onCancel={closeModal}
        okText={editTarget ? '保存' : '创建'}
        cancelText="取消"
        width={540}
        destroyOnHidden
        styles={{ body: { maxHeight: '70vh', overflowY: 'auto' } }}
      >
        <Form form={form} layout="vertical" className="character-form-modal">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '请输入人物名称' }]}
          >
            <Input placeholder="请输入人物姓名" maxLength={50} />
          </Form.Item>

          <Form.Item name="gender" label="性别">
            <Radio.Group>
              <Radio value="男">男</Radio>
              <Radio value="女">女</Radio>
              <Radio value="其他">其他</Radio>
            </Radio.Group>
          </Form.Item>

          <Form.Item name="age" label="年龄">
            <InputNumber
              placeholder="请输入年龄"
              min={0}
              max={999}
              style={{ width: '100%' }}
            />
          </Form.Item>

          <Form.Item name="height" label="身高">
            <Input placeholder="如：175cm、中等、一米八" maxLength={30} />
          </Form.Item>

          <Form.Item name="occupation" label="职业">
            <Input placeholder="如：程序员、学生、侠客" maxLength={50} />
          </Form.Item>

          <Form.Item name="origin" label="籍贯">
            <Input placeholder="如：北京、江南" maxLength={50} />
          </Form.Item>

          <Form.Item name="appearance" label="外貌">
            <TextArea
              placeholder="体型、五官特征、标志性外观等"
              rows={2}
              maxLength={300}
              showCount
            />
          </Form.Item>

          <Form.Item name="personality" label="性格">
            <Select
              mode="tags"
              placeholder="选择或输入性格标签，回车确认"
              options={personalityOptions.map((p) => ({ label: p.value, value: p.value }))}
              maxCount={10}
            />
          </Form.Item>

          <Form.Item name="background" label="背景">
            <TextArea
              placeholder="人物的出身、经历、社会背景等"
              rows={3}
              maxLength={1000}
              showCount
            />
          </Form.Item>

          <Form.Item name="biography" label="人物小传">
            <TextArea
              placeholder="人物的简要介绍、在故事中的定位、核心动机等"
              rows={4}
              maxLength={2000}
              showCount
            />
          </Form.Item>

          <Form.Item name="tags" label="标签">
            <Select
              mode="tags"
              placeholder="选择或输入标签，回车确认"
              options={tagOptions.map((t) => ({ label: t.value, value: t.value }))}
              maxCount={10}
            />
          </Form.Item>

          <Form.Item name="remark" label="备注">
            <TextArea
              placeholder="其他需要记录的说明"
              rows={2}
              maxLength={500}
              showCount
            />
          </Form.Item>
        </Form>
      </Modal>

      <CharacterOptionsModal
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        onOptionsChange={loadOptions}
      />

      <Modal
        title="删除人物"
        open={!!deleteTarget}
        onOk={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>确认删除人物「{deleteTarget?.name}」？此操作不可恢复。</p>
      </Modal>
    </div>
  )
}

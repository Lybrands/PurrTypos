import Markdown from '../../components/Markdown'
import KnowledgeMarkdownEditor from '@/components/KnowledgeMarkdownEditor'
import { PurrCollapse, PurrInput, PurrModal, PurrSelect } from '@/purr-components'
import type { SettingEntityType } from '../../types'

export const MATERIAL_ENTITY_TYPE_OPTIONS: Array<{ value: SettingEntityType; label: string }> = [
  { value: 'location', label: '地点' },
  { value: 'faction', label: '势力' },
  { value: 'item', label: '物品' },
  { value: 'other', label: '其他' },
]

export default function MaterialProfileEditorModal({
  kind,
  open,
  name,
  tags,
  profileMd,
  documentKey,
  onNameChange,
  onTagsChange,
  onProfileChange,
  onOk,
  onCancel,
  entityType = 'other',
  onEntityTypeChange,
  tagOptions = [],
  inheritedBaseline,
  saving = false,
  creating = false,
}: {
  kind: 'character' | 'entity'
  open: boolean
  name: string
  tags: string[]
  profileMd: string
  documentKey: string
  onNameChange: (value: string) => void
  onTagsChange: (value: string[]) => void
  onProfileChange: (value: string) => void
  onOk: () => void
  onCancel: () => void
  entityType?: SettingEntityType
  onEntityTypeChange?: (value: SettingEntityType) => void
  tagOptions?: Array<{ label: string; value: string }>
  inheritedBaseline?: string
  saving?: boolean
  creating?: boolean
}) {
  const character = kind === 'character'
  return <PurrModal
    title={creating ? (character ? '新建人物' : '新建设定条目') : (character ? '编辑人物' : '编辑设定条目')}
    open={open}
    onOk={onOk}
    onCancel={onCancel}
    okText={creating ? '创建' : '保存'}
    cancelText="取消"
    okButtonProps={{ loading: saving, disabled: !name.trim() || !profileMd.trim() }}
    width={680}
    destroyOnHidden
    className="character-edit-modal"
  >
    <div className="character-edit-meta">
      {!character && <PurrSelect value={entityType} onChange={(value) => onEntityTypeChange?.(value as SettingEntityType)} options={MATERIAL_ENTITY_TYPE_OPTIONS} className="world-entity-edit-type" popupMatchSelectWidth={false} />}
      <PurrInput placeholder={character ? '人物姓名（必填）' : '条目名称（必填）'} value={name} onChange={(event) => onNameChange(event.target.value)} maxLength={50} className="character-edit-name" />
      <PurrSelect
        mode="tags"
        placeholder={character ? '标签：选择或输入，回车确认' : '标签：输入后回车确认'}
        value={tags}
        onChange={onTagsChange}
        options={tagOptions}
        {...(!character || !tagOptions.length ? { open: false, suffixIcon: null } : {})}
        maxCount={10}
        className="character-edit-tags"
      />
    </div>
    {inheritedBaseline && <><PurrCollapse size="small" defaultActiveKeys={["baseline"]} items={[{ key: "baseline", label: "原作资料 · 只读", children: <Markdown>{inheritedBaseline}</Markdown> }]} /><p>本书后续发展</p></>}
    <KnowledgeMarkdownEditor documentKey={documentKey} value={profileMd} onChange={onProfileChange} ariaLabel={character ? '人物档案' : '设定条目档案'} className="character-edit-profile" />
  </PurrModal>
}

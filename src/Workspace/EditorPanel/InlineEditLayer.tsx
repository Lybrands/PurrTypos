/**
 * 编辑器选中文字 → 浮出 Inline Edit 工具条 → 打开 Popover 输入改写指令 → AI 流式生成 → 替换/追加/取消。
 *
 * 架构：
 * - `InlineEditLayer` 对外唯一导出。EditorPanel 通过 props 传入 Lexical ref + 模型配置 + 上下文。
 * - 由 LexicalEditor 的 `onSelectionChange` 喂入 selection rect + text，触发 `SelectionBubble`。
 * - 点击 bubble 的按钮：调用 `captureSelection()` 拿到 `{ text, restore, replace }` 快照，
 *   随后打开 `InlineEditPopover` 进行 AI 流式改写；用户按「替换」时调 `replace(newText)` 落盘。
 * - 快照机制保证了：popover 打开后即使编辑器失焦，原选区仍可被恢复与替换。
 *
 * 上下文集成：
 * - Layer 持有「关联章节/大纲」「记忆/伏笔」选区，与主 AI 面板通过 localStorage 共享同一份默认勾选；
 * - Popover 顶部嵌入 `AiContextBar`：关联（章节/大纲）/ 注入（设定/伏笔）/ 提示词模板；
 * - 提交时关联章节/大纲全文与记忆/伏笔条目会作为 [参考资料] 拼入 user prompt（见 inlineEditContext）。
 *
 * 拆分：`SelectionBubble`、`InlineEditPopover`、参考资料拼装 `buildInjectedContext` 各自成文件，
 * 本文件只保留编排（选区 → 快照 → popover）与上下文 hook 接线。
 */

import React from 'react'
import type { AiModelConfig, EntityId } from '../../types'
import type { LexicalEditorHandle } from './LexicalEditor'
import MemoryModal from '../AiPanel/components/MemoryModal'
import { useAssociatedContext, useMemorySelection } from '../AiPanel/hooks'
import SelectionBubble from './SelectionBubble'
import InlineEditPopover, { type InlineCapture } from './InlineEditPopover'
import './InlineEditLayer.scss'

interface InlineEditLayerProps {
  /** Lexical 编辑器 ref，用于捕获/替换选区 */
  lexicalRef: React.RefObject<LexicalEditorHandle | null>
  /** 选区状态由 LexicalEditor 的 onSelectionChange 注入 */
  selection: { text: string; rect: DOMRect } | null
  /** 外部清空选区状态（点击关闭后） */
  onClearSelection: () => void
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>) => void
  selectedModelId: string
  /** 用户在 popover 内切换模型时回调，让外层持久化（与 ai-floating 共享一份选择） */
  onSelectedModelChange?: (id: string) => void
  bookId: EntityId | null
  chapterId: EntityId | null
  chapterTitle: string
  /** 章节概要 / 风格 / 书名，用于给 AI system prompt */
  bookTitle?: string
  /** 写作章节列表（用于关联章节下拉） */
  writingChapters: { id: EntityId; title: string }[]
}

export default function InlineEditLayer({
  lexicalRef,
  selection,
  onClearSelection,
  modelConfigs,
  onUpdateModelConfig,
  selectedModelId,
  onSelectedModelChange,
  bookId,
  chapterId,
  chapterTitle,
  bookTitle,
  writingChapters,
}: InlineEditLayerProps) {
  // 被点击过 preset 后，持有选区快照并进入 popover 模式。
  const [capture, setCapture] = React.useState<InlineCapture | null>(null)
  const [initialPrompt, setInitialPrompt] = React.useState('')

  // 若外部没传回调，本地兜底 state 让 popover 仍可切换
  const [localModelId, setLocalModelId] = React.useState(selectedModelId)
  React.useEffect(() => {
    setLocalModelId(selectedModelId)
  }, [selectedModelId])
  const effectiveModelId = onSelectedModelChange ? selectedModelId : localModelId
  const setModelId = onSelectedModelChange ?? setLocalModelId

  const activeModel = React.useMemo(
    () => modelConfigs.find((m) => m.id === effectiveModelId) ?? modelConfigs[0],
    [modelConfigs, effectiveModelId]
  )

  // 关联章节 / 大纲（与主 AI 面板共享一份持久化选择）
  const {
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    availableOutlines,
    chapterSelectOptions,
    outlineSelectOptions,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
  } = useAssociatedContext({ bookId, chapterId, writingChapters })

  // 记忆 / 伏笔（同样跨入口共享）
  const {
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  } = useMemorySelection(bookId)

  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false)

  const handleBubbleTrigger = React.useCallback(
    (presetPrompt: string) => {
      const snap = lexicalRef.current?.captureSelection()
      if (!snap) return
      setCapture(snap)
      setInitialPrompt(presetPrompt)
    },
    [lexicalRef]
  )

  const handleClosePopover = React.useCallback(() => {
    setCapture(null)
    setInitialPrompt('')
    onClearSelection()
  }, [onClearSelection])

  return (
    <>
      {selection && !capture && (
        <SelectionBubble
          rect={selection.rect}
          onPreset={(p) => handleBubbleTrigger(p.prompt)}
          onCustom={() => handleBubbleTrigger('')}
        />
      )}
      {capture && activeModel && (
        <InlineEditPopover
          capture={capture}
          initialPrompt={initialPrompt}
          modelConfigs={modelConfigs}
          onUpdateModelConfig={onUpdateModelConfig}
          selectedModelId={effectiveModelId}
          onSelectedModelChange={setModelId}
          bookId={bookId}
          chapterId={chapterId}
          chapterTitle={chapterTitle}
          bookTitle={bookTitle}
          onClose={handleClosePopover}
          // 上下文相关
          associatedChapterIds={associatedChapterIds}
          setAssociatedChapterIds={setAssociatedChapterIds}
          associatedOutlineIds={associatedOutlineIds}
          setAssociatedOutlineIds={setAssociatedOutlineIds}
          availableOutlines={availableOutlines}
          chapterSelectOptions={chapterSelectOptions}
          outlineSelectOptions={outlineSelectOptions}
          handleQuickAssociateChapter={handleQuickAssociateChapter}
          handleQuickAssociateOutline={handleQuickAssociateOutline}
          selectedMemoryIds={selectedMemoryIds}
          selectedForeshadowingIds={selectedForeshadowingIds}
          onOpenMemoryModal={() => setMemoryModalOpen(true)}
        />
      )}
      <MemoryModal
        open={memoryModalOpen}
        onCancel={() => setMemoryModalOpen(false)}
        bookId={bookId}
        writingChapters={writingChapters}
        selectedIds={selectedMemoryIds}
        selectedForeshadowingIds={selectedForeshadowingIds}
        onSelectConfirm={(memoryIds, foreshadowingIds) => {
          setSelectedMemoryIds(memoryIds)
          setSelectedForeshadowingIds(foreshadowingIds)
        }}
      />
    </>
  )
}

import React from 'react'
import { LexicalComposer } from '@lexical/react/LexicalComposer'
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin'
import { ContentEditable } from '@lexical/react/LexicalContentEditable'
import { OnChangePlugin } from '@lexical/react/LexicalOnChangePlugin'
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin'
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import {
  $getRoot,
  $createParagraphNode,
  $createTextNode,
  $getSelection,
  $isRangeSelection,
  CLEAR_HISTORY_COMMAND,
  COMMAND_PRIORITY_HIGH,
  KEY_DOWN_COMMAND,
  FORMAT_TEXT_COMMAND,
  UNDO_COMMAND,
  REDO_COMMAND,
  type EditorState,
  type LexicalEditor,
} from 'lexical'
import { Dropdown } from 'antd'
import type { MenuProps } from 'antd'
import { DownOutlined, UnorderedListOutlined, OrderedListOutlined } from '@ant-design/icons'
import { $setBlocksType } from '@lexical/selection'
import { HeadingNode, $createHeadingNode } from '@lexical/rich-text'
import { ListNode, ListItemNode, INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND } from '@lexical/list'
import { ListPlugin } from '@lexical/react/LexicalListPlugin'

// ─── 工具：纯文本 ↔ Lexical 状态 ────────────────────────────────
// 首行缩进由 CSS text-indent 控制，内容中不再插入全角空格

/** 加载：按行拆成段落，空内容留空让 placeholder 显示；加载后清空历史 */
function textToEditorState(text: string, editor: LexicalEditor) {
  editor.update(
    () => {
      const root = $getRoot()
      root.clear()
      if (!text) {
        root.append($createParagraphNode())
        return
      }
      const lines = text.split('\n')
      for (const line of lines) {
        const para = $createParagraphNode()
        para.append($createTextNode(line))
        root.append(para)
      }
    },
    {
      onUpdate: () => {
        editor.dispatchCommand(CLEAR_HISTORY_COMMAND, undefined)
      },
    }
  )
}

/** 保存：按段取文本拼接为纯文本（导出供工作台搜索等使用） */
export function editorStateToText(editorState: EditorState): string {
  let text = ''
  editorState.read(() => {
    const root = $getRoot()
    text = root.getChildren().map((node) => node.getTextContent()).join('\n')
  })
  return text
}

/** 读取编辑器当前纯文本，用于与外部 value 对比 */
function getEditorText(editor: LexicalEditor): string {
  return editor.getEditorState().read(() =>
    $getRoot()
      .getChildren()
      .map((node) => node.getTextContent())
      .join('\n')
  )
}

// ─── 插件：暴露 undo/redo 给父组件 ref ─────────────────────────────
function UndoRedoRefPlugin({ parentRef }: { parentRef: React.Ref<LexicalEditorHandle | null> }) {
  const [editor] = useLexicalComposerContext()
  React.useImperativeHandle(
    parentRef,
    () => ({
      undo: () => editor.dispatchCommand(UNDO_COMMAND, undefined),
      redo: () => editor.dispatchCommand(REDO_COMMAND, undefined),
    }),
    [editor]
  )
  return null
}

function ExposeLexicalEditorPlugin({ onEditor }: { onEditor: (editor: LexicalEditor | null) => void }) {
  const [editor] = useLexicalComposerContext()
  React.useEffect(() => {
    onEditor(editor)
    return () => onEditor(null)
  }, [editor, onEditor])
  return null
}

// ─── 插件：章节切换时加载内容 ────────────────────────────────────
// 配合父组件的 key={chapterId}：章节切换时组件完整重建
// 此插件额外处理「内容异步到达」的情况（初次 mount 时 value 可能是空字符串）

function LoadContentPlugin({ value }: { value: string }) {
  const [editor] = useLexicalComposerContext()
  const isInitialRef = React.useRef(true)

  React.useEffect(() => {
    // 初次 mount：无论内容是否为空都加载
    if (isInitialRef.current) {
      isInitialRef.current = false
      textToEditorState(value, editor)
      return
    }
    // value 后续变化：与编辑器实际内容对比
    // - 用户输入时：onChange → setContent(x) → value=x，编辑器也是 x → 相同 → 跳过
    // - 异步内容到达：value 变为真实内容，编辑器仍是之前的旧内容 → 不同 → 重载
    const editorText = getEditorText(editor)
    if (editorText !== value) {
      textToEditorState(value, editor)
    }
  }, [value, editor])

  return null
}

// 首行缩进已改为 CSS text-indent，无需段落缩进插件


// ─── 插件：\ 键唤起 AI，Escape 关闭 ─────────────────────────────

// ─── 插件：聚焦时隐藏 placeholder ─────────────────────────────────

function FocusPlaceholderPlugin({ onFocusChange }: { onFocusChange: (focused: boolean) => void }) {
  const [editor] = useLexicalComposerContext()
  React.useEffect(() => {
    const root = editor.getRootElement()
    if (!root) return
    const handleFocus = () => onFocusChange(true)
    const handleBlur = () => onFocusChange(false)
    root.addEventListener('focus', handleFocus, true)
    root.addEventListener('blur', handleBlur, true)
    return () => {
      root.removeEventListener('focus', handleFocus, true)
      root.removeEventListener('blur', handleBlur, true)
    }
  }, [editor, onFocusChange])
  return null
}

// ─── 插件：\ 键唤起 AI，Escape 关闭 ─────────────────────────────

function KeyPlugin({
  onKeyTrigger,
}: {
  onKeyTrigger?: (key: string, rect: DOMRect) => void
}) {
  const [editor] = useLexicalComposerContext()

  React.useEffect(() => {
    return editor.registerCommand(
      KEY_DOWN_COMMAND,
      (event: KeyboardEvent) => {
        if (event.key === '\\') {
          event.preventDefault()
          const el = editor.getRootElement()
          const rect = el?.getBoundingClientRect() ?? new DOMRect(200, 100, 0, 0)
          onKeyTrigger?.('backslash', rect)
          return true
        }
        if (event.key === 'Escape') {
          const el = editor.getRootElement()
          const rect = el?.getBoundingClientRect() ?? new DOMRect(200, 100, 0, 0)
          onKeyTrigger?.('escape', rect)
          return false
        }
        return false
      },
      COMMAND_PRIORITY_HIGH
    )
  }, [editor, onKeyTrigger])

  return null
}

// ─── 富文本格式栏 ────────────────────────────────────────────────
type TextFormat = 'bold' | 'italic' | 'underline' | 'strikethrough'
type HeadingTag = 'h1' | 'h2' | 'h3' | 'h4'

function FormatToolbar() {
  const [editor] = useLexicalComposerContext()

  const applyText = (format: TextFormat) => {
    editor.dispatchCommand(FORMAT_TEXT_COMMAND, format)
    editor.focus()
  }

  const applyHeading = (tag: HeadingTag) => {
    editor.update(() => {
      const selection = $getSelection()
      if ($isRangeSelection(selection)) {
        $setBlocksType(selection, () => $createHeadingNode(tag))
      }
    })
    editor.focus()
  }

  const applyList = (ordered: boolean) => {
    editor.dispatchCommand(ordered ? INSERT_ORDERED_LIST_COMMAND : INSERT_UNORDERED_LIST_COMMAND, undefined)
    editor.focus()
  }

  return (
    <div className="story-background-format-bar" role="toolbar">
      <span className="format-bar-group">
        <button type="button" onClick={() => applyText('bold')} title="加粗">
          <b>B</b>
        </button>
        <button type="button" onClick={() => applyText('italic')} title="斜体">
          <i>I</i>
        </button>
        <button type="button" onClick={() => applyText('underline')} title="下划线">
          <u>U</u>
        </button>
        <button type="button" onClick={() => applyText('strikethrough')} title="删除线">
          <s>S</s>
        </button>
      </span>
      <span className="format-bar-divider" />
      <span className="format-bar-group">
        <Dropdown
          menu={{
            items: [
              { key: 'h1', label: '标题 1', onClick: () => applyHeading('h1') },
              { key: 'h2', label: '标题 2', onClick: () => applyHeading('h2') },
              { key: 'h3', label: '标题 3', onClick: () => applyHeading('h3') },
              { key: 'h4', label: '标题 4', onClick: () => applyHeading('h4') },
            ] as MenuProps['items'],
          }}
          trigger={['click']}
        >
          <button type="button" title="标题" className="format-bar-heading-trigger">
            标题 <DownOutlined />
          </button>
        </Dropdown>
      </span>
      <span className="format-bar-divider" />
      <span className="format-bar-group">
        <button type="button" onClick={() => applyList(false)} title="无序列表">
          <UnorderedListOutlined />
        </button>
        <button type="button" onClick={() => applyList(true)} title="有序列表（1. 2. 3.）">
          <OrderedListOutlined />
        </button>
      </span>
    </div>
  )
}

// ─── 主组件 ─────────────────────────────────────────────────────

export interface LexicalEditorHandle {
  undo: () => void
  redo: () => void
}

interface LexicalEditorProps {
  value: string
  chapterId: number | null
  onChange: (text: string) => void
  onKeyTrigger?: (key: string, rect: DOMRect) => void
  placeholder?: string
  className?: string
  /** 是否显示富文本格式栏（加粗/斜体/下划线） */
  showFormatToolbar?: boolean
  /** 供工作台搜索定位 Lexical 实例 */
  onLexicalEditor?: (editor: LexicalEditor | null) => void
}

const theme = {
  paragraph: 'lexical-paragraph',
  heading: {
    h1: 'lexical-heading-h1',
    h2: 'lexical-heading-h2',
    h3: 'lexical-heading-h3',
    h4: 'lexical-heading-h4',
  },
  list: {
    list: 'lexical-list',
    listitem: 'lexical-listitem',
  },
  text: {
    bold: 'lexical-text-bold',
    italic: 'lexical-text-italic',
    underline: 'lexical-text-underline',
    strikethrough: 'lexical-text-strikethrough',
  },
}

const LexicalEditorComponentInner = React.forwardRef<LexicalEditorHandle, LexicalEditorProps>(function LexicalEditorComponentInner({
  value,
  chapterId,
  onChange,
  onKeyTrigger,
  placeholder = '开始写作...',
  className,
  showFormatToolbar = false,
  onLexicalEditor,
}, ref) {
  const [focused, setFocused] = React.useState(false)
  const initialConfig = React.useMemo(
    () => ({
      namespace: 'WritingEditor',
      theme,
      nodes: showFormatToolbar ? [HeadingNode, ListNode, ListItemNode] : [HeadingNode],
      onError: (err: Error) => console.error('[LexicalEditor]', err),
    }),
    [showFormatToolbar]
  )

  const handleChange = React.useCallback(
    (editorState: EditorState) => {
      const text = editorStateToText(editorState)
      onChange(text)
    },
    [onChange]
  )

  return (
    <LexicalComposer initialConfig={initialConfig}>
      <UndoRedoRefPlugin parentRef={ref} />
      {onLexicalEditor && <ExposeLexicalEditorPlugin onEditor={onLexicalEditor} />}
      <div className={`${className ?? ''} ${focused ? 'lexical-focused' : ''}`.trim()}>
        {showFormatToolbar && <FormatToolbar />}
        {showFormatToolbar && <ListPlugin />}
        <RichTextPlugin
          contentEditable={
            <ContentEditable
              className="lexical-content"
              spellCheck={false}
              aria-placeholder={placeholder}
              placeholder={<div className="lexical-placeholder">{placeholder}</div>}
            />
          }
          placeholder={
            <div className="lexical-placeholder">{placeholder}</div>
          }
          ErrorBoundary={LexicalErrorBoundary}
        />
        <FocusPlaceholderPlugin onFocusChange={setFocused} />
        <OnChangePlugin onChange={handleChange} ignoreSelectionChange />
        <HistoryPlugin />
        <LoadContentPlugin value={value} />

        <KeyPlugin onKeyTrigger={onKeyTrigger} />
      </div>
    </LexicalComposer>
  )
})

export default LexicalEditorComponentInner

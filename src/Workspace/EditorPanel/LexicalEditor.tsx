import React from 'react'
import { LexicalComposer } from '@lexical/react/LexicalComposer'
import { RichTextPlugin } from '@lexical/react/LexicalRichTextPlugin'
import { ContentEditable } from '@lexical/react/LexicalContentEditable'
import { OnChangePlugin } from '@lexical/react/LexicalOnChangePlugin'
import { HistoryPlugin } from '@lexical/react/LexicalHistoryPlugin'
import { LexicalErrorBoundary } from '@lexical/react/LexicalErrorBoundary'
import { type EditorState, type LexicalEditor } from 'lexical'
import { HeadingNode } from '@lexical/rich-text'
import { ListNode, ListItemNode } from '@lexical/list'
import { ListPlugin } from '@lexical/react/LexicalListPlugin'
import { editorStateToText, reformatArticleText } from './plugins/editorText'
import { EditorHandlePlugin, type LexicalEditorHandle } from './plugins/EditorHandlePlugin'
import { IdleDetectPlugin } from './plugins/IdleDetectPlugin'
import { SelectionChangePlugin } from './plugins/SelectionChangePlugin'
import { LoadContentPlugin } from './plugins/LoadContentPlugin'
import { ExposeLexicalEditorPlugin, FocusPlaceholderPlugin, KeyPlugin } from './plugins/misc'
import { FormatToolbar } from './plugins/FormatToolbar'
import { WordRulerPlugin } from './plugins/WordRulerPlugin'

// 对外 API 保持稳定：这些符号原本就从本文件导出
export { editorStateToText, reformatArticleText }
export type { LexicalEditorHandle }

interface LexicalEditorProps {
  value: string
  onChange: (text: string) => void
  onKeyTrigger?: (key: string, rect: DOMRect) => void
  placeholder?: string
  className?: string
  /** 是否显示富文本格式栏（加粗/斜体/下划线） */
  showFormatToolbar?: boolean
  /** 供工作台搜索定位 Lexical 实例 */
  onLexicalEditor?: (editor: LexicalEditor | null) => void
  /** 选区变化回调：非空文本选区时返回 text + rect，否则 null。 */
  onSelectionChange?: (payload: { text: string; rect: DOMRect } | null) => void
  /** Ghost text 空闲检测：开启后 idleMs 毫秒无输入则触发 onGhostIdle。 */
  ghostEnabled?: boolean
  onGhostIdle?: (payload: { prefix: string; cursorRect: DOMRect }) => void
  /** 任意编辑/光标变化：父组件用来 cancel 正在显示的 ghost。 */
  onGhostReset?: () => void
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
  onChange,
  onKeyTrigger,
  placeholder = '开始写作...',
  className,
  showFormatToolbar = false,
  onLexicalEditor,
  onSelectionChange,
  ghostEnabled = false,
  onGhostIdle,
  onGhostReset,
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
      <EditorHandlePlugin parentRef={ref} />
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
        <WordRulerPlugin interval={500} />

        <KeyPlugin onKeyTrigger={onKeyTrigger} />
        {onSelectionChange && <SelectionChangePlugin onSelectionChange={onSelectionChange} />}
        {ghostEnabled && onGhostIdle && onGhostReset && (
          <IdleDetectPlugin
            enabled={ghostEnabled}
            onIdle={onGhostIdle}
            onChangeAny={onGhostReset}
          />
        )}
      </div>
    </LexicalComposer>
  )
})

export default LexicalEditorComponentInner

import React from 'react'
import type { Editor } from '@tiptap/core'
import { Extension } from '@tiptap/core'
import { TableKit } from '@tiptap/extension-table'
import { EditorContent, useEditor, useEditorState } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import {
  OrderedListIcon,
  PurrButton,
  PurrTooltip,
  RedoIcon,
  UndoIcon,
  UnorderedListIcon,
} from '@/purr-components'
import { htmlToMarkdown, markdownToHtml, markdownToYamlFrontmatter, yamlFrontmatterToMarkdown } from '@/utils/markdown'
import { looksLikeMarkdown } from './markdown'
import './index.scss'

const LiteralTab = Extension.create({
  name: 'knowledgeMarkdownLiteralTab',
  addKeyboardShortcuts() {
    return {
      Tab: () => {
        if (this.editor.commands.sinkListItem('listItem')) return true
        this.editor.commands.insertContent('\t')
        return true
      },
    }
  },
})

export { appendImportedMarkdown, looksLikeMarkdown } from './markdown'

export interface KnowledgeMarkdownEditorHandle {
  focus: () => void
  getMarkdown: () => string
  setMarkdown: (markdown: string) => void
}

export interface KnowledgeMarkdownEditorProps {
  /** Switches the editor instance when the business document changes. */
  documentKey: React.Key
  value: string
  onChange: (value: string) => void
  className?: string
  ariaLabel?: string
  yamlFrontmatter?: boolean
}

interface ToolbarState {
  heading2: boolean
  bold: boolean
  italic: boolean
  bulletList: boolean
  orderedList: boolean
  blockquote: boolean
  canUndo: boolean
  canRedo: boolean
}

const EMPTY_TOOLBAR_STATE: ToolbarState = {
  heading2: false,
  bold: false,
  italic: false,
  bulletList: false,
  orderedList: false,
  blockquote: false,
  canUndo: false,
  canRedo: false,
}

/**
 * Shared rich Markdown editor for knowledge/reference documents such as
 * characters, world building, story background, outlines and Agent drafts.
 * Long-form manuscript body text intentionally stays in its dedicated editor.
 */
const KnowledgeMarkdownEditor = React.forwardRef<
  KnowledgeMarkdownEditorHandle,
  KnowledgeMarkdownEditorProps
>(function KnowledgeMarkdownEditor({
  documentKey,
  value,
  onChange,
  className,
  ariaLabel = '资料内容',
  yamlFrontmatter = false,
}, ref) {
  const editorRef = React.useRef<Editor | null>(null)
  const onChangeRef = React.useRef(onChange)

  React.useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  const editor = useEditor({
    immediatelyRender: true,
    extensions: [
      LiteralTab,
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4] },
      }),
      TableKit,
    ],
    content: markdownToHtml(yamlFrontmatter ? yamlFrontmatterToMarkdown(value) : value),
    editorProps: {
      attributes: {
        class: 'knowledge-markdown-editor__editable',
        spellcheck: 'false',
        'aria-label': ariaLabel,
      },
      handlePaste: (_view, event) => {
        const text = event.clipboardData?.getData('text/plain') ?? ''
        if (!looksLikeMarkdown(text)) return false

        event.preventDefault()
        editorRef.current?.commands.insertContent(markdownToHtml(yamlFrontmatter ? yamlFrontmatterToMarkdown(text) : text))
        return true
      },
    },
    onUpdate: ({ editor: currentEditor }) => {
      const markdown = htmlToMarkdown(currentEditor.getHTML())
      onChangeRef.current(yamlFrontmatter ? markdownToYamlFrontmatter(markdown) : markdown)
    },
  }, [documentKey])

  React.useEffect(() => {
    editorRef.current = editor ?? null
  }, [editor])

  React.useEffect(() => {
    if (!editor) return
    const currentValue = htmlToMarkdown(editor.getHTML())
    const currentMarkdown = yamlFrontmatter ? markdownToYamlFrontmatter(currentValue) : currentValue
    if (currentMarkdown === value) return
    editor.commands.setContent(markdownToHtml(yamlFrontmatter ? yamlFrontmatterToMarkdown(value) : value), { emitUpdate: false })
  }, [editor, value, yamlFrontmatter])

  React.useImperativeHandle(ref, () => ({
    focus: () => editorRef.current?.commands.focus(),
    getMarkdown: () => {
      const currentEditor = editorRef.current
      if (!currentEditor) return value
      const markdown = htmlToMarkdown(currentEditor.getHTML())
      return yamlFrontmatter ? markdownToYamlFrontmatter(markdown) : markdown
    },
    setMarkdown: (markdown) => {
      const currentEditor = editorRef.current
      if (!currentEditor) {
        onChangeRef.current(markdown)
        return
      }
      currentEditor.commands.setContent(markdownToHtml(yamlFrontmatter ? yamlFrontmatterToMarkdown(markdown) : markdown))
    },
  }), [value, yamlFrontmatter])

  const toolbarState = useEditorState({
    editor,
    selector: ({ editor: currentEditor }): ToolbarState => ({
      heading2: currentEditor.isActive('heading', { level: 2 }),
      bold: currentEditor.isActive('bold'),
      italic: currentEditor.isActive('italic'),
      bulletList: currentEditor.isActive('bulletList'),
      orderedList: currentEditor.isActive('orderedList'),
      blockquote: currentEditor.isActive('blockquote'),
      canUndo: currentEditor.can().chain().focus().undo().run(),
      canRedo: currentEditor.can().chain().focus().redo().run(),
    }),
  }) ?? EMPTY_TOOLBAR_STATE

  const formatButton = (
    label: string,
    active: boolean,
    action: () => void,
    content: React.ReactNode,
    disabled = false,
  ) => (
    <PurrTooltip title={label}>
      <PurrButton
        type="text"
        size="small"
        className={active ? 'is-active' : ''}
        aria-label={label}
        aria-pressed={active}
        disabled={disabled}
        onClick={action}
      >
        {content}
      </PurrButton>
    </PurrTooltip>
  )

  return (
    <div className={`knowledge-markdown-editor${className ? ` ${className}` : ''}`}>
      <div
        className="knowledge-markdown-editor__toolbar"
        role="toolbar"
        aria-label="Markdown 格式"
      >
        {formatButton(
          '二级标题',
          toolbarState.heading2,
          () => editor?.chain().focus().toggleHeading({ level: 2 }).run(),
          <span aria-hidden="true">H2</span>,
        )}
        {formatButton(
          '加粗',
          toolbarState.bold,
          () => editor?.chain().focus().toggleBold().run(),
          <strong>B</strong>,
        )}
        {formatButton(
          '斜体',
          toolbarState.italic,
          () => editor?.chain().focus().toggleItalic().run(),
          <em>I</em>,
        )}
        <span className="knowledge-markdown-editor__divider" />
        {formatButton(
          '无序列表',
          toolbarState.bulletList,
          () => editor?.chain().focus().toggleBulletList().run(),
          <UnorderedListIcon />,
        )}
        {formatButton(
          '有序列表',
          toolbarState.orderedList,
          () => editor?.chain().focus().toggleOrderedList().run(),
          <OrderedListIcon />,
        )}
        {formatButton(
          '引用',
          toolbarState.blockquote,
          () => editor?.chain().focus().toggleBlockquote().run(),
          <span aria-hidden="true">“</span>,
        )}
        <span className="knowledge-markdown-editor__divider" />
        {formatButton(
          '撤销',
          false,
          () => editor?.chain().focus().undo().run(),
          <UndoIcon />,
          !toolbarState.canUndo,
        )}
        {formatButton(
          '重做',
          false,
          () => editor?.chain().focus().redo().run(),
          <RedoIcon />,
          !toolbarState.canRedo,
        )}
      </div>
      <EditorContent
        editor={editor}
        className="knowledge-markdown-editor__content"
      />
    </div>
  )
})

export default KnowledgeMarkdownEditor

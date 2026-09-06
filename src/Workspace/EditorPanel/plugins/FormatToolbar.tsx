// ─── 富文本格式栏 ────────────────────────────────────────────────

import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import { $getSelection, $isRangeSelection, FORMAT_TEXT_COMMAND } from 'lexical'
import { PurrDropdown } from '@/purr-components'
import { ChevronDownIcon, UnorderedListIcon, OrderedListIcon } from '@/purr-components'
import { $setBlocksType } from '@lexical/selection'
import { $createHeadingNode } from '@lexical/rich-text'
import { INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND } from '@lexical/list'

type TextFormat = 'bold' | 'italic' | 'underline' | 'strikethrough'
type HeadingTag = 'h1' | 'h2' | 'h3' | 'h4'

export function FormatToolbar() {
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
        <PurrDropdown
          menu={{
            items: [
              { key: 'h1', label: '标题 1', onClick: () => applyHeading('h1') },
              { key: 'h2', label: '标题 2', onClick: () => applyHeading('h2') },
              { key: 'h3', label: '标题 3', onClick: () => applyHeading('h3') },
              { key: 'h4', label: '标题 4', onClick: () => applyHeading('h4') },
            ],
          }}
        >
          <button type="button" title="标题" className="format-bar-heading-trigger">
            标题 <ChevronDownIcon />
          </button>
        </PurrDropdown>
      </span>
      <span className="format-bar-divider" />
      <span className="format-bar-group">
        <button type="button" onClick={() => applyList(false)} title="无序列表">
          <UnorderedListIcon />
        </button>
        <button type="button" onClick={() => applyList(true)} title="有序列表（1. 2. 3.）">
          <OrderedListIcon />
        </button>
      </span>
    </div>
  )
}

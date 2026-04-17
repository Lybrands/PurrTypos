import React from 'react'
import ReactDOM from 'react-dom'
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
  $createRangeSelection,
  $setSelection,
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
  type RangeSelection,
} from 'lexical'
import { Dropdown } from 'antd'
import type { MenuProps } from 'antd'
import { DownOutlined, UnorderedListOutlined, OrderedListOutlined } from '@ant-design/icons'
import { $setBlocksType } from '@lexical/selection'
import { HeadingNode, $createHeadingNode } from '@lexical/rich-text'
import { ListNode, ListItemNode, INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND } from '@lexical/list'
import { ListPlugin } from '@lexical/react/LexicalListPlugin'
import type { EntityId } from '../../types'

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

/**
 * 一键排版：去除每段首行的空白（包括半角、全角空格、制表符、不换行空格），
 * 并删除段落间只含空白的空行。
 * - 不保留任何首行缩进，由 CSS text-indent 负责视觉缩进
 * - 空行定义：trim 后为空
 */
export function reformatArticleText(text: string): string {
  if (!text) return text
  return text
    .split('\n')
    .map((line) => line.replace(/^[\u0020\t\u00A0\u3000]+/, ''))
    .filter((line) => line.trim().length > 0)
    .join('\n')
}

// ─── 插件：暴露 undo/redo/reformat 给父组件 ref ─────────────────────
function UndoRedoRefPlugin({ parentRef }: { parentRef: React.Ref<LexicalEditorHandle | null> }) {
  const [editor] = useLexicalComposerContext()
  React.useImperativeHandle(
    parentRef,
    () => ({
      undo: () => editor.dispatchCommand(UNDO_COMMAND, undefined),
      redo: () => editor.dispatchCommand(REDO_COMMAND, undefined),
      reformat: () => {
        const before = getEditorText(editor)
        const after = reformatArticleText(before)
        if (after === before) {
          return { changed: false, removedEmptyLines: 0, strippedIndents: 0 }
        }
        const beforeLines = before.split('\n')
        const afterLines = after ? after.split('\n') : []
        const removedEmptyLines = beforeLines.length - afterLines.length
        let strippedIndents = 0
        for (const line of beforeLines) {
          if (/^[\u0020\t\u00A0\u3000]+/.test(line) && line.trim().length > 0) {
            strippedIndents += 1
          }
        }
        editor.update(() => {
          const root = $getRoot()
          root.clear()
          if (!after) {
            root.append($createParagraphNode())
            return
          }
          for (const line of afterLines) {
            const para = $createParagraphNode()
            para.append($createTextNode(line))
            root.append(para)
          }
        })
        return { changed: true, removedEmptyLines, strippedIndents }
      },
      insertAtCursor: (text: string) => {
        if (!text) return
        const lines = text.split('\n')
        editor.focus()
        editor.update(() => {
          const selection = $getSelection()
          if ($isRangeSelection(selection)) {
            // 多行：第一行直接插入到当前光标，后续行作为新段落
            // 利用 selection.insertText 处理首行；后续行通过 insertParagraph 插入
            selection.insertText(lines[0])
            for (let i = 1; i < lines.length; i++) {
              const sel = $getSelection()
              if ($isRangeSelection(sel)) {
                sel.insertParagraph()
                sel.insertText(lines[i])
              }
            }
          } else {
            // 没有选区：追加到 root 末尾
            const root = $getRoot()
            for (const line of lines) {
              const para = $createParagraphNode()
              para.append($createTextNode(line))
              root.append(para)
            }
          }
        })
      },
      captureSelection: () => {
        let result:
          | {
              text: string
              restore: () => void
              replace: (newText: string) => void
            }
          | null = null
        editor.getEditorState().read(() => {
          const sel = $getSelection()
          if (!$isRangeSelection(sel) || sel.isCollapsed()) return
          const text = sel.getTextContent()
          if (!text) return
          const anchorKey = sel.anchor.key
          const anchorOffset = sel.anchor.offset
          const anchorType = sel.anchor.type
          const focusKey = sel.focus.key
          const focusOffset = sel.focus.offset
          const focusType = sel.focus.type

          const restoreFn = () => {
            editor.update(() => {
              const range = $createRangeSelection()
              range.anchor.set(anchorKey, anchorOffset, anchorType)
              range.focus.set(focusKey, focusOffset, focusType)
              $setSelection(range)
            })
          }
          const replaceFn = (newText: string) => {
            if (!newText) return
            editor.focus()
            editor.update(() => {
              const range = $createRangeSelection()
              range.anchor.set(anchorKey, anchorOffset, anchorType)
              range.focus.set(focusKey, focusOffset, focusType)
              $setSelection(range)
              const current = $getSelection() as RangeSelection | null
              if (!current || !$isRangeSelection(current)) return
              const lines = newText.split('\n')
              current.insertText(lines[0])
              for (let i = 1; i < lines.length; i++) {
                const s = $getSelection()
                if ($isRangeSelection(s)) {
                  s.insertParagraph()
                  s.insertText(lines[i])
                }
              }
            })
          }
          result = { text, restore: restoreFn, replace: replaceFn }
        })
        return result
      },
      getSelectedText: () => {
        let text = ''
        editor.getEditorState().read(() => {
          const sel = $getSelection()
          if ($isRangeSelection(sel) && !sel.isCollapsed()) {
            text = sel.getTextContent()
          }
        })
        return text
      },
      focus: () => editor.focus(),
    }),
    [editor]
  )
  return null
}

// ─── 插件：Ghost Text 触发检测 ──────────────────────────────────
// - selection 折叠且空闲 `idleMs` 毫秒后触发 onIdle（携带光标前缀 + cursor DOM rect）
// - 任何 update（编辑或光标移动）触发 onChangeAny，供父组件即时 cancel ghost
function IdleDetectPlugin({
  enabled,
  idleMs = 800,
  onIdle,
  onChangeAny,
}: {
  enabled: boolean
  idleMs?: number
  onIdle: (payload: { prefix: string; cursorRect: DOMRect }) => void
  onChangeAny: () => void
}) {
  const [editor] = useLexicalComposerContext()
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTriggerPrefixRef = React.useRef<string | null>(null)

  React.useEffect(() => {
    if (!enabled) {
      if (timerRef.current) clearTimeout(timerRef.current)
      lastTriggerPrefixRef.current = null
      return
    }
    const clearTimer = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }

    const unregister = editor.registerUpdateListener(({ editorState, tags }) => {
      // 内部 load / remote 等 tag 不触发
      if (tags.has('history-merge')) return
      onChangeAny()
      clearTimer()

      // 800ms idle 后再检查状态
      timerRef.current = setTimeout(() => {
        editorState.read(() => {
          const sel = $getSelection()
          if (!$isRangeSelection(sel) || !sel.isCollapsed()) return
          const root = editor.getRootElement()
          if (!root) return
          if (document.activeElement !== root) return
          const fullText = $getRoot().getTextContent()
          if (!fullText.trim()) return
          // 用选区前的文本做 prefix；Lexical 没有直接 offset，
          // 用 anchor 所在 textNode + 之前所有节点拼接。
          const anchorNode = sel.anchor.getNode()
          const anchorOffset = sel.anchor.offset
          let prefix = ''
          const children = $getRoot().getChildren()
          for (const child of children) {
            const desc = child.getTextContent()
            if (child.getKey() === anchorNode.getTopLevelElement()?.getKey()) {
              // 粗粒度：段落前的全部 + 本段落 anchorOffset 之前
              // （多节点 inline 的精细 offset 在 MVP 暂忽略）
              prefix += desc.slice(0, anchorOffset)
              break
            }
            prefix += desc + '\n'
          }
          if (prefix.length < 4) return
          if (prefix === lastTriggerPrefixRef.current) return
          lastTriggerPrefixRef.current = prefix
          const domSel = window.getSelection()
          if (!domSel || domSel.rangeCount === 0) return
          const rect = domSel.getRangeAt(0).getBoundingClientRect()
          onIdle({ prefix, cursorRect: rect })
        })
      }, idleMs)
    })
    return () => {
      unregister()
      clearTimer()
    }
  }, [editor, enabled, idleMs, onIdle, onChangeAny])

  return null
}

// ─── 插件：暴露选区变化（非空文本选区时）给父组件 ──────────────────
function SelectionChangePlugin({
  onSelectionChange,
}: {
  onSelectionChange: (payload: { text: string; rect: DOMRect } | null) => void
}) {
  const [editor] = useLexicalComposerContext()
  React.useEffect(() => {
    const root = editor.getRootElement()
    const report = () => {
      editor.getEditorState().read(() => {
        const sel = $getSelection()
        if (!$isRangeSelection(sel) || sel.isCollapsed()) {
          onSelectionChange(null)
          return
        }
        const text = sel.getTextContent()
        if (!text.trim()) {
          onSelectionChange(null)
          return
        }
        const domSel = window.getSelection()
        if (!domSel || domSel.rangeCount === 0) {
          onSelectionChange(null)
          return
        }
        const rect = domSel.getRangeAt(0).getBoundingClientRect()
        if (rect.width === 0 && rect.height === 0) {
          onSelectionChange(null)
          return
        }
        onSelectionChange({ text, rect })
      })
    }
    const unregister = editor.registerUpdateListener(() => {
      report()
    })
    const onBlur = () => {
      // blur 会触发 DOM selection 清空，但 Lexical 选区可能仍在。
      // 延时一帧：若焦点移到受控 UI（如工具条），保留；否则关闭。
      setTimeout(() => {
        const active = document.activeElement as HTMLElement | null
        if (active?.closest('.inline-edit-toolbar') || active?.closest('.inline-edit-popover')) {
          return
        }
        onSelectionChange(null)
      }, 50)
    }
    root?.addEventListener('blur', onBlur, true)
    return () => {
      unregister()
      root?.removeEventListener('blur', onBlur, true)
    }
  }, [editor, onSelectionChange])
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
  /**
   * 记录编辑器最近一次提交后的纯文本。用于辨别 `value` prop 是否源自用户自己的输入。
   *
   * 为何需要：
   * - 用户输入时，Lexical 提交 → onChange 把文本向外回流到父组件的 setState，
   *   随后若父组件因为「其他 state」（如 saveStatus）先一步触发过中间渲染，
   *   父组件重渲染时传下来的 `value` 就可能「暂时落后」于编辑器最新提交的文本；
   *   此时旧逻辑的 `editorText !== value` 会成立 → 触发 textToEditorState(旧 value)
   *   → root.clear() + 以旧文本重建段落，造成「选中替换后，选区外的内容也被吞掉」。
   * - 通过追踪编辑器自身最近提交的文本，只要 `value` 能匹配到近期的「自发出」文本，
   *   就一律跳过重建，即使与 `editorText` 的严格比较不一致也不会误伤。
   */
  const lastEmittedRef = React.useRef<string>('')

  React.useEffect(() => {
    const unregister = editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        lastEmittedRef.current = $getRoot()
          .getChildren()
          .map((n) => n.getTextContent())
          .join('\n')
      })
    })
    return unregister
  }, [editor])

  React.useEffect(() => {
    if (isInitialRef.current) {
      isInitialRef.current = false
      lastEmittedRef.current = value
      textToEditorState(value, editor)
      return
    }
    // 1) value 与编辑器最近发出的文本一致 → 这是用户输入回环造成的 value 更新，跳过。
    if (value === lastEmittedRef.current) return
    // 2) value 与编辑器当前实际文本一致 → 已经同步过，跳过。
    const editorText = getEditorText(editor)
    if (editorText === value) return
    // 3) 走到这里说明 value 是真正的外部更新（章节异步加载 / AI 写回等），才重建编辑器。
    lastEmittedRef.current = value
    textToEditorState(value, editor)
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
  /**
   * 一键排版：去除段落首行空白、删除段落间空行。
   * 返回本次排版的统计；若无变更则 changed=false。
   * 保留编辑历史（可撤销）。
   */
  reformat: () => {
    changed: boolean
    removedEmptyLines: number
    strippedIndents: number
  }
  /**
   * 把文本插入到当前光标位置：
   * - 若有选区，先替换选区
   * - 文本中的 `\n` 会拆为新段落
   * - 没有选区或聚焦时：默认追加到末尾
   * 保留编辑历史（可撤销）。
   */
  insertAtCursor: (text: string) => void
  /**
   * 捕获当前选区，返回：
   * - `text`：选中文本
   * - `restore()`：重新把同一范围设为选区
   * - `replace(newText)`：基于快照范围替换为新文本
   * 无选区（collapsed / 空）返回 null。
   */
  captureSelection: () => {
    text: string
    restore: () => void
    replace: (newText: string) => void
  } | null
  /** 读取当前选区纯文本；无选区返回空串。 */
  getSelectedText: () => string
  /** 聚焦编辑器。 */
  focus: () => void
}

interface LexicalEditorProps {
  value: string
  chapterId: EntityId | null
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

/**
 * 字数标尺插件：每 N 个字（默认 500）在编辑器右侧打一个浮签：「500字 / 1000字 ...」。
 * - 字数定义：去除空白字符（含换行）后的全部字符
 * - 标尺贴在「跨过该阈值的段落」的顶部，scrollTop 一起滚
 * - 一段若跨多个阈值，按发生顺序竖向堆叠排列
 */
function WordRulerPlugin({ interval = 500 }: { interval?: number }) {
  const [editor] = useLexicalComposerContext()
  const [markers, setMarkers] = React.useState<{ top: number; label: string }[]>([])
  const [wrapEl, setWrapEl] = React.useState<HTMLElement | null>(null)

  React.useEffect(() => {
    if (interval <= 0) return

    let raf = 0
    const compute = () => {
      const editorEl = editor.getRootElement()
      if (!editorEl) {
        setMarkers([])
        return
      }
      const wrap = editorEl.closest('.editor-lexical-wrap') as HTMLElement | null
      if (!wrap) return
      setWrapEl(wrap)

      const next: { top: number; label: string }[] = []
      let cum = 0
      let nextThreshold = interval

      editor.getEditorState().read(() => {
        const root = $getRoot()
        const children = root.getChildren()
        for (const child of children) {
          const text = child.getTextContent()
          // 去掉空白与换行，按可见字符计数
          const charsInThisPara = text.replace(/\s+/g, '').length
          cum += charsInThisPara
          if (cum < nextThreshold) continue

          const paraEl = editor.getElementByKey(child.getKey()) as HTMLElement | null
          if (!paraEl) {
            while (nextThreshold <= cum) nextThreshold += interval
            continue
          }
          // 段相对于 wrap 的纵坐标（offsetParent 应该就是 .editor-lexical-wrap）
          const baseTop = paraEl.offsetTop
          let stackOffset = 0
          while (nextThreshold <= cum) {
            next.push({
              top: baseTop + stackOffset,
              label: `${nextThreshold} 字`,
            })
            nextThreshold += interval
            stackOffset += 22 // 同段多个阈值时往下错开
          }
        }
      })
      setMarkers(next)
    }

    const schedule = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = requestAnimationFrame(compute)
    }

    schedule()
    const unregister = editor.registerUpdateListener(() => schedule())
    const ro = new ResizeObserver(schedule)
    const editorEl = editor.getRootElement()
    if (editorEl) ro.observe(editorEl)
    window.addEventListener('resize', schedule)

    return () => {
      unregister()
      ro.disconnect()
      window.removeEventListener('resize', schedule)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [editor, interval])

  if (!wrapEl || markers.length === 0) return null
  return ReactDOM.createPortal(
    <>
      {markers.map((m, i) => (
        <div
          key={`${m.label}-${i}`}
          className="lexical-word-ruler"
          style={{ top: m.top }}
        >
          {m.label}
        </div>
      ))}
    </>,
    wrapEl,
  )
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

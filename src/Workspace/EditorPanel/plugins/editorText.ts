// ─── 工具：纯文本 ↔ Lexical 状态 ────────────────────────────────
// 首行缩进由 CSS text-indent 控制，内容中不再插入全角空格

import {
  $getRoot,
  $createParagraphNode,
  $createTextNode,
  CLEAR_HISTORY_COMMAND,
  type EditorState,
  type LexicalEditor,
} from 'lexical'

/** 加载：按行拆成段落，空内容留空让 placeholder 显示；加载后清空历史 */
export function textToEditorState(text: string, editor: LexicalEditor) {
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
export function getEditorText(editor: LexicalEditor): string {
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

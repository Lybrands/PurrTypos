import React from 'react'
import type { LexicalEditor } from 'lexical'
import type { EntityId } from '../../types'
import { editorStateToText } from '../EditorPanel/LexicalEditor'
import { findAllMatchStarts, selectLexicalSearchMatch } from '../search/lexicalSearch'

/**
 * 工作区全局搜索：DOM 命中（带 data-ws-search-hit 标记的节点）+ Lexical 正文命中
 * 的统一计数、当前命中高亮 / 滚动定位与上一处/下一处导航。
 *
 * `notifyWorkspaceSearchContentChanged` 由可搜索内容的渲染方在内容变化时调用，
 * 触发重新统计；`searchContentVersion` 同时被字数统计等"内容变更感知"逻辑复用。
 */
export function useWorkspaceSearch({
  lexicalEditorRef,
  activeChapterId,
}: {
  lexicalEditorRef: React.RefObject<LexicalEditor | null>
  activeChapterId: EntityId | null
}) {
  const [workspaceSearchQuery, setWorkspaceSearchQuery] = React.useState('')
  const [workspaceSearchActiveIndex, setWorkspaceSearchActiveIndex] = React.useState(0)
  const [workspaceSearchMatchTotal, setWorkspaceSearchMatchTotal] = React.useState(0)
  const [searchContentVersion, setSearchContentVersion] = React.useState(0)
  const notifyWorkspaceSearchContentChanged = React.useCallback(() => {
    setSearchContentVersion((v) => v + 1)
  }, [])

  React.useEffect(() => {
    setWorkspaceSearchActiveIndex(0)
  }, [workspaceSearchQuery])

  const goToNextWorkspaceSearch = React.useCallback(() => {
    setWorkspaceSearchActiveIndex((i) => {
      const t = Math.max(workspaceSearchMatchTotal, 1)
      return (i + 1) % t
    })
  }, [workspaceSearchMatchTotal])

  const goToPrevWorkspaceSearch = React.useCallback(() => {
    setWorkspaceSearchActiveIndex((i) => {
      const t = Math.max(workspaceSearchMatchTotal, 1)
      return (i - 1 + t) % t
    })
  }, [workspaceSearchMatchTotal])

  React.useEffect(() => {
    if (workspaceSearchMatchTotal > 0 && workspaceSearchActiveIndex >= workspaceSearchMatchTotal) {
      setWorkspaceSearchActiveIndex(0)
    }
  }, [workspaceSearchMatchTotal, workspaceSearchActiveIndex])

  React.useLayoutEffect(() => {
    const q = workspaceSearchQuery.trim()
    document.querySelectorAll('.workspace-search-hit--active').forEach((el) => {
      el.classList.remove('workspace-search-hit--active')
    })
    if (!q) {
      setWorkspaceSearchMatchTotal(0)
      return
    }
    const scope = document.getElementById('workspace-search-scope')
    if (!scope) return
    const domHits = [...scope.querySelectorAll('.workspace-search-include [data-ws-search-hit]')]
    domHits.forEach((el) => el.classList.remove('workspace-search-hit--active'))
    const lex = lexicalEditorRef.current
    let lexCount = 0
    if (lex) {
      const flat = editorStateToText(lex.getEditorState())
      lexCount = findAllMatchStarts(flat, q).length
    }
    const total = domHits.length + lexCount
    setWorkspaceSearchMatchTotal(total)
    if (total === 0) return

    const idx = ((workspaceSearchActiveIndex % total) + total) % total
    if (idx < domHits.length) {
      domHits[idx].classList.add('workspace-search-hit--active')
      domHits[idx].scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    } else if (lex) {
      selectLexicalSearchMatch(lex, q, idx - domHits.length)
    }
  }, [
    workspaceSearchQuery,
    workspaceSearchActiveIndex,
    searchContentVersion,
    activeChapterId,
  ])

  return {
    workspaceSearchQuery,
    setWorkspaceSearchQuery,
    workspaceSearchActiveIndex,
    setWorkspaceSearchActiveIndex,
    workspaceSearchMatchTotal,
    searchContentVersion,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged,
  }
}

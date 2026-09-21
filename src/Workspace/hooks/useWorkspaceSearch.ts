import React from 'react'
import { editorStateToText } from '../EditorPanel/LexicalEditor'
import { findAllMatchStarts, selectLexicalSearchMatch } from '../search/lexicalSearch'
import { lexicalEditorRef } from '../../stores/workspaceStore'
import {
  useSearchActiveIndex,
  useSearchContentVersion,
  useSearchMatchTotal,
  useSearchQuery,
  useWorkspaceStore,
} from '../../stores/workspaceStore'
import { useActiveChapterId } from '../../stores/workspaceStore'

/**
 * 工作区全局搜索：DOM 命中（带 data-ws-search-hit 标记的节点）+ Lexical 正文命中
 * 的统一计数、当前命中高亮 / 滚动定位与上一处/下一处导航。
 *
 * 状态在 workspaceStore 的 search 片（高频字段，窄订阅）；本 hook 只承载
 * 计数/导航副作用，由搜索面板组件挂载——击键与 contentVersion 只重渲染面板自身。
 * `notifyWorkspaceSearchContentChanged`（store action）由可搜索内容的渲染方调用。
 */
export function useWorkspaceSearch() {
  const workspaceSearchQuery = useSearchQuery()
  const workspaceSearchActiveIndex = useSearchActiveIndex()
  const workspaceSearchMatchTotal = useSearchMatchTotal()
  const searchContentVersion = useSearchContentVersion()
  const activeChapterId = useActiveChapterId()

  const goToNextWorkspaceSearch = React.useCallback(() => {
    const { searchActiveIndex, searchMatchTotal, setSearchActiveIndex } =
      useWorkspaceStore.getState()
    const t = Math.max(searchMatchTotal, 1)
    setSearchActiveIndex((searchActiveIndex + 1) % t)
  }, [])

  const goToPrevWorkspaceSearch = React.useCallback(() => {
    const { searchActiveIndex, searchMatchTotal, setSearchActiveIndex } =
      useWorkspaceStore.getState()
    const t = Math.max(searchMatchTotal, 1)
    setSearchActiveIndex((searchActiveIndex - 1 + t) % t)
  }, [])

  React.useEffect(() => {
    if (
      workspaceSearchMatchTotal > 0 &&
      workspaceSearchActiveIndex >= workspaceSearchMatchTotal
    ) {
      useWorkspaceStore.getState().setSearchActiveIndex(0)
    }
  }, [workspaceSearchMatchTotal, workspaceSearchActiveIndex])

  React.useLayoutEffect(() => {
    const q = workspaceSearchQuery.trim()
    document.querySelectorAll('.workspace-search-hit--active').forEach((el) => {
      el.classList.remove('workspace-search-hit--active')
    })
    const { setSearchMatchTotal } = useWorkspaceStore.getState()
    if (!q) {
      setSearchMatchTotal(0)
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
    setSearchMatchTotal(total)
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
    setWorkspaceSearchQuery: (q: string) =>
      useWorkspaceStore.getState().setSearchQuery(q),
    workspaceSearchActiveIndex,
    setWorkspaceSearchActiveIndex: (n: number) =>
      useWorkspaceStore.getState().setSearchActiveIndex(n),
    workspaceSearchMatchTotal,
    searchContentVersion,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    notifyWorkspaceSearchContentChanged: () =>
      useWorkspaceStore.getState().bumpSearchContentVersion(),
  }
}

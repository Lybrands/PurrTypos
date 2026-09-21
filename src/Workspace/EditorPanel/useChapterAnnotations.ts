/**
 * 当前章节批注数据源：加载 + 增删改，订阅 annotationsStore 的章节修订号，
 * 批注弹层保存/更新/删除后自动重载。章节/书籍切换时清空重拉。
 */

import React from 'react'
import { services } from '@/services'
import type { ChapterAnnotation, EntityId } from '../../types'
import { useAnnotationsRevision } from '../../stores/annotationsStore'

export function useChapterAnnotations(bookId: EntityId | null, chapterId: EntityId | null) {
  const [annotations, setAnnotations] = React.useState<ChapterAnnotation[]>([])
  const [loading, setLoading] = React.useState(false)
  const revision = useAnnotationsRevision(bookId, chapterId)

  const reload = React.useCallback(async () => {
    if (bookId == null || chapterId == null) {
      setAnnotations([])
      return
    }
    setLoading(true)
    try {
      const res = await services.annotations.getAnnotationsByBook({ bookId, chapterId })
      setAnnotations(res.success ? res.data ?? [] : [])
    } finally {
      setLoading(false)
    }
  }, [bookId, chapterId])

  React.useEffect(() => {
    void reload()
  }, [reload, revision])

  const updateAnnotation = React.useCallback(
    async (id: number, data: { note?: string; status?: 'open' | 'resolved' }) => {
      if (bookId == null) return false
      const res = await services.annotations.updateAnnotation({ bookId, id, data })
      if (!res.success) return false
      setAnnotations((prev) =>
        prev.map((a) => (a.id === id && res.data ? { ...a, ...res.data } : a)),
      )
      return true
    },
    [bookId],
  )

  const deleteAnnotation = React.useCallback(
    async (id: number) => {
      if (bookId == null) return false
      const res = await services.annotations.deleteAnnotation({ bookId, id })
      if (!res.success) return false
      setAnnotations((prev) => prev.filter((a) => a.id !== id))
      return true
    },
    [bookId],
  )

  return { annotations, loading, reload, updateAnnotation, deleteAnnotation }
}

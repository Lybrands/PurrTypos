import React from 'react'
import type { Chapter, EntityId, Outline, VolumeOutline } from '../../types'
import { getTitleFromXmind, parseXmindToChapters } from '../../utils/outlineXmind'

export type OutlineDeleteModal = {
  title: string
  message: string
  onConfirm: (checked: boolean) => void
  checkboxLabel?: string
}

export interface UseOutlineCrudOptions {
  refreshKey?: number
  onRefreshReady?: (refresh: () => void) => void
  onChapterOutlineDeleted?: (title: string) => void
  displayOutlineId: EntityId | null
  setDisplayChapters: React.Dispatch<React.SetStateAction<Chapter[]>>
  setDisplayOutlineId: React.Dispatch<React.SetStateAction<EntityId | null>>
  setViewMode: React.Dispatch<React.SetStateAction<'list' | 'detail'>>
  bookId?: EntityId | null
  enableVolume?: boolean
}

export function useOutlineCrud(options: UseOutlineCrudOptions) {
  const {
    refreshKey,
    onRefreshReady,
    onChapterOutlineDeleted,
    displayOutlineId,
    setDisplayChapters,
    setDisplayOutlineId,
    setViewMode,
    bookId,
    enableVolume = false,
  } = options

  const [loading, setLoading] = React.useState(false)
  const [loadingType, setLoadingType] = React.useState<'global' | 'chapter' | 'volume' | null>(null)
  const [error, setError] = React.useState('')
  const [globalOutline, setGlobalOutline] = React.useState<Outline | null>(null)
  const [chapterOutlines, setChapterOutlines] = React.useState<Outline[]>([])
  const [volumeOutlines, setVolumeOutlines] = React.useState<VolumeOutline[]>([])
  const [deleteModal, setDeleteModal] = React.useState<OutlineDeleteModal | null>(null)
  const chaptersCache = React.useRef<Record<EntityId, Chapter[]>>({})

  const loadData = React.useCallback(() => {
    window.electronAPI.getGlobalOutline(bookId).then((res) => {
      if (res.success && res.data != null) setGlobalOutline(res.data)
      else setGlobalOutline(null)
    })
    if (enableVolume) {
      window.electronAPI.getVolumeOutlines(bookId).then((res) => {
        if (res.success) setVolumeOutlines(res.data ?? [])
      })
      // 分卷模式下仍加载 chapter outlines（用于 XMind 上传/预览）
      window.electronAPI.getChapterOutlines(bookId).then((res) => {
        if (res.success) setChapterOutlines(res.data ?? [])
      })
    } else {
      window.electronAPI.getChapterOutlines(bookId).then((res) => {
        if (res.success) setChapterOutlines(res.data ?? [])
      })
    }
  }, [bookId, enableVolume])

  React.useEffect(() => {
    loadData()
  }, [loadData, refreshKey])

  React.useEffect(() => {
    onRefreshReady?.(loadData)
  }, [loadData, onRefreshReady])

  // 通用：上传 XMind 并更新指定大纲（volume 或 chapter 或 global）
  const uploadXmindToOutline = React.useCallback(
    async (outline: Outline): Promise<boolean> => {
      const filePath = await window.electronAPI.openXmindFile()
      if (!filePath) return false
      const parseRes = await window.electronAPI.parseXmind(filePath)
      if (!parseRes.success) { setError('解析失败：' + parseRes.error); return false }
      const rootTopic = parseRes.data[0]?.rootTopic
      if (!rootTopic) { setError('未找到根节点，请确认文件格式'); return false }
      const flatChapters = parseXmindToChapters(parseRes)
      const xmindJson = JSON.stringify(parseRes.data)
      const title = getTitleFromXmind(parseRes, filePath) || outline.title
      await window.electronAPI.updateOutline({
        outlineId: outline.id,
        title: outline.title, // 保留原标题
        xmind_data: xmindJson,
        file_path: filePath,
      })
      await window.electronAPI.saveChapters({ outlineId: outline.id, chapters: flatChapters })
      delete chaptersCache.current[outline.id]
      const chapRes = await window.electronAPI.getChapters({ outlineId: outline.id })
      if (chapRes.success && chapRes.data.length > 0) {
        chaptersCache.current[outline.id] = chapRes.data
      }
      if (displayOutlineId === outline.id) {
        setDisplayChapters(flatChapters as Chapter[])
      }
      return true
    },
    [displayOutlineId, setDisplayChapters]
  )

  // 非分卷：传统 handleUploadXmind（全局/章节）
  const handleUploadXmind = React.useCallback(
    async (type: 'global' | 'chapter', forChapterTitle?: string) => {
      setError('')
      setLoading(true)
      setLoadingType(type)
      try {
        const filePath = await window.electronAPI.openXmindFile()
        if (!filePath) return

        const parseRes = await window.electronAPI.parseXmind(filePath)
        if (!parseRes.success) { setError('解析失败：' + parseRes.error); return }
        const rootTopic = parseRes.data[0]?.rootTopic
        if (!rootTopic) { setError('未找到根节点，请确认文件格式'); return }

        const title =
          type === 'chapter'
            ? (forChapterTitle ?? '')
            : getTitleFromXmind(parseRes, filePath)
        const flatChapters = parseXmindToChapters(parseRes)
        const xmindJson = JSON.stringify(parseRes.data)

        if (type === 'global') {
          const outlineRes = await window.electronAPI.saveOutline({
            title, type, xmind_data: xmindJson, file_path: filePath, book_id: bookId ?? null,
          })
          if (!outlineRes.success) { setError('保存大纲失败：' + outlineRes.error); return }
          const saveId = outlineRes.data.id
          await window.electronAPI.saveChapters({ outlineId: saveId, chapters: flatChapters })
          const dbRes = await window.electronAPI.getGlobalOutline(bookId)
          const dbOutline = dbRes.success && dbRes.data ? dbRes.data : { ...outlineRes.data, type }
          const dbId = dbOutline.id
          if (dbId !== saveId && saveId) {
            await window.electronAPI.saveChapters({ outlineId: dbId, chapters: flatChapters })
          }
          const chapRes = await window.electronAPI.getChapters({ outlineId: dbId })
          const chapters = chapRes?.success && Array.isArray(chapRes.data) ? chapRes.data : []
          if (chapters.length > 0) chaptersCache.current[dbId] = chapters
          setGlobalOutline(dbOutline as Outline)
        } else if (type === 'chapter') {
          if (!forChapterTitle) return
          const existingOutline = chapterOutlines.find((o) => o.title === forChapterTitle)
          if (existingOutline && existingOutline.id != null) {
            await window.electronAPI.updateOutline({
              outlineId: existingOutline.id, title: existingOutline.title,
              xmind_data: xmindJson, file_path: filePath,
            })
            await window.electronAPI.saveChapters({ outlineId: existingOutline.id, chapters: flatChapters })
            delete chaptersCache.current[existingOutline.id]
            const chapRes = await window.electronAPI.getChapters({ outlineId: existingOutline.id })
            const chapters = chapRes?.success && Array.isArray(chapRes.data) ? chapRes.data : []
            if (chapters.length > 0) chaptersCache.current[existingOutline.id] = chapters
            setChapterOutlines((prev) =>
              prev.map((o) => o.id === existingOutline.id ? { ...o, xmind_data: xmindJson, file_path: filePath } : o)
            )
          } else {
            setError('未找到对应章节，请先创建写作章节')
          }
        }
        loadData()
      } catch (e) {
        setError('发生错误：' + (e instanceof Error ? e.message : String(e)))
      } finally {
        setLoading(false)
        setLoadingType(null)
      }
    },
    [loadData, chapterOutlines, bookId, setDisplayChapters, setDisplayOutlineId, setViewMode]
  )

  // 分卷：为卷大纲或章节大纲上传 XMind
  const handleUploadXmindForOutline = React.useCallback(
    async (outline: Outline) => {
      setError('')
      setLoading(true)
      setLoadingType(outline.type as 'volume' | 'chapter')
      try {
        const ok = await uploadXmindToOutline(outline)
        if (ok) loadData()
      } catch (e) {
        setError('发生错误：' + (e instanceof Error ? e.message : String(e)))
      } finally {
        setLoading(false)
        setLoadingType(null)
      }
    },
    [uploadXmindToOutline, loadData]
  )

  const handleEdit = React.useCallback(
    async (outline: Outline) => {
      setError('')
      setLoading(true)
      try {
        const ok = await uploadXmindToOutline(outline)
        if (ok) loadData()
      } catch (e) {
        setError('发生错误：' + (e instanceof Error ? e.message : String(e)))
      } finally {
        setLoading(false)
      }
    },
    [uploadXmindToOutline, loadData]
  )

  const handleDelete = React.useCallback(
    async (outline: Outline) => {
      const isChapterOutline = chapterOutlines.some((o) => o.id === outline.id)
      let checkboxLabel: string | undefined
      if (isChapterOutline && outline.writing_chapter_id) {
        checkboxLabel = '同时删除对应写作章节'
      }

      setDeleteModal({
        title: '删除大纲',
        message: `确认删除大纲「${outline.title}」？删除后无法恢复。`,
        checkboxLabel,
        onConfirm: async (checked: boolean) => {
          setDeleteModal(null)
          const res = await window.electronAPI.deleteOutline({ outlineId: outline.id })
          if (res.success) {
            delete chaptersCache.current[outline.id]
            loadData()
            if (displayOutlineId === outline.id) {
              setViewMode('list')
              setDisplayChapters([])
              setDisplayOutlineId(null)
            }
            if (checked && outline.writing_chapter_id) {
              onChapterOutlineDeleted?.(outline.title)
            }
          }
        },
      })
    },
    [loadData, displayOutlineId, onChapterOutlineDeleted, setDisplayChapters, setDisplayOutlineId, setViewMode, chapterOutlines]
  )

  // 删除卷大纲（同时删除卷下所有章节大纲）
  const handleDeleteVolumeOutline = React.useCallback(
    async (vol: VolumeOutline) => {
      setDeleteModal({
        title: '删除卷大纲',
        message: `确认删除「${vol.title}」及其下所有章节大纲？删除后无法恢复。`,
        onConfirm: async () => {
          setDeleteModal(null)
          // 先删章节大纲
          for (const ch of vol.chapters) {
            await window.electronAPI.deleteOutline({ outlineId: ch.id })
            delete chaptersCache.current[ch.id]
          }
          // 再删卷大纲
          await window.electronAPI.deleteOutline({ outlineId: vol.id })
          delete chaptersCache.current[vol.id]
          if (displayOutlineId === vol.id || vol.chapters.some((c) => c.id === displayOutlineId)) {
            setViewMode('list')
            setDisplayChapters([])
            setDisplayOutlineId(null)
          }
          loadData()
        },
      })
    },
    [loadData, displayOutlineId, setViewMode, setDisplayChapters, setDisplayOutlineId]
  )

  const handleDeleteGlobal = React.useCallback(() => {
    if (!globalOutline) return
    const outlineToDelete = globalOutline
    setDeleteModal({
      title: '删除全局大纲',
      message: `确认删除全局大纲「${outlineToDelete.title}」？删除后无法恢复。`,
      onConfirm: async () => {
        setDeleteModal(null)
        const res = await window.electronAPI.deleteOutline({ outlineId: outlineToDelete.id })
        if (res.success) {
          delete chaptersCache.current[outlineToDelete.id]
          loadData()
          setViewMode('list')
          setGlobalOutline(null)
          setDisplayChapters([])
          setDisplayOutlineId(null)
        }
      },
    })
  }, [globalOutline, loadData, setDisplayChapters, setDisplayOutlineId, setViewMode])

  return {
    loadData,
    globalOutline, setGlobalOutline,
    chapterOutlines, setChapterOutlines,
    volumeOutlines, setVolumeOutlines,
    chaptersCache,
    loading, loadingType,
    error, setError,
    deleteModal, setDeleteModal,
    handleUploadXmind,
    handleUploadXmindForOutline,
    handleEdit,
    handleDelete,
    handleDeleteVolumeOutline,
    handleDeleteGlobal,
  }
}

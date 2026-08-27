import { services } from '@/services'
import React from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { PlusIcon, ArrowLeftIcon, DeleteIcon, EditIcon, ExportIcon } from '@/purr-components'
import { PurrButton, PurrCheckbox, PurrInput, PurrModal, PurrTooltip } from '@/purr-components'
import {
  Book,
  type ContinuationCanonPreview,
  type EntityId,
  type NovelSourceRevision,
  type NovelSourceSection,
  type NovelSourceWork,
  type PublishedNovelAnalysis,
} from '../types'
import { useAppFeedback } from '../hooks/useAppFeedback'
import AppHeader from '../components/AppHeader'
import ExportModal, { type ExportFormat } from '../components/ExportModal'
import { fetchExportData, buildExportEntries, buildSingleTxtContent } from '../utils/exportBooks'
import './index.scss'

interface BookshelfPageProps {
  books: Book[]
  lastOpenedBookId: EntityId | null
  onOpenBook: (book: Book) => void
  onCreateBook: (title: string, enableVolume?: boolean) => void
  onDeleteBook: (bookId: EntityId) => void
  onRenameBook: (bookId: EntityId, title: string) => void
  onContinuationCreated: () => void
  onOpenNovelSources: () => void
  onOpenWritingMethods: () => void
  onBack: () => void
}

interface ContinuationSeed {
  workId: string
  revisionId: string
  analysisId: string
}

export default function BookshelfPage({
  books,
  lastOpenedBookId,
  onOpenBook,
  onCreateBook,
  onDeleteBook,
  onRenameBook,
  onContinuationCreated,
  onOpenNovelSources,
  onOpenWritingMethods,
  onBack,
}: BookshelfPageProps) {
  const { message } = useAppFeedback()
  const location = useLocation()
  const navigate = useNavigate()
  const [createModalOpen, setCreateModalOpen] = React.useState(false)
  const [createTitle, setCreateTitle] = React.useState('')
  const [createEnableVolume, setCreateEnableVolume] = React.useState(false)
  const [renameModalOpen, setRenameModalOpen] = React.useState(false)
  const [renameTarget, setRenameTarget] = React.useState<Book | null>(null)
  const [renameTitle, setRenameTitle] = React.useState('')
  const [deleteTarget, setDeleteTarget] = React.useState<Book | null>(null)
  const [exportModalOpen, setExportModalOpen] = React.useState(false)
  const [exportSelectedIds, setExportSelectedIds] = React.useState<EntityId[]>([])
  const [exporting, setExporting] = React.useState(false)
  const [continuationModalOpen, setContinuationModalOpen] = React.useState(false)
  const [continuationSources, setContinuationSources] = React.useState<NovelSourceWork[]>([])
  const [continuationRevisions, setContinuationRevisions] = React.useState<NovelSourceRevision[]>([])
  const [continuationSections, setContinuationSections] = React.useState<NovelSourceSection[]>([])
  const [continuationAnalyses, setContinuationAnalyses] = React.useState<PublishedNovelAnalysis[]>([])
  const [continuationSourceWorkId, setContinuationSourceWorkId] = React.useState('')
  const [continuationRevisionId, setContinuationRevisionId] = React.useState('')
  const [continuationAnalysisId, setContinuationAnalysisId] = React.useState('')
  const [continuationForkSectionId, setContinuationForkSectionId] = React.useState('')
  const [continuationTitle, setContinuationTitle] = React.useState('')
  const [continuationEnableVolume, setContinuationEnableVolume] = React.useState(false)
  const [canonPreview, setCanonPreview] = React.useState<ContinuationCanonPreview | null>(null)
  const [creatingContinuation, setCreatingContinuation] = React.useState(false)

  const originalBooks = books.filter((book) => book.creation_mode !== 'continuation')
  const continuationBooks = books.filter((book) => book.creation_mode === 'continuation')

  const handleCreate = React.useCallback(() => {
    const t = createTitle.trim()
    if (!t) { message.warning('书名不能为空'); return }
    onCreateBook(t, createEnableVolume)
    setCreateTitle('')
    setCreateEnableVolume(false)
    setCreateModalOpen(false)
  }, [createTitle, createEnableVolume, onCreateBook, message])

  const handleRename = React.useCallback(() => {
    if (!renameTarget) return
    const t = renameTitle.trim()
    if (!t) { message.warning('书名不能为空'); return }
    onRenameBook(renameTarget.id, t)
    setRenameModalOpen(false)
    setRenameTarget(null)
  }, [renameTarget, renameTitle, onRenameBook, message])

  const handleDeleteConfirm = React.useCallback(() => {
    if (!deleteTarget) return
    onDeleteBook(deleteTarget.id)
    setDeleteTarget(null)
  }, [deleteTarget, onDeleteBook])

  const openRename = React.useCallback((e: React.MouseEvent, book: Book) => {
    e.stopPropagation()
    setRenameTarget(book)
    setRenameTitle(book.title)
    setRenameModalOpen(true)
  }, [])

  const openDelete = React.useCallback((e: React.MouseEvent, book: Book) => {
    e.stopPropagation()
    setDeleteTarget(book)
  }, [])

  const openExport = React.useCallback(() => {
    setExportSelectedIds([])
    setExportModalOpen(true)
  }, [])

  const chooseContinuationSource = React.useCallback(async (workId: string) => {
    setContinuationSourceWorkId(workId)
    setContinuationRevisionId('')
    setContinuationAnalysisId('')
    setContinuationForkSectionId('')
    setCanonPreview(null)
    if (!workId) return setContinuationRevisions([])
    const result = await services.novelSources.get({ workId })
    if (!result.success || !result.data) return message.error(result.error || '读取来源版本失败')
    setContinuationRevisions(result.data.revisions ?? [])
    setContinuationTitle(`${result.data.title} · 续写`)
  }, [message])

  const chooseContinuationRevision = React.useCallback(async (revisionId: string) => {
    setContinuationRevisionId(revisionId)
    setContinuationAnalysisId('')
    setContinuationForkSectionId('')
    setCanonPreview(null)
    if (!revisionId) return
    const [revision, analyses] = await Promise.all([
      services.novelSources.getRevision({ revisionId }),
      services.novelSources.listPublishedAnalyses({ revisionId }),
    ])
    if (!revision.success || !revision.data) return message.error(revision.error || '读取来源章节失败')
    if (!analyses.success) return message.error(analyses.error || '读取正式分析失败')
    setContinuationSections(revision.data.sections ?? [])
    setContinuationAnalyses(analyses.data ?? [])
  }, [message])

  const openContinuationWizard = React.useCallback(async (seed?: ContinuationSeed) => {
    const result = await services.novelSources.list()
    if (!result.success) return message.error(result.error || '读取来源库失败')
    setContinuationSources(result.data ?? [])
    setContinuationModalOpen(true)
    if (!seed) return
    await chooseContinuationSource(seed.workId)
    await chooseContinuationRevision(seed.revisionId)
    setContinuationAnalysisId(seed.analysisId)
  }, [chooseContinuationRevision, chooseContinuationSource, message])

  const continuationSeed = (location.state as { createContinuationFrom?: ContinuationSeed } | null)?.createContinuationFrom
  const handledContinuationSeed = React.useRef('')
  React.useEffect(() => {
    if (!continuationSeed) return
    const key = `${continuationSeed.workId}:${continuationSeed.revisionId}:${continuationSeed.analysisId}`
    if (handledContinuationSeed.current === key) return
    handledContinuationSeed.current = key
    void openContinuationWizard(continuationSeed)
    navigate(location.pathname, { replace: true, state: null })
  }, [continuationSeed, location.pathname, navigate, openContinuationWizard])

  const handleContinuationPrimary = React.useCallback(async () => {
    if (!continuationTitle.trim() || !continuationRevisionId || !continuationAnalysisId || !continuationForkSectionId) {
      message.warning('请选择来源版本、正式分析和章末分叉点，并填写续写名称')
      return
    }
    setCreatingContinuation(true)
    try {
      if (!canonPreview) {
        const result = await services.continuations.previewCanon({
          sourceRevisionId: continuationRevisionId,
          sourceAnalysisId: continuationAnalysisId,
          forkSectionId: continuationForkSectionId,
        })
        if (!result.success || !result.data) throw new Error(result.error || '正史预览失败')
        setCanonPreview(result.data)
        return
      }
      const result = await services.continuations.create({
        title: continuationTitle.trim(),
        sourceRevisionId: continuationRevisionId,
        sourceAnalysisId: continuationAnalysisId,
        forkSectionId: continuationForkSectionId,
        expectedSnapshotDigest: canonPreview.snapshotDigest,
        enableVolume: continuationEnableVolume,
      })
      if (!result.success) throw new Error(result.error || '创建续写作品失败')
      setContinuationModalOpen(false)
      setCanonPreview(null)
      message.success('续写作品、正史快照和来源绑定已原子创建')
      onContinuationCreated()
    } catch (error) {
      message.error((error as Error).message)
    } finally {
      setCreatingContinuation(false)
    }
  }, [
    canonPreview,
    continuationAnalysisId,
    continuationEnableVolume,
    continuationForkSectionId,
    continuationRevisionId,
    continuationTitle,
    message,
    onContinuationCreated,
  ])

  const renderBookCard = (book: Book) => {
    const isLastOpened = book.id === lastOpenedBookId
    return <div
      key={book.id}
      className={`book-card${isLastOpened ? ' is-last-opened' : ''}`}
    >
      <button
        type="button"
        className="book-card-open"
        aria-label={`打开《${book.title}》${isLastOpened ? '，上次打开' : ''}`}
        onClick={() => onOpenBook(book)}
      >
        <span className="book-cover" style={{ '--book-color': book.cover_color || '#c94361' } as React.CSSProperties}>
          <span className="book-spine" style={{ background: book.cover_color || '#c94361' }} />
          <span className="book-cover-brand">{book.creation_mode === 'continuation' ? 'CONTINUATION' : 'PURR TYPOS'}</span>
          <span className="book-cover-title">{book.title}</span>
          {isLastOpened && <span className="book-last-opened-badge">上次打开</span>}
          <span className="book-cover-mark">✦</span>
        </span>
        {book.creation_mode === 'continuation' ? <small className="book-continuation-source">
          来源：{book.continuation_source_title || '来源作品'} · {book.continuation_fork_section_title || '章末分叉'}
        </small> : null}
      </button>
      <div className="book-actions">
        <PurrTooltip title="重命名">
          <button type="button" aria-label={`重命名《${book.title}》`} className="book-action-btn" onClick={(e) => openRename(e, book)}><EditIcon /></button>
        </PurrTooltip>
        <PurrTooltip title="删除书籍">
          <button type="button" aria-label={`删除《${book.title}》`} className="book-action-btn danger" onClick={(e) => openDelete(e, book)}><DeleteIcon /></button>
        </PurrTooltip>
      </div>
    </div>
  }

  const handleExportConfirm = React.useCallback(
    async (selectedIds: EntityId[], format: ExportFormat, exportAsZip: boolean) => {
      if (selectedIds.length === 0) {
        message.warning('请至少选择一本书籍')
        return
      }
      setExporting(true)
      try {
        // EPUB：逐本书走「后端生成 + 保存对话框」
        if (format === 'epub') {
          let okCount = 0
          for (const bookId of selectedIds) {
            const book = books.find((b) => b.id === bookId)
            const res = await services.exports.exportEpub({
              bookId,
              defaultName: book?.title || '书籍',
            })
            if (res.success) okCount += 1
            else if (res.error === 'canceled') break
            else message.error(`「${book?.title || bookId}」导出失败：${res.error || '未知错误'}`)
          }
          if (okCount > 0) {
            message.success(`已导出 ${okCount} 本 EPUB`)
            setExportModalOpen(false)
          }
          return
        }

        const booksData = await fetchExportData(selectedIds)

        // 整本 TXT：逐本书保存为单文件
        if (format === 'txt-single') {
          let okCount = 0
          for (const bookData of booksData) {
            if (bookData.chapters.length === 0) {
              message.warning(`「${bookData.title}」暂无内容，已跳过`)
              continue
            }
            const res = await services.files.writeSingleTextFile({
              defaultName: `${bookData.title || '导出'}.txt`,
              content: buildSingleTxtContent(bookData),
            })
            if (res.success) okCount += 1
            else if (res.error === 'canceled') break
            else message.error(`「${bookData.title}」导出失败：${res.error || '未知错误'}`)
          }
          if (okCount > 0) {
            message.success(`已导出 ${okCount} 本 TXT`)
            setExportModalOpen(false)
          }
          return
        }

        const entries = buildExportEntries(booksData, format)
        if (entries.length === 0) {
          message.warning('所选书籍暂无内容可导出')
          return
        }
        const res = await services.files.writeExportFiles({ entries, exportAsZip })
        if (res.success) {
          message.success('导出成功')
          setExportModalOpen(false)
        } else {
          if (res.error !== 'canceled') message.error(res.error || '导出失败')
        }
      } finally {
        setExporting(false)
      }
    },
    [message, books]
  )

  if (continuationModalOpen) {
    const closeContinuationWizard = () => {
      setContinuationModalOpen(false)
      setCanonPreview(null)
    }
    return (
      <div className="bookshelf-page continuation-create-page">
        <AppHeader
          title="新建续写作品"
          left={
            <PurrTooltip title="返回书架">
              <PurrButton
                type="text"
                size="small"
                aria-label="返回书架"
                icon={<ArrowLeftIcon style={{ fontSize: 14 }} />}
                onClick={closeContinuationWizard}
              />
            </PurrTooltip>
          }
          showActions
        />
        <main className="continuation-create-main">
          <div className="continuation-create-toolbar">
            <div>
              <span className="bookshelf-eyebrow">CONTINUATION</span>
              <h1>新建续写作品</h1>
              <p>冻结来源版本、章末分叉点和继承正史，再创建独立作品。</p>
            </div>
          </div>
          <section className="continuation-create-panel">
            <div className="continuation-wizard">
              <label>来源作品<select value={continuationSourceWorkId} onChange={(event) => void chooseContinuationSource(event.target.value)}><option value="">请选择</option>{continuationSources.map((work) => <option key={work.id} value={work.id}>{work.title}</option>)}</select></label>
              <label>不可变来源版本<select value={continuationRevisionId} onChange={(event) => void chooseContinuationRevision(event.target.value)}><option value="">请选择</option>{continuationRevisions.map((revision) => <option key={revision.id} value={revision.id}>v{revision.version_no}</option>)}</select></label>
              <label>正式分析<select value={continuationAnalysisId} onChange={(event) => { setContinuationAnalysisId(event.target.value); setCanonPreview(null) }}><option value="">请选择</option>{continuationAnalyses.map((analysis) => <option key={analysis.id} value={analysis.id}>分析 v{analysis.versionNo}</option>)}</select></label>
              <label>章末分叉点<select value={continuationForkSectionId} onChange={(event) => { setContinuationForkSectionId(event.target.value); setCanonPreview(null) }}><option value="">请选择完整章节</option>{continuationSections.map((section) => <option key={section.id} value={section.id}>{section.title}</option>)}</select></label>
              <label>续写作品名称<PurrInput value={continuationTitle} onChange={(event) => setContinuationTitle(event.target.value)} maxLength={50} /></label>
              <PurrCheckbox checked={continuationEnableVolume} onChange={(event) => setContinuationEnableVolume(event.target.checked)}>文章分卷</PurrCheckbox>
              {canonPreview ? <div className="continuation-canon-preview"><strong>继承正史预览</strong><span>{canonPreview.sourceTitle} v{canonPreview.sourceVersionNo} · {canonPreview.forkSectionTitle}末</span><p>{canonPreview.records.length} 条硬事实。风格与技法不会写入正史。</p></div> : null}
            </div>
            <div className="continuation-create-actions">
              <PurrButton onClick={closeContinuationWizard}>取消</PurrButton>
              <PurrButton type="primary" loading={creatingContinuation} onClick={() => void handleContinuationPrimary()}>{canonPreview ? '确认并原子创建' : '预览继承正史'}</PurrButton>
            </div>
          </section>
        </main>
      </div>
    )
  }

  return (
    <div className="bookshelf-page">
      <AppHeader
        title="我的书架"
        left={
          <PurrTooltip title="返回首页">
            <PurrButton
              type="text"
              size="small"
              aria-label="返回首页"
              icon={<ArrowLeftIcon style={{ fontSize: 14 }} />}
              onClick={onBack}
            />
          </PurrTooltip>
        }
        right={
          <PurrTooltip title="导出书籍">
            <PurrButton
              type="text"
              size="small"
              icon={<ExportIcon style={{ fontSize: 16 }} />}
              onClick={openExport}
              className="app-header-action-btn"
            />
          </PurrTooltip>
        }
        showActions
      />

      <section className="bookshelf-main">
        <div className="bookshelf-toolbar">
          <div>
            <span className="bookshelf-eyebrow">YOUR STORIES</span>
            <h1>作品书架</h1>
            <p>{books.length > 0 ? `共 ${books.length} 部作品，挑一本继续创作吧。` : '从一个书名开始，写下你的第一部作品。'}</p>
          </div>
          <div className="bookshelf-toolbar-actions">
            <PurrButton onClick={onOpenNovelSources}>小说来源库</PurrButton>
            <PurrButton onClick={onOpenWritingMethods}>写作方法库</PurrButton>
          </div>
        </div>

        <h2 className="bookshelf-section-title">原创作品</h2>
        <div className="bookshelf-list">
          {originalBooks.map(renderBookCard)}

          <button type="button" className="book-card book-card-add" onClick={() => setCreateModalOpen(true)}>
            <span className="book-card-add-inner purr-entry-surface">
              <span className="book-add-icon"><PlusIcon /></span>
              <strong>新建书籍</strong>
              <small>让一个新故事从这里开始</small>
            </span>
          </button>
        </div>

        <h2 className="bookshelf-section-title continuation">续写作品</h2>
        <div className="bookshelf-list">
          {continuationBooks.map(renderBookCard)}

          <button type="button" className="book-card book-card-add" onClick={() => void openContinuationWizard()}>
            <span className="book-card-add-inner purr-entry-surface">
              <span className="book-add-icon"><PlusIcon /></span>
              <strong>新建续写</strong>
              <small>从原作章末继续故事</small>
            </span>
          </button>
        </div>
      </section>

      {/* 新建书籍 */}
      <PurrModal
        title="新建书籍"
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => { setCreateModalOpen(false); setCreateTitle(''); setCreateEnableVolume(false) }}
        okText="创建"
        cancelText="取消"
      >
        <PurrInput
          placeholder="请输入书名"
          value={createTitle}
          onChange={(e) => setCreateTitle(e.target.value)}
          onPressEnter={handleCreate}
          maxLength={50}
          autoFocus
          style={{ marginBottom: 12 }}
        />
        <PurrCheckbox
          checked={createEnableVolume}
          onChange={(e) => setCreateEnableVolume(e.target.checked)}
        >
          文章分「卷」（创建后不可修改）
        </PurrCheckbox>
      </PurrModal>

      {/* 重命名 */}
      <PurrModal
        title="重命名书籍"
        open={renameModalOpen}
        onOk={handleRename}
        onCancel={() => { setRenameModalOpen(false); setRenameTarget(null) }}
        okText="保存"
        cancelText="取消"
      >
        <PurrInput
          placeholder="请输入新书名"
          value={renameTitle}
          onChange={(e) => setRenameTitle(e.target.value)}
          onPressEnter={handleRename}
          maxLength={50}
          autoFocus
        />
      </PurrModal>

      {/* 删除确认 */}
      <PurrModal
        title="删除书籍"
        open={!!deleteTarget}
        onOk={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
        okText="删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>
          确认删除《{deleteTarget?.title}》？此操作将删除该书的所有章节和大纲，且不可恢复。
        </p>
      </PurrModal>

      <ExportModal
        title="导出书籍"
        open={exportModalOpen}
        onCancel={() => setExportModalOpen(false)}
        items={books.map((b) => ({ id: b.id, title: b.title }))}
        selectedIds={exportSelectedIds}
        onSelectedIdsChange={setExportSelectedIds}
        onConfirm={handleExportConfirm}
        confirmLoading={exporting}
        selectLabel="选择书籍（可多选）："
        emptyText="暂无书籍"
        showFolderHint
      />
    </div>
  )
}

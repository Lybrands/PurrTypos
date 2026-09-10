import { services } from '@/services'
import React from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { InfoIcon, PlusIcon, DeleteIcon, EditIcon, ExportIcon } from '@/purr-components'
import { PurrButton, PurrCheckbox, PurrInput, PurrModal, PurrSelect, PurrTooltip } from '@/purr-components'
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

function ContinuationFieldLabel({ label, help }: { label: string; help: string }) {
  return <span className="continuation-field-label">
    {label}
    <PurrTooltip title={help} placement="top" mouseEnterDelay={0.15}>
      <button type="button" className="continuation-field-info" aria-label={`${label}说明`}>
        <InfoIcon />
      </button>
    </PurrTooltip>
  </span>
}

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
  onOpenSettings: () => void
  onBack: () => void
}

interface ContinuationSeed {
  workId: string
  revisionId?: string
  analysisId?: string
}

interface ContinuationNavigationState {
  createContinuationFrom?: ContinuationSeed
  returnTo?: string
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
  onOpenSettings,
  onBack,
}: BookshelfPageProps) {
  const { message } = useAppFeedback()
  const location = useLocation()
  const navigate = useNavigate()
  const continuationNavigation = location.state as ContinuationNavigationState | null
  const continuationSeed = continuationNavigation?.createContinuationFrom
  const continuationReturnTo = continuationNavigation?.returnTo
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
  const [continuationModalOpen, setContinuationModalOpen] = React.useState(Boolean(continuationSeed))
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
  const [loadingCanon, setLoadingCanon] = React.useState(false)
  const [canonError, setCanonError] = React.useState('')
  const [canonRefresh, setCanonRefresh] = React.useState(0)
  const [useSourceTechniques, setUseSourceTechniques] = React.useState(true)
  const continuationOperation = React.useRef(crypto.randomUUID())
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
    if (continuationSeed && workId !== continuationSeed.workId) return
    setContinuationSourceWorkId(workId)
    setContinuationRevisionId('')
    setContinuationAnalysisId('')
    setContinuationForkSectionId('')
    setContinuationSections([])
    setContinuationAnalyses([])
    setCanonPreview(null)
    if (!workId) return setContinuationRevisions([])
    const result = await services.novelSources.get({ workId })
    if (!result.success || !result.data) return message.error(result.error || '读取来源版本失败')
    setContinuationRevisions(result.data.revisions ?? [])
    setContinuationTitle(`${result.data.title} · 续写`)
  }, [message, continuationSeed?.workId])

  const chooseContinuationRevision = React.useCallback(async (revisionId: string) => {
    setContinuationRevisionId(revisionId)
    setContinuationAnalysisId('')
    setContinuationForkSectionId('')
    setContinuationSections([])
    setContinuationAnalyses([])
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
    setUseSourceTechniques(true)
    setContinuationModalOpen(true)
    if (!seed) return
    await chooseContinuationSource(seed.workId)
    if (seed.revisionId) await chooseContinuationRevision(seed.revisionId)
    if (seed.analysisId) setContinuationAnalysisId(seed.analysisId)
  }, [chooseContinuationRevision, chooseContinuationSource, message])

  const handledContinuationSeed = React.useRef('')
  React.useEffect(() => {
    if (!continuationSeed) return
    const key = `${continuationSeed.workId}:${continuationSeed.revisionId}:${continuationSeed.analysisId}`
    if (handledContinuationSeed.current === key) return
    handledContinuationSeed.current = key
    void openContinuationWizard(continuationSeed)
  }, [continuationSeed, openContinuationWizard])

  React.useEffect(() => {
    let active = true
    setCanonPreview(null)
    setCanonError('')
    setLoadingCanon(false)
    if (!continuationModalOpen || !continuationRevisionId || !continuationAnalysisId || !continuationForkSectionId) return
    setLoadingCanon(true)
    void services.continuations.previewCanon({
      sourceRevisionId: continuationRevisionId,
      sourceAnalysisId: continuationAnalysisId,
      forkSectionId: continuationForkSectionId,
    }).then(result => {
      if (!active) return
      if (!result.success || !result.data) throw new Error(result.error || '读取继承清单失败')
      continuationOperation.current = crypto.randomUUID()
      setCanonPreview(result.data)
    }).catch(error => {
      if (active) setCanonError(error instanceof Error ? error.message : '读取继承清单失败')
    }).finally(() => { if (active) setLoadingCanon(false) })
    return () => { active = false }
  }, [continuationModalOpen, continuationRevisionId, continuationAnalysisId, continuationForkSectionId, canonRefresh])

  const handleContinuationPrimary = React.useCallback(async () => {
    if (!continuationTitle.trim() || !continuationRevisionId || !continuationAnalysisId || !continuationForkSectionId) {
      message.warning('请选择来源版本、正式分析和章末分叉点，并填写续写名称')
      return
    }
    if (!canonPreview || loadingCanon) return
    setCreatingContinuation(true)
    try {
      const result = await services.continuations.create({
        title: continuationTitle.trim(),
        sourceRevisionId: continuationRevisionId,
        sourceAnalysisId: continuationAnalysisId,
        forkSectionId: continuationForkSectionId,
        expectedSnapshotDigest: canonPreview.snapshotDigest,
        operationId: continuationOperation.current,
        useSourceTechniques,
        enableVolume: continuationEnableVolume,
      })
      if (!result.success) {
        if (result.error?.includes('预览已变化')) setCanonRefresh(value => value + 1)
        throw new Error(result.error || '创建续写作品失败')
      }
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
    loadingCanon,
    useSourceTechniques,
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
      if (continuationSeed && continuationReturnTo) navigate(-1)
    }
    return (
      <div className="bookshelf-page continuation-create-page">
        <AppHeader
          title="新建续写作品"
          navigation={{
            home: { label: '返回首页', onClick: onBack },
            back: { label: continuationReturnTo ? '返回上一页' : '返回书架', onClick: closeContinuationWizard },
          }}
          showActions
          onOpenSettings={onOpenSettings}
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
              <div className="continuation-field">
                <span>来源作品</span>
                {continuationSeed ? <strong className="continuation-source-name">{continuationSources.find(work => work.id === continuationSeed.workId)?.title || '正在加载来源作品…'}</strong> :
                  <PurrSelect aria-label="来源作品" value={continuationSourceWorkId || null} placeholder="请选择" options={continuationSources.map(work => ({ value: work.id, label: work.title }))} onChange={value => void chooseContinuationSource(String(value || ''))} />}
              </div>
              <div className="continuation-field"><span>来源版本</span><PurrSelect aria-label="来源版本" value={continuationRevisionId || null} placeholder="请选择" options={continuationRevisions.map(revision => ({ value: revision.id, label: `v${revision.version_no}` }))} onChange={value => void chooseContinuationRevision(String(value || ''))} /></div>
              <div className="continuation-field"><span>正式分析</span><PurrSelect aria-label="正式分析" value={continuationAnalysisId || null} placeholder="请选择" options={continuationAnalyses.map(analysis => ({ value: analysis.id, label: `分析 v${analysis.versionNo}` }))} onChange={value => { setContinuationAnalysisId(String(value || '')); setCanonPreview(null) }} /></div>
              <div className="continuation-field"><ContinuationFieldLabel label="章末分叉点" help="从原作哪一章结束后开始续写。所选章及之前的内容作为历史，之后的原作情节不作为必须遵守的历史。" /><PurrSelect aria-label="章末分叉点" value={continuationForkSectionId || null} placeholder="请选择完整章节" options={continuationSections.map(section => ({ value: section.id, label: section.title }))} onChange={value => { setContinuationForkSectionId(String(value || '')); setCanonPreview(null) }} /></div>
              <label className="continuation-field">续写作品名称<PurrInput value={continuationTitle} onChange={(event) => setContinuationTitle(event.target.value)} maxLength={50} /></label>
              <PurrCheckbox checked={continuationEnableVolume} onChange={(event) => setContinuationEnableVolume(event.target.checked)}>文章分卷</PurrCheckbox>
              {loadingCanon && <p role="status">正在整理继承内容…</p>}
              {canonError && <div role="alert"><span>{canonError}</span><PurrButton type="text" onClick={() => setCanonRefresh(value => value + 1)}>重试</PurrButton></div>}
              {canonPreview ? <div className="continuation-canon-preview"><strong>继承内容</strong><span>{canonPreview.sourceTitle} v{canonPreview.sourceVersionNo} · {canonPreview.forkSectionTitle}末</span><p>{canonPreview.sections.length} 个历史章节 · {canonPreview.records.length} 条有证据事实 · {useSourceTechniques ? canonPreview.defaultTechniques.length : 0} 项默认技法</p>
                <PurrSelect aria-label="续写写作技法" value={useSourceTechniques ? 'source' : 'none'} options={[{ value: 'source', label: '使用来源作品的写作技法' }, { value: 'none', label: '无写作技法' }]} onChange={value => setUseSourceTechniques(value === 'source')} />
                {useSourceTechniques && canonPreview.techniques.map(item => <p key={`${item.ref.id}:${item.ref.versionId}`}>{item.name || '来源分析技法'} · {item.available ? '创建后默认使用' : item.reason || '暂不可用'}</p>)}
                {useSourceTechniques && !canonPreview.defaultTechniques.length && <p>暂无可继承技法，可直接创建，稍后再添加。</p>}
                </div> : null}
            </div>
            <div className="continuation-create-actions">
              <PurrButton onClick={closeContinuationWizard}>取消</PurrButton>
              <PurrButton type="primary" disabled={!canonPreview || loadingCanon || !continuationTitle.trim()} loading={creatingContinuation} onClick={() => void handleContinuationPrimary()}>创建续写作品</PurrButton>
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
        navigation={{
          home: { label: '返回首页', onClick: onBack },
          back: {
            label: '返回上一页',
            onClick: () => continuationReturnTo ? navigate(-1) : onBack(),
          },
        }}
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
        onOpenSettings={onOpenSettings}
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
            <PurrButton onClick={onOpenWritingMethods}>写作技法库</PurrButton>
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

import { runtimeCapabilities, services } from '@/services'
import React from 'react'
import { useAppFeedback } from '../hooks/useAppFeedback'

export type DatabaseInfo = {
  dbPath: string
  books: number
  outlineChapters: number
  articles: number
}

export function useDatabaseActions(active: boolean) {
  const { message } = useAppFeedback()
  const [exportingDb, setExportingDb] = React.useState(false)
  const [importingDb, setImportingDb] = React.useState(false)
  const [openingDbDir, setOpeningDbDir] = React.useState(false)
  const [dbInfoLoading, setDbInfoLoading] = React.useState(false)
  const [dbInfo, setDbInfo] = React.useState<DatabaseInfo | null>(null)

  const refreshDbInfo = React.useCallback(async () => {
    setDbInfoLoading(true)
    try {
      const res = await services.database.getDatabaseInfo()
      setDbInfo(res.success && res.data ? res.data : null)
    } finally {
      setDbInfoLoading(false)
    }
  }, [])

  React.useEffect(() => {
    if (!active) return
    void refreshDbInfo()
  }, [active, refreshDbInfo])

  const handleExportDatabase = React.useCallback(async () => {
    setExportingDb(true)
    try {
      const res = await services.database.exportDatabase()
      if (res.success) {
        message.success('完整项目备份已导出（不包含 API 密钥）')
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导出失败')
      }
    } finally {
      setExportingDb(false)
    }
  }, [message])

  const handleImportDatabase = React.useCallback(async () => {
    const confirmed = window.confirm(
      '恢复将使用所选完整备份覆盖当前作品、运行记录和记忆组件数据；本机 API 密钥不会从备份导入。是否继续？'
    )
    if (!confirmed) return
    setImportingDb(true)
    try {
      const res = await services.database.importDatabase()
      if (res.success) {
        const before = res.data?.beforeStats
        const after = res.data?.afterStats
        if (runtimeCapabilities.runtime === 'browser' && res.data?.restartRequired) {
          message.success('完整备份已恢复。请重启后端服务后再继续使用记忆功能。')
        } else if (before && after) {
          message.success(
            `数据库已导入：章节 ${before.outlineChapters} -> ${after.outlineChapters}，正文 ${before.articles} -> ${after.articles}。正在刷新...`
          )
        } else {
          message.success('数据库已导入，正在刷新...')
        }
        if (runtimeCapabilities.runtime === 'browser' && !res.data?.restartRequired) {
          window.setTimeout(() => window.location.reload(), 500)
        }
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导入失败')
      }
    } finally {
      setImportingDb(false)
    }
  }, [message])

  const handleOpenDbDir = React.useCallback(async () => {
    setOpeningDbDir(true)
    try {
      const res = await services.database.openDatabaseDirectory()
      if (!res.success) {
        message.error(res.error || '打开目录失败')
        return
      }
      message.success('已打开数据库目录')
    } catch {
      message.error('打开目录失败')
    } finally {
      setOpeningDbDir(false)
    }
  }, [message])

  return {
    dbInfo,
    dbInfoLoading,
    exportingDb,
    importingDb,
    openingDbDir,
    refreshDbInfo,
    handleExportDatabase,
    handleImportDatabase,
    handleOpenDbDir,
    canOpenDbDir: runtimeCapabilities.openDatabaseDirectory,
  }
}

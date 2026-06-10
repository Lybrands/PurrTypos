import React from 'react'
import { useAntdApp } from '../hooks/useAntdApp'

export type DatabaseInfo = {
  dbPath: string
  books: number
  outlineChapters: number
  articles: number
}

export function useDatabaseActions(active: boolean) {
  const { message } = useAntdApp()
  const [exportingDb, setExportingDb] = React.useState(false)
  const [importingDb, setImportingDb] = React.useState(false)
  const [dbInfoLoading, setDbInfoLoading] = React.useState(false)
  const [dbInfo, setDbInfo] = React.useState<DatabaseInfo | null>(null)

  const refreshDbInfo = React.useCallback(async () => {
    setDbInfoLoading(true)
    try {
      const res = await window.electronAPI.getDatabaseInfo()
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
      const res = await window.electronAPI.exportDatabase()
      if (res.success) {
        message.success('数据库已导出')
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导出失败')
      }
    } finally {
      setExportingDb(false)
    }
  }, [message])

  const handleImportDatabase = React.useCallback(async () => {
    const confirmed = window.confirm(
      '导入将使用所选备份文件覆盖当前全部数据，完成后将自动刷新页面。是否继续？'
    )
    if (!confirmed) return
    setImportingDb(true)
    try {
      const res = await window.electronAPI.importDatabase()
      if (res.success) {
        const before = res.data?.beforeStats
        const after = res.data?.afterStats
        if (before && after) {
          message.success(
            `数据库已导入：章节 ${before.outlineChapters} -> ${after.outlineChapters}，正文 ${before.articles} -> ${after.articles}。正在刷新...`
          )
        } else {
          message.success('数据库已导入，正在刷新...')
        }
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导入失败')
      }
    } finally {
      setImportingDb(false)
    }
  }, [message])

  const handleOpenDbDir = React.useCallback(async () => {
    const res = await window.electronAPI.openDatabaseDirectory()
    if (!res.success) {
      message.error(res.error || '打开目录失败')
      return
    }
    message.success('已打开数据库目录')
  }, [message])

  return {
    dbInfo,
    dbInfoLoading,
    exportingDb,
    importingDb,
    refreshDbInfo,
    handleExportDatabase,
    handleImportDatabase,
    handleOpenDbDir,
  }
}

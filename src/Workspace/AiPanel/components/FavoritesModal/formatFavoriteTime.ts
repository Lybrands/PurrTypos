/** SQLite 存的是 UTC，转成本地时间再格式化为 YYYY-MM-DD HH:mm */
export function formatFavoriteTime(createTime?: string): string {
  if (!createTime) return ''
  const str = createTime.replace(' ', 'T') + 'Z'
  const date = new Date(str)
  if (isNaN(date.getTime())) return createTime
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const h = String(date.getHours()).padStart(2, '0')
  const min = String(date.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${d} ${h}:${min}`
}

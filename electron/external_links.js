// Keep research-source navigation outside the application renderer.
function externalWebUrl(value) {
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}

function configureExternalLinks(window, shell) {
  const open = (value) => {
    const url = externalWebUrl(value)
    if (url) void shell.openExternal(url).catch(() => {})
  }
  window.webContents.setWindowOpenHandler(({ url }) => {
    open(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    let current, next
    try { current = new URL(window.webContents.getURL()); next = new URL(url) } catch { event.preventDefault(); return }
    if (next.protocol === current.protocol && next.host === current.host) return
    event.preventDefault()
    open(url)
  })
}

module.exports = { configureExternalLinks, externalWebUrl }

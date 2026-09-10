import React from 'react'

export function useMaterialRefresh(load: () => Promise<void>, paused = false) {
  React.useEffect(() => {
    const refresh = () => { if (!paused && document.visibilityState === 'visible') void load() }
    window.addEventListener('focus', refresh)
    const timer = window.setInterval(refresh, 5000)
    return () => { window.removeEventListener('focus', refresh); window.clearInterval(timer) }
  }, [load, paused])
}

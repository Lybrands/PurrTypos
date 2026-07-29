import React from 'react'

const STORAGE_KEY = 'purrtypos_font_size'

export type FontSizeLevel = 'small' | 'medium' | 'large'

interface FontSizeContextValue {
  fontSize: FontSizeLevel
  setFontSize: (level: FontSizeLevel) => void
}

const FontSizeContext = React.createContext<FontSizeContextValue | null>(null)

const LEVELS: FontSizeLevel[] = ['small', 'medium', 'large']

function getStoredFontSize(): FontSizeLevel {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'small' || stored === 'medium' || stored === 'large') return stored
  } catch {}
  return 'medium'
}

function applyFontSize(level: FontSizeLevel) {
  document.documentElement.setAttribute('data-font-size', level)
}

export function FontSizeProvider({ children }: { children: React.ReactNode }) {
  const [fontSize, setFontSizeState] = React.useState<FontSizeLevel>(() => {
    const stored = getStoredFontSize()
    applyFontSize(stored)
    return stored
  })

  React.useEffect(() => {
    applyFontSize(fontSize)
    localStorage.setItem(STORAGE_KEY, fontSize)
  }, [fontSize])

  const setFontSize = React.useCallback((level: FontSizeLevel) => {
    setFontSizeState(level)
  }, [])

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return

      const increase =
        event.key === '+' ||
        event.key === '=' ||
        event.code === 'NumpadAdd'
      const decrease =
        event.key === '-' ||
        event.key === '_' ||
        event.code === 'NumpadSubtract'
      const reset = event.key === '0' || event.code === 'Numpad0'

      if (!increase && !decrease && !reset) return

      event.preventDefault()
      event.stopPropagation()

      if (reset) {
        setFontSizeState('medium')
        return
      }

      const direction = increase ? 1 : -1
      setFontSizeState((current) => {
        const currentIndex = LEVELS.indexOf(current)
        const nextIndex = Math.min(LEVELS.length - 1, Math.max(0, currentIndex + direction))
        return LEVELS[nextIndex]
      })
    }

    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [])

  const value = React.useMemo(
    () => ({ fontSize, setFontSize }),
    [fontSize, setFontSize]
  )

  return <FontSizeContext.Provider value={value}>{children}</FontSizeContext.Provider>
}

export function useFontSize() {
  const ctx = React.useContext(FontSizeContext)
  if (!ctx) throw new Error('useFontSize must be used within FontSizeProvider')
  return ctx
}

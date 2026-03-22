import React from 'react'

const STORAGE_KEY = 'purrtypos_font_size'

export type FontSizeLevel = 'small' | 'medium' | 'large'

interface FontSizeContextValue {
  fontSize: FontSizeLevel
  setFontSize: (level: FontSizeLevel) => void
  cycleFontSize: () => void
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

  const cycleFontSize = React.useCallback(() => {
    setFontSizeState((prev) => {
      const idx = LEVELS.indexOf(prev)
      return LEVELS[(idx + 1) % LEVELS.length]
    })
  }, [])

  const value = React.useMemo(
    () => ({ fontSize, setFontSize, cycleFontSize }),
    [fontSize, setFontSize, cycleFontSize]
  )

  return <FontSizeContext.Provider value={value}>{children}</FontSizeContext.Provider>
}

export function useFontSize() {
  const ctx = React.useContext(FontSizeContext)
  if (!ctx) throw new Error('useFontSize must be used within FontSizeProvider')
  return ctx
}

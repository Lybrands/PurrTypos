/// <reference path="../../vite-env.d.ts" />
import React from 'react'
import type { Chapter } from '../../types'
import { chaptersToMindMapData, convertXmindJson } from '../../utils/mindMapData'
import { useTheme } from '../../contexts/ThemeContext'
import 'simple-mind-map/dist/simpleMindMap.esm.min.css'

export interface MindMapViewProps {
  chapters: Chapter[]
  rootTitle?: string
  xmindData?: string | null
  filePath?: string | null
}

let smmPluginRegistered = false

export default function MindMapView({ chapters, rootTitle, xmindData }: MindMapViewProps) {
  const { theme } = useTheme()
  const containerRef = React.useRef<HTMLDivElement>(null)
  const mindMapRef = React.useRef<any>(null)
  const isDark = theme === 'dark'

  const mindMapData = React.useMemo(() => {
    if (xmindData) {
      try {
        const tree = convertXmindJson(xmindData)
        if (tree) return tree
      } catch (err) {
        console.error('[MindMapView] xmind convert error:', err)
      }
    }
    if (chapters && chapters.length > 0) {
      return chaptersToMindMapData(chapters, rootTitle || '大纲')
    }
    return null
  }, [xmindData, chapters, rootTitle])

  React.useEffect(() => {
    if (!containerRef.current || !mindMapData) return

    let destroyed = false
    let resizeObs: ResizeObserver | null = null
    let fitTimer: ReturnType<typeof setTimeout> | null = null

    const waitForContainerReady = async (el: HTMLDivElement): Promise<boolean> =>
      new Promise((resolve) => {
        const hasSize = () => {
          const rect = el.getBoundingClientRect()
          return rect.width > 0 && rect.height > 0
        }
        if (hasSize()) {
          resolve(true)
          return
        }
        if (typeof ResizeObserver === 'undefined') {
          const tick = () => {
            if (destroyed) {
              resolve(false)
              return
            }
            if (hasSize()) {
              resolve(true)
              return
            }
            requestAnimationFrame(tick)
          }
          requestAnimationFrame(tick)
          return
        }
        const ro = new ResizeObserver(() => {
          if (destroyed) {
            ro.disconnect()
            resolve(false)
            return
          }
          if (hasSize()) {
            ro.disconnect()
            resolve(true)
          }
        })
        ro.observe(el)
      })

    const init = async () => {
      const MindMapModule = await import('simple-mind-map')
      const MindMap = MindMapModule.default

      if (!smmPluginRegistered) {
        try {
          const AssociativeLineModule = await import('simple-mind-map/src/plugins/AssociativeLine.js')
          MindMap.usePlugin(AssociativeLineModule.default)
        } catch (err) {
          console.warn('[MindMapView] AssociativeLine plugin load failed:', err)
        }
        smmPluginRegistered = true
      }

      if (destroyed || !containerRef.current) return

      if (mindMapRef.current) {
        try {
          mindMapRef.current.destroy()
        } catch (_) {}
        mindMapRef.current = null
      }
      containerRef.current.innerHTML = ''
      const ready = await waitForContainerReady(containerRef.current)
      if (!ready || destroyed || !containerRef.current) return

      try {
        const tokens = getComputedStyle(document.documentElement)
        const token = (name: string, fallback: string) => tokens.getPropertyValue(name).trim() || fallback
        const accent = token('--accent', '#c94361')
        const accentDim = token('--accent-dim', 'rgba(201, 67, 97, 0.17)')
        const textPrimary = token('--text-primary', isDark ? '#f6f0eb' : '#292421')
        const textSecondary = token('--text-secondary', isDark ? '#c5bab3' : '#625a55')
        const warning = token('--warning', '#b87416')
        const instance = new MindMap({
          el: containerRef.current,
          data: mindMapData,
          readonly: true,
          layout: 'logicalStructure',
          theme: 'classic4',
          themeConfig: {
            backgroundColor: 'transparent',
            lineColor: accent,
            lineWidth: 2,
            generalizationLineWidth: 2,
            associativeLineColor: warning,
            associativeLineWidth: 2,
            associativeLineDasharray: '6,4',
            associativeLineActiveWidth: 8,
            associativeLineActiveColor: warning,
            associativeLineTextColor: warning,
            associativeLineTextFontSize: 14,
            associativeLineTextFontFamily: 'inherit',
            root: {
              fillColor: accentDim,
              color: textPrimary,
              borderColor: accent,
              borderWidth: 2,
              fontSize: 16,
              fontWeight: 'bold',
            },
            second: {
              fillColor: `color-mix(in srgb, ${accent} 9%, transparent)`,
              color: textSecondary,
              borderColor: accent,
              borderWidth: 1,
              fontSize: 14,
            },
            node: {
              fillColor: `color-mix(in srgb, ${accent} 6%, transparent)`,
              color: textSecondary,
              borderColor: `color-mix(in srgb, ${accent} 48%, transparent)`,
              borderWidth: 1,
              fontSize: 12,
            },
          },
          enableFreeDrag: false,
          initRootNodePosition: ['left', 'center'],
          nodeTextEditZIndex: 1000,
          expandBtnSize: 16,
          customNoteContentShow: {
            show(content: string, left: number, top: number) {
              let el = document.getElementById('smm-note-tooltip')
              if (!el) {
                el = document.createElement('div')
                el.id = 'smm-note-tooltip'
                el.style.cssText = `
                  position: fixed;
                  padding: 10px 14px;
                  border-radius: 6px;
                  background: #1e293b;
                  color: #e2e8f0;
                  font-size: 13px;
                  line-height: 1.5;
                  max-width: 320px;
                  white-space: pre-wrap;
                  word-break: break-word;
                  box-shadow: 0 4px 16px rgba(0,0,0,0.4);
                  border: 1px solid #334155;
                  z-index: 9999;
                  display: none;
                  pointer-events: none;
                `
                document.body.appendChild(el)
              }
              el.innerText = content
              el.style.left = left + 'px'
              el.style.top = top + 'px'
              el.style.display = 'block'
            },
            hide() {
              const el = document.getElementById('smm-note-tooltip')
              if (el) el.style.display = 'none'
            },
          },
        })
        mindMapRef.current = instance

        fitTimer = setTimeout(() => {
          if (!destroyed && mindMapRef.current) {
            try {
              mindMapRef.current.view.fit()
            } catch (_) {}
          }
        }, 300)
        if (typeof ResizeObserver !== 'undefined') {
          resizeObs = new ResizeObserver(() => {
            if (!mindMapRef.current) return
            try {
              mindMapRef.current.view.fit()
            } catch (_) {}
          })
          resizeObs.observe(containerRef.current)
        }
      } catch (err) {
        console.error('[MindMapView] init error:', err)
      }
    }

    init()

    return () => {
      destroyed = true
      if (fitTimer) {
        clearTimeout(fitTimer)
        fitTimer = null
      }
      if (resizeObs) {
        resizeObs.disconnect()
        resizeObs = null
      }
      if (mindMapRef.current) {
        try {
          mindMapRef.current.destroy()
        } catch (_) {}
        mindMapRef.current = null
      }
    }
  }, [mindMapData, isDark])

  if (!mindMapData) return null

  return <div ref={containerRef} className="mindmap-container" />
}

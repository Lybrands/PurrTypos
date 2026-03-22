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

const ACCENT = '#1677ff'
const LIGHT_TEXT = 'rgba(0, 0, 0, 0.88)'
const LIGHT_TEXT_SEC = 'rgba(0, 0, 0, 0.65)'
const DARK_TEXT = 'rgba(255, 255, 255, 0.85)'
const DARK_TEXT_SEC = 'rgba(255, 255, 255, 0.65)'

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

      try {
        const instance = new MindMap({
          el: containerRef.current,
          data: mindMapData,
          readonly: true,
          layout: 'logicalStructure',
          theme: 'classic4',
          themeConfig: {
            backgroundColor: 'transparent',
            lineColor: ACCENT,
            lineWidth: 2,
            generalizationLineWidth: 2,
            associativeLineColor: '#ff9800',
            associativeLineWidth: 2,
            associativeLineDasharray: '6,4',
            associativeLineActiveWidth: 8,
            associativeLineActiveColor: 'rgba(255, 152, 0, 0.6)',
            associativeLineTextColor: '#ffb74d',
            associativeLineTextFontSize: 14,
            associativeLineTextFontFamily: 'inherit',
            root: {
              fillColor: isDark ? 'rgba(64, 150, 255, 0.15)' : 'rgba(22, 119, 255, 0.12)',
              color: isDark ? DARK_TEXT : LIGHT_TEXT,
              borderColor: ACCENT,
              borderWidth: 2,
              fontSize: 16,
              fontWeight: 'bold',
            },
            second: {
              fillColor: isDark ? 'rgba(64, 150, 255, 0.08)' : 'rgba(22, 119, 255, 0.06)',
              color: isDark ? DARK_TEXT_SEC : LIGHT_TEXT_SEC,
              borderColor: ACCENT,
              borderWidth: 1,
              fontSize: 14,
            },
            node: {
              fillColor: isDark ? 'rgba(64, 150, 255, 0.06)' : 'rgba(22, 119, 255, 0.04)',
              color: isDark ? DARK_TEXT_SEC : LIGHT_TEXT_SEC,
              borderColor: isDark ? 'rgba(64, 150, 255, 0.5)' : 'rgba(22, 119, 255, 0.4)',
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

        setTimeout(() => {
          if (!destroyed && mindMapRef.current) {
            try {
              mindMapRef.current.view.fit()
            } catch (_) {}
          }
        }, 300)
      } catch (err) {
        console.error('[MindMapView] init error:', err)
      }
    }

    init()

    return () => {
      destroyed = true
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

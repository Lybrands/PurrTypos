import React from 'react'

// Keep measured space when expensive descendants leave the scroll viewport.
const heights = new Map<string, number>()
const rememberHeight = (key: string, height: number) => {
  heights.delete(key)
  heights.set(key, height)
  if (heights.size > 2000) heights.delete(heights.keys().next().value!)
}

export default function ViewportBlock({ id, children, estimate = 160, keepMounted = false }: {
  id: string
  children: React.ReactNode
  keepMounted?: boolean
  estimate?: number
}) {
  const ref = React.useRef<HTMLDivElement>(null)
  const [visible, setVisible] = React.useState(typeof IntersectionObserver === 'undefined')
  const mounted = React.useRef(visible)
  if (visible) mounted.current = true
  const render = visible || (keepMounted && mounted.current)
  const height = React.useRef(heights.get(id) ?? estimate)
  React.useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    const root = node.closest('.agent-conversation, .ai-dev-inspector__body')
    const observer = new IntersectionObserver(([entry]) => {
      // Keep keyboard focus and text selection alive during scrolling.
      const selection = node.ownerDocument.getSelection()
      const retained = node.contains(node.ownerDocument.activeElement)
        || Boolean(selection?.anchorNode && !selection.isCollapsed && node.contains(selection.anchorNode))
      setVisible(entry.isIntersecting || retained)
    }, { root, rootMargin: '600px 0px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [id])
  React.useLayoutEffect(() => {
    const node = ref.current
    if (!node || !visible || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      const measured = node.getBoundingClientRect().height
      if (measured > 0) {
        height.current = measured
        rememberHeight(id, measured)
      }
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [id, visible])
  return <div ref={ref} data-viewport-block={id}
    style={render ? { display: 'flow-root', contentVisibility: 'auto', containIntrinsicSize: `auto ${height.current}px` } : { height: height.current }}>
    {render ? children : null}
  </div>
}

import React from 'react'

export function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export interface HighlightTextProps {
  text: string
  query: string
}

/** 纯文本内高亮关键字，命中带 data-ws-search-hit 供全局跳转 */
export function HighlightText({ text, query }: HighlightTextProps) {
  const q = query.trim()
  if (!q) return <>{text}</>
  try {
    const re = new RegExp(`(${escapeRegExp(q)})`, 'gi')
    const parts = text.split(re)
    return (
      <>
        {parts.map((part, i) => {
          const isHit = part.toLowerCase() === q.toLowerCase()
          if (!isHit) return <React.Fragment key={i}>{part}</React.Fragment>
          return (
            <mark key={i} className="workspace-search-hit" data-ws-search-hit>
              {part}
            </mark>
          )
        })}
      </>
    )
  } catch {
    return <>{text}</>
  }
}

/** 递归处理 React children，对字符串做高亮 */
export function highlightNodeChildren(children: React.ReactNode, query: string): React.ReactNode {
  const q = query.trim()
  if (!q) return children
  return React.Children.map(children, (child, i) => {
    if (typeof child === 'string' || typeof child === 'number') {
      return <HighlightText key={i} text={String(child)} query={query} />
    }
    if (React.isValidElement(child) && child.props && (child.props as { children?: React.ReactNode }).children != null) {
      const props = child.props as { children?: React.ReactNode }
      return React.cloneElement(child, {
        ...props,
        key: child.key ?? i,
        children: highlightNodeChildren(props.children, query),
      } as Record<string, unknown>)
    }
    return child
  })
}

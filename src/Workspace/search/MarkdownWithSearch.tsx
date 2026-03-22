import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { highlightNodeChildren } from './highlightText'

function wrapWithHighlight(query: string, Tag: keyof JSX.IntrinsicElements) {
  function MdTag({ children, ...rest }: React.HTMLAttributes<HTMLElement>) {
    return React.createElement(Tag, rest, highlightNodeChildren(children, query))
  }
  return MdTag
}

function buildComponents(query: string): Components {
  const tags: (keyof JSX.IntrinsicElements)[] = [
    'p', 'li', 'td', 'th', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'strong', 'em', 'span', 'a', 'del', 'table', 'thead', 'tbody', 'tr', 'ul', 'ol',
  ]
  const c: Partial<Components> = {
    code: (props) => {
      const p = props as React.HTMLAttributes<HTMLElement> & { inline?: boolean; children?: React.ReactNode }
      const { inline, children, className, ...rest } = p
      if (inline) {
        return (
          <code className={className} {...rest}>
            {highlightNodeChildren(children, query)}
          </code>
        )
      }
      return (
        <code className={className} {...rest}>
          {children}
        </code>
      )
    },
    pre: ({ children, ...props }) => <pre {...props}>{children}</pre>,
  }
  const bucket = c as Record<string, unknown>
  for (const t of tags) {
    bucket[t] = wrapWithHighlight(query, t)
  }
  return c as Components
}

interface MarkdownWithSearchProps {
  content: string
  searchQuery: string
  className?: string
}

/** ReactMarkdown + 关键字高亮（查看态） */
export default function MarkdownWithSearch({ content, searchQuery, className }: MarkdownWithSearchProps) {
  const components = React.useMemo(() => buildComponents(searchQuery), [searchQuery])
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content || ''}
      </ReactMarkdown>
    </div>
  )
}

import React from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import "./index.scss";

const REMARK_PLUGINS = [remarkGfm];
const MARKDOWN_COMPONENTS: Components = {
  table: ({ children }) => (
    <div className="agent-markdown__table-scroll">
      <table>{children}</table>
    </div>
  ),
};

export interface MarkdownProps {
  children: string;
  className?: string;
}

function MarkdownInner({ children, className }: MarkdownProps) {
  return (
    <div className={['agent-markdown', className || ''].filter(Boolean).join(' ')}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        components={MARKDOWN_COMPONENTS}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export default React.memo(
  MarkdownInner,
  (prev, next) =>
    prev.children === next.children && prev.className === next.className,
);

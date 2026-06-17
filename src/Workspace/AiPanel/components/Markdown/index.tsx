import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const REMARK_PLUGINS = [remarkGfm];

export interface MarkdownProps {
  children: string;
  className?: string;
}

function MarkdownInner({ children, className }: MarkdownProps) {
  const md = (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS}>{children}</ReactMarkdown>
  );
  if (className) {
    return <div className={className}>{md}</div>;
  }
  return md;
}

export default React.memo(
  MarkdownInner,
  (prev, next) =>
    prev.children === next.children && prev.className === next.className,
);

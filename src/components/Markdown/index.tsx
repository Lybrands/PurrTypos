import React from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { yamlFrontmatterToMarkdown } from "@/utils/markdown";
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
  preserveSoftBreaks?: boolean;
  yamlFrontmatter?: boolean;
}

function MarkdownInner({
  children,
  className,
  preserveSoftBreaks = false,
  yamlFrontmatter = false,
}: MarkdownProps) {
  return (
    <div className={[
      'agent-markdown',
      preserveSoftBreaks ? 'agent-markdown--preserve-soft-breaks' : '',
      className || '',
    ].filter(Boolean).join(' ')}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        components={MARKDOWN_COMPONENTS}
      >
        {yamlFrontmatter ? yamlFrontmatterToMarkdown(children) : children}
      </ReactMarkdown>
    </div>
  );
}

export default React.memo(
  MarkdownInner,
  (prev, next) =>
    prev.children === next.children
    && prev.className === next.className
    && prev.preserveSoftBreaks === next.preserveSoftBreaks
    && prev.yamlFrontmatter === next.yamlFrontmatter,
);

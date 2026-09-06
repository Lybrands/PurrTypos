import React from 'react';

export function characterCount(text: string): number {
  return Array.from(text).length;
}

export default function DiagnosticText({ label, text, characters, truncated = false }: {
  label: React.ReactNode;
  text: string;
  characters?: number;
  truncated?: boolean;
}) {
  const shown = characterCount(text);
  return <details className="ai-dev-inspector__text-block">
    <summary>{label} <small>{(characters ?? shown).toLocaleString()} 字符{truncated ? ` · 预览 ${shown.toLocaleString()} 字符` : ''}</small></summary>
    <pre>{text}</pre>
    {truncated ? <div className="ai-dev-inspector__notice">预览已截断；原始日志未修改。</div> : null}
  </details>;
}

import React from 'react';
import { Virtuoso } from 'react-virtuoso';

export function characterCount(text: string): number {
  let count = 0;
  for (const _character of text) count++;
  return count;
}

function formatValue(value: unknown): string {
  if (value === undefined) return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function TextBody({ text, value, characters, truncated }: {
  text?: string; value?: unknown; characters?: number; truncated: boolean;
}) {
  const formatted = React.useMemo(() => text ?? formatValue(value), [text, value]);
  const shown = React.useMemo(() => characterCount(formatted), [formatted]);
  const chunks = React.useMemo(() => {
    const result: string[] = [];
    for (let start = 0; start < formatted.length;) {
      let end = Math.min(start + 4000, formatted.length);
      const last = formatted.charCodeAt(end - 1);
      if (last >= 0xd800 && last <= 0xdbff && end < formatted.length) end--;
      result.push(formatted.slice(start, end));
      start = end;
    }
    return result;
  }, [formatted]);
  return <>
    <small>{(characters ?? shown).toLocaleString()} 字符{truncated ? ` · 预览 ${shown.toLocaleString()} 字符` : ''}</small>
    {chunks.length > 1 ? <Virtuoso style={{ height: 320 }} data={chunks}
      increaseViewportBy={240}
      itemContent={(_index, chunk) => <pre style={{ margin: 0, maxHeight: 'none', overflow: 'visible' }}>{chunk}</pre>}
    /> : <pre>{formatted}</pre>}
    {truncated ? <div className="ai-dev-inspector__notice">预览已截断；原始日志未修改。</div> : null}
  </>;
}

export default React.memo(function DiagnosticText({ label, text, value, characters, truncated = false }: {
  label: React.ReactNode;
  text?: string;
  value?: unknown;
  characters?: number;
  truncated?: boolean;
}) {
  const [opened, setOpened] = React.useState(false);
  return <details className="ai-dev-inspector__text-block"
    onToggle={event => setOpened(event.currentTarget.open)}>
    <summary>{label}{characters != null ? <small> {characters.toLocaleString()} 字符</small> : null}</summary>
    {opened ? <TextBody text={text} value={value} characters={characters} truncated={truncated} /> : null}
  </details>;
});

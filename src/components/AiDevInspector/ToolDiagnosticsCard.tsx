import React from 'react';
import { services } from '../../services';
import type { AiDiagnosticPreview, AiToolCallDiagnostic } from '../../types';
import { mergeToolDiagnostics } from './toolDiagnostics';
import { groupToolPresentation } from './toolPresentation';
import DiagnosticText from './DiagnosticText';

function Preview({ label, value }: { label: string; value: AiDiagnosticPreview }) {
  return <DiagnosticText label={label} text={value.text} characters={value.characters} truncated={value.truncated} />;
}

function callStatusText(call: AiToolCallDiagnostic): string {
  if (call.status === 'failed') return '失败';
  if (call.status === 'completed') return '完成';
  return '尚无结束记录';
}

function ToolDiagnosticRow({
  call,
  index,
}: {
  call: AiToolCallDiagnostic;
  index: number;
}) {
  return <details className="ai-dev-inspector__tool">
    <summary>
      <span className="ai-dev-inspector__tool-index">{index + 1}</span>
      <strong>{call.displayName || call.name || '未知工具'}</strong>
      {call.cached ? <span className="ai-dev-inspector__tag">缓存</span> : null}
      <span>{callStatusText(call)}</span>
    </summary>
    <div className="ai-dev-inspector__tool-detail">
      {call.displayName && call.name && call.displayName !== call.name
        ? <><span>工具函数</span><pre>{call.name}</pre></>
        : null}
      <span>调用 ID</span><pre>{call.toolCallId}</pre>
      {call.operationId ? <><span>操作 ID</span><pre>{call.operationId}</pre></> : null}
      {call.outcome ? <><span>执行结果</span><pre>{call.outcome}</pre></> : null}
      {call.approvalStatus ? <><span>审批状态</span><pre>{call.approvalStatus}</pre></> : null}
      {call.error ? <Preview label="错误" value={call.error} /> : null}
      {call.arguments ? <Preview label="模型提交的参数" value={call.arguments} />
        : <div className="ai-dev-inspector__notice">未读取到参数记录，不补造内容。</div>}
      {call.result ? <Preview label="返回给模型的结果" value={call.result} />
        : <div className="ai-dev-inspector__notice">未读取到返回记录；调用可能尚未结束、被中断或日志缺失。</div>}
    </div>
  </details>;
}

function groupedCallStatus(calls: AiToolCallDiagnostic[]): {
  text: string;
  state: 'running' | 'failed' | 'completed';
} {
  const completed = calls.filter((call) => call.status === 'completed').length;
  const pending = calls.filter((call) => !call.status).length;
  const failed = calls.length - completed - pending;
  if (pending > 0) {
    return { state: 'running', text: `保存中 · ${completed}/${calls.length}` };
  }
  if (failed > 0) {
    return { state: 'failed', text: `部分失败 · ${completed}/${calls.length}` };
  }
  return { state: 'completed', text: `已保存 · ${calls.length} 条` };
}

function ToolDiagnosticGroup({
  label,
  calls,
  callIndex,
}: {
  label: string;
  calls: AiToolCallDiagnostic[];
  callIndex: (call: AiToolCallDiagnostic) => number;
}) {
  const status = groupedCallStatus(calls);
  return <details
    className="ai-dev-inspector__tool-presentation-group"
    data-status={status.state}
  >
    <summary>
      <span>
        <strong>{label}</strong>
        <small>{calls.length} 条独立、可恢复的落盘记录</small>
      </span>
      <em>{status.text}</em>
    </summary>
    <div className="ai-dev-inspector__tool-list ai-dev-inspector__tool-presentation-group-body">
      <div className="ai-dev-inspector__notice">
        这些记录共同构成一次面向用户的保存动作；展开后可逐项核对参数、返回和恢复记录。
      </div>
      {calls.map((call) => <ToolDiagnosticRow
        key={call.toolCallId}
        call={call}
        index={callIndex(call)}
      />)}
    </div>
  </details>;
}

function ToolDiagnosticsList({ calls }: { calls: AiToolCallDiagnostic[] }) {
  const indexes = new Map(calls.map((call, index) => [call.toolCallId, index]));
  return <div className="ai-dev-inspector__tool-list">
    {groupToolPresentation(calls).map((item) => item.type === 'tool'
      ? <ToolDiagnosticRow
        key={item.tool.toolCallId}
        call={item.tool}
        index={indexes.get(item.tool.toolCallId) ?? 0}
      />
      : <ToolDiagnosticGroup
        key={`group-${item.groupKey}`}
        label={item.label}
        calls={item.tools}
        callIndex={(call) => indexes.get(call.toolCallId) ?? 0}
      />)}
  </div>;
}

export default function ToolDiagnosticsCard({ runId, revision }: {
  runId: string;
  revision: string;
}) {
  const [calls, setCalls] = React.useState<AiToolCallDiagnostic[]>([]);
  const [opened, setOpened] = React.useState(false);
  const [loaded, setLoaded] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [hasMore, setHasMore] = React.useState(false);
  const cursor = React.useRef(0);
  const version = React.useRef(0);

  const load = React.useCallback(async () => {
    const requestVersion = ++version.current;
    setLoading(true);
    setError('');
    try {
      const response = await services.ai.getAgentRunToolDiagnostics({ runId, after: cursor.current });
      if (requestVersion !== version.current) return;
      if (!response.success) {
        setError(response.error || '读取工具调用失败');
        return;
      }
      if (response.data.runId !== runId) {
        setError('工具调用记录与当前 Run 不匹配');
        return;
      }
      setCalls((current) => mergeToolDiagnostics(current, response.data.calls));
      cursor.current = response.data.nextCursor;
      setHasMore(response.data.hasMore);
      setLoaded(true);
    } catch {
      if (requestVersion === version.current) setError('读取工具调用失败');
    } finally {
      if (requestVersion === version.current) setLoading(false);
    }
  }, [runId]);

  // The parent keys this component by Run. Closing the inspector also makes
  // pending responses obsolete; diagnostics never enter the public SSE store.
  React.useEffect(() => () => { version.current += 1; }, []);
  React.useEffect(() => {
    if (opened) void load();
  }, [opened, revision, load]);

  return <details
    className="ai-dev-inspector__planner-raw"
    open={opened}
    onToggle={(event) => setOpened(event.currentTarget.open)}
  >
    <summary>
      <span>
        <strong>工具调用</strong>
        <small>开发环境私有日志；常见凭据字段脱敏，按调用 ID 关联</small>
      </span>
      <em data-status={error ? 'fail' : loaded ? 'pass' : 'captured'}>
        {loading ? '读取中' : error ? '读取失败' : loaded ? `${calls.length} 次调用` : '展开读取'}
      </em>
    </summary>
    <div className="ai-dev-inspector__planner-raw-body">
      <div className="ai-dev-inspector__notice">
        参数为模型提交值，不包含宿主随后绑定的字段；结果为工具返回给模型的内容。仅展示有日志证据的字段。
      </div>
      {error ? <div className="ai-dev-inspector__error">{error}</div> : null}
      {loaded && !error && calls.length === 0 ? <div className="ai-dev-inspector__notice">
        当前 Run 没有工具调用记录；子 Run 的调用请展开对应执行段查看。
      </div> : null}
      <ToolDiagnosticsList calls={calls} />
      {hasMore ? <div className="ai-dev-inspector__notice">还有日志未读取，参数和结果可能分布在后续页。</div> : null}
      <button type="button" onClick={() => void load()} disabled={loading}>
        {loading ? '读取中…' : hasMore ? '加载后续记录' : '刷新记录'}
      </button>
    </div>
  </details>;
}

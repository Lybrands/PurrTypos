import React from 'react'
import { createRoot } from 'react-dom/client'
import AssistantOutput from '../../src/components/AgentConversation/AssistantOutput'
import { dispatchAgentChunk, initialAgentAccumulator } from '../../src/agent-runtime/chunkHandlers/index'
import '../../src/App.scss'
const endpoint = 'http://127.0.0.1:18325'
function App() {
  const [messages, setMessages] = React.useState<any[]>([{ role: 'assistant', content: '' }])
  const [status, setStatus] = React.useState<any>({ state: 'ready' })
  const context = React.useMemo(() => ({
    acc: initialAgentAccumulator({ sessionId: 1, userText: 'synthetic', turnStartedAt: Date.now() }),
    sessionId: 1, modelIdentity: { name: 'glm-5.3-flash' }, now: Date.now,
    host: { readMessages: () => messages, replaceMessages: setMessages,
      scheduleCommit: setMessages, flushCommits() {}, setRunning() {}, isVisible: () => true, onSettled() {} },
  }), [])
  React.useEffect(() => {
    const stream = new EventSource(endpoint + '/events')
    stream.onmessage = ({ data }) => {
      const event = JSON.parse(data)
      if (event.fixture) setStatus(event.fixture)
      else dispatchAgentChunk(event, context as any)
    }
    return () => stream.close()
  }, [])
  async function command(action: string) { await fetch(endpoint + '/' + action, { method: 'POST' }) }
  return <main style={{ maxWidth: 850, margin: '40px auto', padding: 24 }}>
    <h1>PurrTypos 阶段输出验收</h1>
    <p>合成材料 · 实际 AssistantOutput 组件 · Core → SQLite → HTTP SSE</p>
    <nav style={{ display: 'flex', gap: 12 }}>
      <button onClick={() => command('start')}>开始</button>
      <button onClick={() => command('stage')}>继续阶段流</button>
      <button onClick={() => command('slow')}>释放慢 Child</button>
      <button onClick={() => command('cancel')}>取消</button>
    </nav>
    <pre aria-label="运行状态">{JSON.stringify(status, null, 2)}</pre>
    <AssistantOutput index={0} message={messages.at(-1)} loading={!context.acc.canonicalOutput?.runTerminal}
      isLastAssistant showPlaceholder={false} setScrolledUpByReason={() => {}}
      onResolveToolApproval={async () => ({ success: false })}/>
  </main>
}
createRoot(document.getElementById('root')!).render(<App />)

import readline from 'node:readline'
import { dispatchAgentChunk, initialAgentAccumulator } from '../../../src/agent-runtime/chunkHandlers/index.ts'
import { buildAssistantTimeline } from '../../../src/components/AgentConversation/AssistantOutput/timeline.ts'
let messages: any[] = [{ role: 'assistant', content: '' }]
const context: any = {
  acc: initialAgentAccumulator({ sessionId: 1, userText: 'synthetic', turnStartedAt: 0 }),
  sessionId: 1, modelIdentity: { name: 'test' }, now: () => 100,
  host: {
    readMessages: () => messages, replaceMessages: (next: any) => { messages = next },
    scheduleCommit: (update: any) => { messages = update(messages) },
    flushCommits() {}, setRunning() {}, isVisible: () => true, onSettled() {},
  },
}
for await (const line of readline.createInterface({ input: process.stdin })) {
  for (const event of JSON.parse(line)) dispatchAgentChunk(event, context)
  const message = messages.at(-1)
  process.stdout.write(JSON.stringify({
    state: context.acc.canonicalOutput,
    stages: buildAssistantTimeline(message, { messageIndex: 0, isStreaming: true, loading: true })
      .filter((part: any) => part.type === 'stage'),
  }) + '\n')
}

import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'vite'
import { parseHTML } from 'linkedom'

test('analysis page waits for history and Root completion, freezes queued runtime and protects switched drafts', async () => {
  const React = await import('react')
  await import('sass')
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, globalThis[key]]))
  Object.assign(globalThis, { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true })
  const state = {
    workId: 'A', controller: null, subscriptions: new Map(), requests: [], canceledRoots: [],
    location: { pathname: '/novel-sources/A', state: null },
    toast: { error() {}, success() {}, info() {} },
  }
  let acceptRequest
  let resolveArtifact
  const source = id => ({ id, title: id, source_type: 'external_text', latest_revision_id: `${id}:v1`,
    revisions: [{ id: `${id}:v1`, version_no: 1, character_count: 12 }] })
  state.services = { ai: { cancelAgentRun: async ({ runId }) => {
    state.canceledRoots.push(runId)
    return { success: true }
  } }, novelSources: {
    list: async () => ({ success: true, data: [] }),
    get: async ({ workId }) => ({ success: true, data: source(workId) }),
    listAnalysisRuns: async () => ({ success: true, data: [] }),
    getAnalysisArtifact: () => new Promise(resolve => { resolveArtifact = resolve }),
    consumeAnalysisEvents: ({ revisionId, onEvent, signal }) => {
      state.subscriptions.set(revisionId, onEvent)
      return new Promise(resolve => signal.addEventListener('abort', () => resolve(), { once: true }))
    },
    followUpAnalysis: args => {
      state.requests.push(args)
      return new Promise(resolve => { acceptRequest = resolve })
    },
    startAnalysis: () => { throw new Error('queued follow-up must not start a fresh analysis') },
  } }
  globalThis.__analysisComposerTest = state
  const stub = `
    import React from 'react'
    const state = globalThis.__analysisComposerTest
    export const services = state.services
    export const analysisSessions = {
      list: async revision => ({success: true, data: [{id: 'legacy:' + revision, title: '来源对话'}, ...(state.newSession ? [state.newSession] : [])]}),
      create: async () => { state.newSession = {id: 'new-session', title: '新对话'}; return {success: true, data: state.newSession} },
      update: async () => ({success: true}),
    }
    export const useParams = () => ({ workId: state.workId })
    export const useLocation = () => state.location
    const navigate = () => {}
    export const useNavigate = () => navigate
    export const usePurrToast = () => state.toast
    const confirm = async () => 'confirm'
    export const usePurrConfirm = () => confirm
    const Empty = () => null
    export default Empty
    export const AgentConversationPanel = ({ controller, extensions }) => { state.controller = controller; state.extensions = extensions; return null }
    export const WritingSkillReview = Empty, NovelAnalysisEvidenceList = Empty
    export const recordAgentConversationDebugChunk = () => {}
    export const PurrButton = Empty, PurrCheckbox = Empty, PurrInput = Object.assign(Empty, { TextArea: Empty })
    export const PurrModal = Empty, PurrSegmented = Empty, PurrSelect = Empty, PurrSpin = Empty
    export const PurrTabs = Empty, PurrTooltip = Empty
    export const ArrowLeftIcon = Empty, ArrowRightIcon = Empty, BookIcon = Empty, DeleteIcon = Empty
    export const FileTextIcon = Empty, HomeIcon = Empty, ImportIcon = Empty, InboxIcon = Empty, SearchIcon = Empty
  `
  const replaced = new Set(['../services/analysisSessions', 'react-router-dom', '@/services', '@/purr-components', '../components/AppHeader',
    '../components/AiDevInspector/store', '../components/AgentConversation', '../components/Markdown',
    './WritingSkillReview', './SourceTechniqueResults', '../components/NovelAnalysisEvidenceModal'])
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true },
    plugins: [{ name: 'analysis-page-service-fixture', enforce: 'pre',
      transform(code, id) {
        if (!id.endsWith('/src/NovelSourcesPage/index.tsx')) return null
        return code.replace(/from '([^']+)'/g, (match, name) => replaced.has(name) ? "from 'analysis-page-fixture'" : match)
      },
      resolveId: id => id === 'analysis-page-fixture' ? '\0analysis-page-fixture' : null,
      load: id => id === '\0analysis-page-fixture' ? stub : null,
    }],
  })
  let root
  try {
    const { default: Page } = await vite.ssrLoadModule('/src/NovelSourcesPage/index.tsx')
    const { createRoot } = await import('react-dom/client')
    root = createRoot(window.document.getElementById('root'))
    const model = { id: 'glm-5.3-flash', name: 'GLM', apiKey: 'original', baseUrl: '',
      supportsThinking: true, thinkingOnly: false, apiProvider: 'zai' }
    const updateModel = () => {}
    const render = () => root.render(React.createElement(Page, { books: [], modelConfigs: [model],
      onUpdateModelConfig: updateModel, onBack() {}, onHome() {} }))
    const publish = async runs => {
      await React.act(async () => state.subscriptions.get(`${state.workId}:v1`)({ runs, chunks: [] }))
    }
    await React.act(async () => render())
    assert.equal(state.controller.conversation.initializing, true)
    assert.equal(state.controller.composer.updateModel, updateModel)
    await React.act(async () => state.controller.actions.send('历史尚未加载'))
    assert.equal(state.controller.conversation.queuedSubmissions.length, 0)
    const run = { runId: 'root-A', commandId: 'command-A', runStatus: 'running', taskStatus: 'completed', workflowStatus: 'running',
      prompt: '分析', units: [], artifactRef: 'novel-analysis-artifact://artifact-A', interactionKind: 'analysis' }
    await publish([run])
    assert.equal(state.controller.capabilities.inputDisabled, false)
    assert.equal(state.controller.capabilities.submitMode, 'queue')
    await React.act(async () => state.controller.composer.setValue('排队追问'))
    await React.act(async () => state.controller.actions.send())
    assert.equal(state.controller.composer.value, '')
    assert.equal(state.controller.conversation.queuedSubmissions.length, 1)
    assert.equal(state.requests.length, 0, 'Task completion cannot release a running Root')
    const queuedId = state.controller.conversation.queuedSubmissions[0].id
    await React.act(async () => state.controller.actions.updateQueuedSubmission(queuedId, { editing: true }))
    await React.act(async () => state.controller.actions.send('随后删除的消息'))
    const deletedId = state.controller.conversation.queuedSubmissions[1].id
    await React.act(async () => state.controller.actions.updateQueuedSubmission(deletedId, null))
    model.apiKey = 'changed-after-enqueue'
    await publish([{ ...run, runStatus: 'completed', workflowStatus: 'completed' }])
    assert.equal(state.requests.length, 0, 'wait for the existing artifact before deciding follow-up')
    await React.act(async () => resolveArtifact({ success: true, data: {
      artifactId: 'artifact-A', facts: [], craftCards: [],
    } }))
    assert.equal(state.requests.length, 0, 'Root completion must not send a message being edited')
    await React.act(async () => state.controller.actions.updateQueuedSubmission(queuedId,
      { content: '修改后的追问', editing: false }))
    assert.equal(state.requests.length, 1)
    assert.equal(state.requests[0].prompt, '修改后的追问')
    assert.equal(state.requests[0].runtime.apiKey, 'original')
    assert.equal(state.requests[0].revisionId, 'A:v1')
    await React.act(async () => state.controller.composer.setValue('A 的新草稿'))
    const retiredController = state.controller
    state.workId = 'B'
    await React.act(async () => render())
    await publish([])
    await React.act(async () => state.controller.composer.setValue('B 的草稿'))
    await React.act(async () => retiredController.actions.send('旧页面的迟到事件'))
    await React.act(async () => retiredController.actions.abort())
    assert.deepEqual(state.canceledRoots, [])
    await React.act(async () => acceptRequest({ success: true }))
    assert.equal(state.controller.composer.value, 'B 的草稿')
    assert.equal(state.controller.conversation.running, false)
    state.workId = 'A'
    await React.act(async () => render())
    await publish([])
    assert.equal(state.controller.composer.value, 'A 的新草稿')
    assert.equal(state.controller.conversation.queuedSubmissions.length, 0)
    assert.equal(state.requests.length, 1, 'late completion must not resubmit the accepted request')
    await publish([{ ...run, taskId: 'completed-task', workflowStatus: 'completed' }])
    await React.act(async () => state.controller.actions.abort())
    assert.deepEqual(state.canceledRoots, ['root-A'], 'stop must target the still-running Root when its Task has completed')
    state.workId = 'C'
    await React.act(async () => render())
    await publish([])
    await React.act(async () => state.controller.actions.send('没有分析结果也只问原文'))
    assert.equal(state.requests.length, 2)
    assert.equal(state.requests[1].artifactId, undefined)
    assert.equal(state.requests[1].prompt, '没有分析结果也只问原文')
    await React.act(async () => acceptRequest({ success: true }))
    const cRun = {...run, runId: 'c-answer', commandId: state.requests[1].commandId, taskId: null, artifactRef: undefined,
      conversationId: 'legacy:C:v1', interactionKind: 'follow_up', runStatus: 'done', conversationStatus: 'finalized', workflowStatus: 'completed', finalResponse: '之前的回答'}
    await publish([cRun])
    assert.ok(state.controller.conversation.messages.some(message => message.content === '之前的回答'))
    await React.act(async () => state.controller.composer.setValue('尚未发送的草稿'))
    const editedIndex = state.controller.conversation.messages.findIndex(message => message.role === 'user')
    assert.ok(editedIndex >= 0)
    await React.act(async () => state.controller.actions.editMessage(editedIndex, '编辑后重新发送的问题'))
    assert.equal(state.requests.length, 3, 'editing a historical message must dispatch a real request')
    assert.equal(state.requests[2].prompt, '编辑后重新发送的问题')
    assert.equal(state.requests[2].conversationId, 'legacy:C:v1')
    assert.equal(state.requests[2].revisionId, 'C:v1')
    assert.equal(state.requests[2].replaceRunId, 'c-answer')
    assert.equal(state.requests[2].artifactId, undefined, 'edited requests must not borrow the latest result')
    assert.equal(state.controller.composer.value, '尚未发送的草稿')
    await React.act(async () => acceptRequest({ success: true }))
    const editedRun = { ...cRun, runId: 'edited-answer', commandId: state.requests[2].commandId,
      prompt: state.requests[2].prompt, finalResponse: '重新生成的回答' }
    await publish([editedRun])
    assert.ok(!state.controller.conversation.messages.some(message => message.content === '之前的回答'))
    assert.ok(state.controller.conversation.messages.some(message => message.content === '重新生成的回答'))
    await React.act(async () => state.controller.actions.createSession())
    assert.equal(state.controller.conversation.activeSessionId, 'new-session')
    assert.equal(state.controller.conversation.messages.length, 0)
    await React.act(async () => state.controller.composer.setValue('新对话草稿'))
    await React.act(async () => state.controller.actions.selectSession('legacy:C:v1'))
    assert.ok(state.controller.conversation.messages.some(message => message.content === '重新生成的回答'))
    await React.act(async () => state.controller.actions.selectSession('new-session'))
    assert.equal(state.controller.composer.value, '新对话草稿')
    await React.act(async () => state.controller.actions.selectSession('legacy:C:v1'))
    await publish([{...cRun, runId:'later-question', artifactRef:undefined}, {...cRun, artifactRef:'novel-analysis-artifact://owned-result'}])
    assert.ok(state.extensions.renderAssistantAttachment({role:'assistant', agentRunId:'c-answer'}))
    assert.equal(state.extensions.renderAssistantAttachment({role:'assistant', agentRunId:'later-question'}), null)


  } finally {
    if (root) await React.act(async () => root.unmount())
    await vite.close()
    delete globalThis.__analysisComposerTest
    Object.assign(globalThis, previous)
  }
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { initialCanonicalOutputState } from '../../agent-runtime/canonicalOutput.ts'
import {
  buildAssistantCopyView,
  getAssistantRenderableMarkdown,
} from './assistantCopy.ts'

test('assistant copy is hidden when the final answer is empty', () => {
  const view = buildAssistantCopyView({
    message: { role: 'assistant', content: '   ' },
    isLastAssistant: false,
    loading: false,
    showPlaceholder: false,
  })

  assert.deepEqual(view, {
    visible: false,
    markdown: '',
    plainText: '',
  })
})

test('assistant copy exposes plain text and original Markdown', () => {
  const markdown = '# 标题\n\n- **重点**：[链接](https://example.com)'
  const view = buildAssistantCopyView({
    message: { role: 'assistant', content: markdown },
    isLastAssistant: false,
    loading: false,
    showPlaceholder: false,
  })

  assert.equal(view.visible, true)
  assert.equal(view.markdown, markdown)
  assert.equal(view.plainText, '标题\n重点：链接')
})

test('canonical final text is a copyable answer even before legacy content is populated', () => {
  const canonicalOutput = {
    ...initialCanonicalOutputState(),
    finalText: '**规范答案**',
    finalStreamStatus: 'committed' as const,
  }
  const message = { role: 'assistant' as const, content: '', canonicalOutput }

  assert.equal(getAssistantRenderableMarkdown(message), '**规范答案**')
  assert.deepEqual(buildAssistantCopyView({
    message,
    isLastAssistant: false,
    loading: false,
    showPlaceholder: false,
  }), {
    visible: true,
    markdown: '**规范答案**',
    plainText: '规范答案',
  })
})

test('assistant copy keeps the old error, placeholder and active-stream exclusions', () => {
  const base = {
    isLastAssistant: false,
    loading: false,
    showPlaceholder: false,
  }
  assert.equal(buildAssistantCopyView({
    ...base,
    message: { role: 'assistant', content: '失败', isError: true },
  }).visible, false)
  assert.equal(buildAssistantCopyView({
    ...base,
    message: { role: 'assistant', content: '占位' },
    showPlaceholder: true,
  }).visible, false)
  assert.equal(buildAssistantCopyView({
    ...base,
    message: { role: 'assistant', content: '生成中' },
    isLastAssistant: true,
    loading: true,
  }).visible, false)
})

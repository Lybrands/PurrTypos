import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('固定版本默认项连续发送保持，取消跨重载保持，临时上传仅本轮，原创为空', async () => {
  const vite = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const saved = Object.fromEntries(['window','document','IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis,key)]))
  let root
  try {
    const { useWritingTechniqueSelection } = await vite.ssrLoadModule('/src/Workspace/AiPanel/hooks/useWritingTechniqueSelection.ts')
    const { services } = await vite.ssrLoadModule('/src/services/index.ts')
    const pinned = {kind: 'technique', id: 't1', versionId: 'old-version'}
    const sessions = new Map([['1',[pinned]],['2',[pinned]],['3',[]]])
    services.writingTechniques.getMode = async () => ({success:true,data:'manual'})
    services.writingTechniques.getSelection = async (kind,id) => ({success:true,data:kind==='book' ? id==='book'?[pinned]:[] : sessions.get(id)})
    services.writingTechniques.setSelection = async (kind,id,refs) => { sessions.set(id,refs); return {success:true,data:refs} }
    services.writingTechniques.list = async kind => ({success:true,data:kind==='scheme'?[]:[{kind,id:'t1',publishedHead:'latest-version',metadata:{name:'来源技法'}}]})
    Object.assign(globalThis,{window,document:window.document,IS_REACT_ACT_ENVIRONMENT:true})
    const { createRoot } = await import('react-dom/client')
    let hook
    function Harness({bookId='book',sessionId=1}) { hook=useWritingTechniqueSelection(bookId,sessionId); return React.createElement('span',null,JSON.stringify(hook.selection)) }
    root=createRoot(document.getElementById('root'))
    await act(async()=>root.render(React.createElement(Harness)))
    assert.equal(hook.ready,true)
    assert.deepEqual(hook.selection.refs,[pinned])
    assert.equal(hook.choices[0].ref.versionId,'old-version')
    await act(async()=>hook.accepted(hook.selection))
    await act(async()=>hook.accepted(hook.selection))
    assert.deepEqual(hook.selection.refs,[pinned])
    await act(async()=>hook.addUpload({ref:{kind:'technique',id:'upload',versionId:'u1'},name:'upload'}))
    assert.equal(hook.selection.refs.length,2)
    await act(async()=>hook.accepted(hook.selection))
    assert.deepEqual(hook.selection.refs,[pinned])
    await act(async()=>hook.toggle('t1'))
    assert.deepEqual(hook.selection.refs,[])
    await act(async()=>root.render(React.createElement(Harness,{sessionId:2})))
    assert.deepEqual(hook.selection.refs,[pinned])
    await act(async()=>root.render(React.createElement(Harness,{sessionId:1})))
    assert.deepEqual(hook.selection.refs,[])
    await act(async()=>hook.toggle('t1'))
    assert.deepEqual(hook.selection.refs,[pinned])
    sessions.set('4', [{...pinned, versionId:'session-version'}])
    await act(async()=>root.render(React.createElement(Harness,{sessionId:4})))
    assert.equal(hook.choices[0].ref.versionId,'session-version')
    await act(async()=>root.render(React.createElement(Harness,{bookId:'original',sessionId:3})))
    assert.deepEqual(hook.selection.refs,[])
    await act(async()=>hook.toggle('t1'))
    await act(async()=>hook.accepted(hook.selection))
    assert.deepEqual(hook.selection.refs,[])
  } finally {
    if(root) await act(async()=>root.unmount())
    for(const [key,descriptor] of Object.entries(saved)) { if(descriptor) Object.defineProperty(globalThis,key,descriptor); else delete globalThis[key] }
    await vite.close()
  }
})

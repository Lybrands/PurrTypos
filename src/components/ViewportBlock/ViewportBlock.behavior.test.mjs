import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

test('offscreen work stays unmounted, measured space survives scrolling, and retained forms keep state', async () => {
  const vite = await createServer({configFile:false,appType:'custom',logLevel:'silent',server:{middlewareMode:true,hmr:false}})
  const {window} = parseHTML('<html><body><div id="root"></div></body></html>')
  window.document.getSelection = () => null
  let intersect, resize
  const globals = {window,document:window.document,IS_REACT_ACT_ENVIRONMENT:true,
    IntersectionObserver:class {constructor(callback){intersect=callback} observe(){} disconnect(){}},
    ResizeObserver:class {constructor(callback){resize=callback} observe(){} disconnect(){}}}
  const previous = Object.fromEntries(Object.keys(globals).map(key=>[key,globalThis[key]]))
  Object.assign(globalThis,globals)
  const React=await import('react')
  const {createRoot}=await import('react-dom/client')
  const root=createRoot(window.document.getElementById('root'))
  try {
    const {default:Block}=await vite.ssrLoadModule('/src/components/ViewportBlock/index.tsx')
    let mounts=0,unmounts=0
    function Expensive(){React.useEffect(()=>{mounts++;return()=>unmounts++},[]);return React.createElement('input',{defaultValue:'retained'})}
    const render=keepMounted=>React.act(async()=>root.render(React.createElement(Block,{id:'fixture',keepMounted},React.createElement(Expensive))))
    const visibility=value=>React.act(async()=>intersect([{isIntersecting:value}]))
    await render(false)
    assert.equal(mounts,0)
    await visibility(true)
    assert.equal(mounts,1)
    const node=window.document.querySelector('[data-viewport-block]')
    node.getBoundingClientRect=()=>({height:480})
    await React.act(async()=>resize())
    await visibility(false)
    assert.equal(unmounts,1)
    assert.equal(node.style.height,'480px')
    await render(true)
    await visibility(true)
    window.document.querySelector('input').value='user edit'
    await visibility(false)
    assert.equal(window.document.querySelector('input').value,'user edit')
    assert.equal(unmounts,1)
  } finally {
    await React.act(async()=>root.unmount())
    Object.assign(globalThis,previous)
    await vite.close()
  }
})

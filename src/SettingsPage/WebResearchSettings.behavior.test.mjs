import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import React, { act } from 'react'
import { parseHTML } from 'linkedom'
import { createServer } from 'vite'

let vite
let Component
const fixture = { controls: {}, writes: [], errors: [], get: null, put: null }

before(async () => {
  globalThis.__webSettingsFixture = fixture
  vite = await createServer({
    appType: 'custom', logLevel: 'silent', server: { middlewareMode: true },
    plugins: [{
      name: 'web-settings-fixtures', enforce: 'pre',
      resolveId(id, importer) {
        if (!importer?.endsWith('/WebResearchSettings.tsx')) return
        if (id === '../services') return '\0web-settings-services'
        if (id === '../hooks/useAppFeedback') return '\0web-settings-feedback'
        if (id === '@/purr-components' || id.endsWith('/src/purr-components') || id.endsWith('/src/purr-components/index.ts')) return '\0web-settings-primitives'
      },
      load(id) {
        if (id === '\0web-settings-services') return `export const services = {settings: {getSettings: () => globalThis.__webSettingsFixture.get(), setSettings: (patch) => globalThis.__webSettingsFixture.put(patch)}}`
        if (id === '\0web-settings-feedback') return `export const useAppFeedback = () => ({message: {error: (text) => globalThis.__webSettingsFixture.errors.push(text)}})`
        // Test the product's cascading state and persistence, independently of
        // the shared widgets' overlay implementation (covered separately).
        if (id === '\0web-settings-primitives') return `import React from 'react';
          const remember = (key, props) => { globalThis.__webSettingsFixture.controls[key] = props; };
          export function PurrSwitch(props) { remember('switch', props); return React.createElement('button', {role:'switch', 'aria-label':props['aria-label'], disabled:props.disabled, onClick:()=>props.onChange(!props.checked)}, String(props.checked)); }
          export function PurrSelect(props) { remember('select', props); return React.createElement('select', {'aria-label':props['aria-label'],value:props.value,disabled:props.disabled,onChange:e=>props.onChange(e.target.value)}, props.options.map(o=>React.createElement('option',{key:o.value,value:o.value},o.label))); }
          export const PurrInput = {Password: (props) => { remember('password', props); const {onPressEnter,...inputProps}=props; return React.createElement('input', {...inputProps,type:'password'}); }};
          export function PurrButton({size,...props}) { return React.createElement('button', {...props,type:'button'}); }
        `
      },
    }],
  })
  Component = (await vite.ssrLoadModule('/src/SettingsPage/WebResearchSettings.tsx')).default
})
after(async () => { delete globalThis.__webSettingsFixture; await vite?.close() })

async function mount({ data = {}, loadFailed = false } = {}) {
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>')
  const saved = Object.fromEntries(['window','document','IS_REACT_ACT_ENVIRONMENT'].map(key => [key,Object.getOwnPropertyDescriptor(globalThis,key)]))
  Object.assign(globalThis,{window,document:window.document,IS_REACT_ACT_ENVIRONMENT:true})
  fixture.controls = {}; fixture.writes = []; fixture.errors = []
  fixture.get = async () => ({success:!loadFailed,data})
  fixture.put = async patch => { fixture.writes.push(patch); return {success:true} }
  const { createRoot } = await import('react-dom/client')
  const root = createRoot(document.getElementById('root'))
  await act(async () => root.render(React.createElement(Component)))
  const text = () => document.body.textContent
  const click = async label => {
    const button = [...document.querySelectorAll('button')].find(el => el.textContent === label)
    assert.ok(button, `button ${label} must be visible`)
    assert.equal(button.disabled, false)
    await act(async () => button.click())
  }
  return {text,click,async close() {
    await act(async () => root.unmount())
    for (const [key,descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis,key,descriptor)
      else delete globalThis[key]
    }
  }}
}

test('search settings cascade from permission to provider to write-only key and actual fallback',async () => {
  const ui = await mount()
  try {
    assert.equal(document.querySelector('select'),null)
    await act(async () => fixture.controls.switch.onChange(true))
    assert.match(ui.text(),/未配置 API Key，当前使用免费搜索/)
    assert.equal(document.querySelector('input').type,'password')
    await act(async () => fixture.controls.password.onChange({target:{value:'  sk-fixture-new-key  '}}))
    assert.match(ui.text(),/当前使用免费搜索/) // draft does not switch providers before saving
    await ui.click('保存 API Key')
    assert.deepEqual(fixture.writes.at(-1),{web_search_deepseek_api_key:'sk-fixture-new-key'})
    assert.match(ui.text(),/当前使用 DeepSeek 搜索/)
    assert.equal(document.querySelector('input').value,'')
    await act(async () => fixture.controls.select.onChange('free'))
    assert.equal(document.querySelector('input'),null)
    assert.match(ui.text(),/当前使用免费搜索/)
    await act(async () => fixture.controls.select.onChange('deepseek'))
    assert.match(ui.text(),/当前使用 DeepSeek 搜索/)
    await ui.click('删除 API Key')
    assert.deepEqual(fixture.writes.at(-1),{web_search_deepseek_api_key:''})
    assert.match(ui.text(),/未配置 API Key，当前使用免费搜索/)
    await act(async () => fixture.controls.switch.onChange(false))
    assert.equal(document.querySelector('select'),null)
    assert.equal(fixture.writes.filter(patch=>Object.hasOwn(patch,'web_search_deepseek_api_key')).length,2)
  } finally { await ui.close() }
})

test('saved key is never hydrated into the input and failed writes preserve confirmed settings',async () => {
  const ui = await mount({data:{web_search_enabled:true,web_search_provider:'deepseek',web_search_deepseek_api_key_configured:true}})
  try {
    assert.equal(document.querySelector('input').value,'')
    assert.match(document.querySelector('input').placeholder,/已保存/)
    fixture.put = async () => ({success:false})
    await act(async () => fixture.controls.select.onChange('free'))
    assert.match(ui.text(),/当前使用 DeepSeek 搜索/)
    await act(async () => fixture.controls.password.onChange({target:{value:'sk-replacement'}}))
    await ui.click('更新 API Key')
    assert.equal(document.querySelector('input').value,'sk-replacement')
    await ui.click('删除 API Key')
    assert.match(ui.text(),/当前使用 DeepSeek 搜索/)
    assert.equal(fixture.errors.length,3)
    assert.ok(fixture.errors.every(text=>!text.includes('sk-replacement')))
  } finally { await ui.close() }
})

test('pending saves block duplicate submissions and loading failure blocks permission changes',async () => {
  const ui = await mount({data:{web_search_enabled:true}})
  try {
    let resolve
    fixture.put = patch => { fixture.writes.push(patch); return new Promise(done=>{resolve=done}) }
    await act(async () => { fixture.controls.select.onChange('free'); fixture.controls.switch.onChange(false) })
    assert.equal(fixture.writes.length,1)
    assert.equal(document.querySelector('select').disabled,true)
    await act(async () => resolve({success:true}))
    assert.match(ui.text(),/当前使用免费搜索/)
    assert.equal(document.querySelector('select').disabled,false)
  } finally { await ui.close() }
  const failed = await mount({loadFailed:true})
  try {
    assert.ok(document.querySelector('[role="alert"]'))
    assert.equal(fixture.controls.switch.disabled,true)
    assert.equal(document.querySelector('select'),null)
    assert.deepEqual(fixture.writes,[])
  } finally { await failed.close() }
})

const assert = require('node:assert/strict')
const { test } = require('node:test')
const { configureExternalLinks, externalWebUrl } = require('./external_links')

test('research links open externally and unsafe schemes never reach the OS', () => {
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,test', 'https://user:pass@example.com']) {
    assert.equal(externalWebUrl(url), null)
  }
  const opened = []
  let popup, navigate
  const win = { webContents: {
    setWindowOpenHandler: (handler) => { popup = handler },
    on: (name, handler) => { assert.equal(name, 'will-navigate'); navigate = handler },
    getURL: () => 'http://localhost:5174/',
  } }
  configureExternalLinks(win, { openExternal: async (url) => opened.push(url) })
  assert.deepEqual(popup({ url: 'https://example.com/source' }), { action: 'deny' })
  popup({ url: 'javascript:alert(1)' })
  assert.deepEqual(opened, ['https://example.com/source'])
  let prevented = 0
  const event = { preventDefault: () => prevented++ }
  navigate(event, 'http://localhost:5174/#/settings')
  assert.equal(prevented, 0)
  navigate(event, 'https://example.com/other')
  assert.equal(prevented, 1)
  assert.equal(opened.at(-1), 'https://example.com/other')
})

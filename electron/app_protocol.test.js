'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const {
  createAppProtocolHandler,
  getRequestSegments,
  resolveInside,
} = require('./app_protocol')

test('parses valid app URLs and rejects traversal segments', () => {
  assert.deepEqual(getRequestSegments('app://./dist/index.html'), ['dist', 'index.html'])
  assert.equal(getRequestSegments('app://./dist/%2e%2e/secret.txt'), null)
  assert.equal(getRequestSegments('app://./dist/%2e%2e%2fsecret.txt'), null)
  assert.equal(getRequestSegments('not a url'), null)
})

test('resolveInside never escapes its declared root', () => {
  const root = path.resolve('C:\\resources')
  assert.equal(resolveInside(root, ['dist', 'index.html']), path.join(root, 'dist', 'index.html'))
  assert.equal(resolveInside(root, ['..', 'secret.txt']), null)
})

test('serves packaged resources with the matching content type', async () => {
  const resourcesPath = path.resolve('C:\\resources')
  const expectedPath = path.join(resourcesPath, 'dist', 'index.html')
  const reads = []
  const handler = createAppProtocolHandler({
    resourcesPath,
    appPath: path.resolve('C:\\app'),
    fsImpl: {
      readFileSync(candidate) {
        reads.push(candidate)
        if (candidate === expectedPath) return Buffer.from('<main>ready</main>')
        throw new Error('missing')
      },
    },
  })

  const response = handler({ url: 'app://./dist/index.html' })

  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'text/html; charset=utf-8')
  assert.equal(await response.text(), '<main>ready</main>')
  assert.equal(reads[0], expectedPath)
})

test('returns 404 without reading the filesystem for a traversal URL', () => {
  let reads = 0
  const handler = createAppProtocolHandler({
    resourcesPath: path.resolve('C:\\resources'),
    appPath: path.resolve('C:\\app'),
    fsImpl: { readFileSync() { reads += 1 } },
  })

  const response = handler({ url: 'app://./dist/%2E%2E/secret.txt' })

  assert.equal(response.status, 404)
  assert.equal(reads, 0)
})

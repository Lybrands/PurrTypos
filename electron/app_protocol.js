'use strict'

const fs = require('fs')
const path = require('path')

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
}

function resolveInside(basePath, segments) {
  const base = path.resolve(basePath)
  const candidate = path.resolve(base, ...segments)
  if (candidate === base || candidate.startsWith(`${base}${path.sep}`)) {
    return candidate
  }
  return null
}

function getRequestSegments(requestUrl) {
  try {
    const schemeEnd = requestUrl.indexOf('://')
    if (schemeEnd < 0) return null
    const pathStart = requestUrl.indexOf('/', schemeEnd + 3)
    if (pathStart < 0) return null
    const rawPath = requestUrl.slice(pathStart).split(/[?#]/, 1)[0]
    const rawSegments = rawPath.split('/').filter(Boolean)
    const decodedSegments = rawSegments.map((segment) => decodeURIComponent(segment))
    if (
      decodedSegments.length === 0
      || decodedSegments.some(
        (segment) => (
          segment === '.'
          || segment === '..'
          || segment.includes('\0')
          || segment.includes('/')
          || segment.includes('\\')
        ),
      )
    ) {
      return null
    }

    const url = new URL(requestUrl)
    const decodedPath = decodeURIComponent(url.pathname)
    const segments = decodedPath.split('/').filter(Boolean)
    if (
      segments.length === 0
      || segments.some((segment) => segment === '.' || segment === '..')
    ) {
      return null
    }
    return segments
  } catch {
    return null
  }
}

function createAppProtocolHandler({
  resourcesPath,
  appPath,
  fsImpl = fs,
  ResponseImpl = globalThis.Response,
} = {}) {
  if (!resourcesPath || !appPath) {
    throw new TypeError('resourcesPath and appPath are required')
  }

  return (request) => {
    const segments = getRequestSegments(request.url)
    if (!segments) return new ResponseImpl('', { status: 404 })

    const candidates = [
      resolveInside(resourcesPath, segments),
      ...(segments[0] === 'assets'
        ? [resolveInside(path.join(resourcesPath, 'dist'), segments)]
        : []),
      resolveInside(path.join(resourcesPath, 'app'), segments),
      resolveInside(path.join(resourcesPath, 'app.asar.unpacked'), segments),
      resolveInside(appPath, segments),
    ].filter(Boolean)

    for (const candidate of candidates) {
      try {
        const body = fsImpl.readFileSync(candidate)
        const contentType = MIME_TYPES[path.extname(candidate)] || 'application/octet-stream'
        return new ResponseImpl(body, {
          headers: { 'Content-Type': contentType },
        })
      } catch {
        // Try the next packaged-resource layout.
      }
    }
    return new ResponseImpl('', { status: 404 })
  }
}

module.exports = {
  MIME_TYPES,
  createAppProtocolHandler,
  getRequestSegments,
  resolveInside,
}

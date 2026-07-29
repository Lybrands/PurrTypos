'use strict'

const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')

/** Compile a dependency-free TypeScript module to CommonJS for Node tests. */
function loadTypeScriptModule(filePath) {
  const filename = path.resolve(filePath)
  const source = fs.readFileSync(filename, 'utf8')
  const { outputText, diagnostics } = ts.transpileModule(source, {
    fileName: filename,
    reportDiagnostics: true,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  })
  const errors = (diagnostics ?? []).filter(
    (diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error,
  )
  if (errors.length > 0) {
    throw new Error(ts.formatDiagnostics(errors, {
      getCanonicalFileName: (name) => name,
      getCurrentDirectory: () => process.cwd(),
      getNewLine: () => '\n',
    }))
  }

  const originalTsLoader = Module._extensions['.ts']
  const originalResolveFilename = Module._resolveFilename
  Module._extensions['.ts'] = (loadedModule, dependencyPath) => {
    const dependencySource = fs.readFileSync(dependencyPath, 'utf8')
    const dependencyOutput = ts.transpileModule(dependencySource, {
      fileName: dependencyPath,
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2020,
        esModuleInterop: true,
      },
    }).outputText
    // Node 24 can classify a .ts filename as native ESM even after this helper
    // transpiles it to CommonJS. Compile under a synthetic .cjs filename while
    // preserving the module's real resolution path.
    loadedModule._compile(dependencyOutput, `${dependencyPath}.cjs`)
  }
  Module._resolveFilename = function resolveFilename(request, parent, isMain, options) {
    if (typeof request === 'string' && request.startsWith('@/')) {
      request = path.join(__dirname, '..', 'src', request.slice(2))
    }
    return originalResolveFilename.call(this, request, parent, isMain, options)
  }
  try {
    const loaded = new Module(filename, module)
    loaded.filename = filename
    loaded.paths = Module._nodeModulePaths(path.dirname(filename))
    loaded._compile(outputText, filename)
    return loaded.exports
  } finally {
    Module._resolveFilename = originalResolveFilename
    if (originalTsLoader) Module._extensions['.ts'] = originalTsLoader
    else delete Module._extensions['.ts']
  }
}

module.exports = { loadTypeScriptModule }

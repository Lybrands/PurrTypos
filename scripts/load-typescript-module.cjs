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
    loadedModule._compile(dependencyOutput, dependencyPath)
  }
  try {
    const loaded = new Module(filename, module)
    loaded.filename = filename
    loaded.paths = Module._nodeModulePaths(path.dirname(filename))
    loaded._compile(outputText, filename)
    return loaded.exports
  } finally {
    if (originalTsLoader) Module._extensions['.ts'] = originalTsLoader
    else delete Module._extensions['.ts']
  }
}

module.exports = { loadTypeScriptModule }

// Isolated Electron workload: no application backend, credentials, or user data.
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const {spawn} = require('node:child_process')
async function main() {
  const {build} = await import('vite')
  const root = path.resolve(__dirname, '..')
  const output = fs.mkdtempSync(path.join(os.tmpdir(), 'purr-scroll-performance-'))
  await build({configFile:false,define:{'process.env.NODE_ENV':'"production"'},
    resolve:{alias:{'@':path.join(root,'src')}},
    build:{outDir:output,emptyOutDir:false,lib:{entry:path.join(__dirname,'fixtures/scroll-performance.tsx'),
      name:'ScrollFixture',formats:['iife'],fileName:()=> 'fixture.js',cssFileName:'fixture'}},logLevel:'error'})
  fs.writeFileSync(path.join(output,'index.html'), `<!doctype html><meta charset="utf-8">
    <link rel="stylesheet" href="fixture.css"><style>body{font:14px system-ui;color:#333;background:#faf9f7}
    pre{white-space:pre-wrap;overflow-wrap:anywhere}.work-log__subagent{padding:8px;border-bottom:1px solid #ddd}</style>
    <div id="root"></div><script src="fixture.js"></script>`)
  fs.copyFileSync(path.join(__dirname,'fixtures/scroll-performance-electron.cjs'),path.join(output,'measure.cjs'))
  const child=spawn(require('electron'),[path.join(output,'measure.cjs')],{stdio:'inherit'})
  const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('close',resolve)})
  console.log(`Scroll evidence: ${output}`)
  if(code!==0)process.exitCode=1
}
main().catch(error=>{console.error(error);process.exitCode=1})

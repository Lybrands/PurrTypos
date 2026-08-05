const fs = require('node:fs')
const path = require('node:path')

const projectRoot = path.resolve(__dirname, '..')
const sourceRoot = path.join(projectRoot, 'src')
const purrComponentsRoot = path.join(sourceRoot, 'purr-components')
const sourceExtensions = new Set(['.ts', '.tsx', '.scss', '.css'])
const violations = []
const sharedDirectories = new Set(['icons', 'styles'])
const packageJson = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'))
const packageDependencies = {
  ...packageJson.dependencies,
  ...packageJson.devDependencies,
}

const legacyComponentClass =
  /\b(?:ant|ui)-(?:btn|button|checkbox|drawer|empty|form|input|popover|tag|space|card|list|divider|alert|segmented|progress|switch|slider|typography|radio|select|tabs|modal)(?:[A-Za-z0-9_-]*)\b/g

function validateComponentDirectories() {
  for (const entry of fs.readdirSync(purrComponentsRoot, { withFileTypes: true })) {
    if (!entry.isDirectory() || sharedDirectories.has(entry.name)) continue
    if (!entry.name.startsWith('Purr')) {
      violations.push(`src/purr-components/${entry.name}: 组件必须以 Purr 名称独立建目录`)
      continue
    }

    const componentRoot = path.join(purrComponentsRoot, entry.name)
    const implementationFile = path.join(componentRoot, `${entry.name}.tsx`)
    const entryFile = path.join(componentRoot, 'index.ts')
    if (!fs.existsSync(implementationFile)) {
      violations.push(`src/purr-components/${entry.name}: 缺少 ${entry.name}.tsx`)
    }
    if (!fs.existsSync(entryFile)) {
      violations.push(`src/purr-components/${entry.name}: 缺少组件级 index.ts`)
    }
  }
}

function validateIconDependencies() {
  for (const packageName of ['@ant-design/icons', 'lucide-react']) {
    if (packageDependencies[packageName]) {
      violations.push(`package.json: Purr Icons 已自有化，不允许依赖 ${packageName}`)
    }
  }
}

function validateIconSources() {
  const iconRoot = path.join(purrComponentsRoot, 'icons')
  for (const entry of fs.readdirSync(iconRoot, { withFileTypes: true })) {
    if (!entry.isFile() || path.extname(entry.name) !== '.tsx' || entry.name === 'PurrIcon.tsx') continue

    const filePath = path.join(iconRoot, entry.name)
    const source = fs.readFileSync(filePath, 'utf8')
    const relativePath = path.relative(projectRoot, filePath)

    if (/<svg\b/.test(source)) {
      violations.push(`${relativePath}: 图标必须使用统一的 PurrIcon 画布`)
    }
    if (/\b(?:viewBox|strokeWidth|strokeLinecap|strokeLinejoin|strokeDasharray)=/.test(source)) {
      violations.push(`${relativePath}: 不允许覆盖 Purr Softline 的画布或描边规范`)
    }
    if (!/\b(?:createPurrIcon|PurrIcon)\b/.test(source)) {
      violations.push(`${relativePath}: 图标必须由 createPurrIcon 或 PurrIcon 创建`)
    }
  }
}

function visit(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filePath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      visit(filePath)
      continue
    }
    if (!sourceExtensions.has(path.extname(entry.name))) continue

    const source = fs.readFileSync(filePath, 'utf8')
    const relativePath = path.relative(projectRoot, filePath)

    if (/from\s+['"]antd(?:\/[^'"]*)?['"]|require\(\s*['"]antd(?:\/[^'"]*)?['"]\s*\)/.test(source)) {
      violations.push(`${relativePath}: 不允许直接导入 antd`)
    }
    if (/from\s+['"]@ant-design\/icons(?:\/[^'"]*)?['"]/.test(source)) {
      violations.push(`${relativePath}: Purr Icons 已自有化，不允许重新引入 Ant Design Icons`)
    }
    if (/from\s+['"]lucide-react['"]/.test(source)) {
      violations.push(`${relativePath}: 不允许导入已移除的 Lucide 图标库`)
    }
    if (
      filePath.startsWith(path.join(purrComponentsRoot, 'icons') + path.sep) &&
      /\bexport\s+(?:const|function|class)\s+\w+(?:Outlined|Filled)\b/.test(source)
    ) {
      violations.push(`${relativePath}: 自有图标必须使用 *Icon 语义命名`)
    }
    if (
      !filePath.startsWith(purrComponentsRoot + path.sep) &&
      /from\s+['"]@base-ui\/react(?:\/[^'"]*)?['"]/.test(source)
    ) {
      violations.push(`${relativePath}: Base UI 只能在 src/purr-components 内部使用`)
    }
    if (
      !filePath.startsWith(purrComponentsRoot + path.sep) &&
      /from\s+['"][^'"]*\/purr-components\/[^'"]+['"]/.test(source)
    ) {
      violations.push(`${relativePath}: 业务代码必须从 Purr Components 公共出口导入`)
    }
    if (/from\s+['"][^'"]*(?:\/|^)ui(?:\/[^'"]*)?['"]/.test(source)) {
      violations.push(`${relativePath}: 不允许继续引用已移除的 src/ui 目录`)
    }

    for (const match of source.matchAll(legacyComponentClass)) {
      violations.push(`${relativePath}: 发现旧组件类名 ${match[0]}`)
    }
  }
}

validateComponentDirectories()
validateIconDependencies()
validateIconSources()
visit(sourceRoot)

if (violations.length) {
  console.error('Purr Components 边界检查失败：')
  for (const violation of violations) console.error(`- ${violation}`)
  process.exit(1)
}

console.log('Purr Components 边界检查通过')

const fs = require('node:fs')
const path = require('node:path')

const projectRoot = path.resolve(__dirname, '..')
const sourceRoot = path.join(projectRoot, 'src')
const allowedVendorIconFile = path.join(sourceRoot, 'ui', 'icons.tsx')
const sourceExtensions = new Set(['.ts', '.tsx', '.scss', '.css'])
const violations = []

const legacyComponentClass =
  /\b(?:ant|ui)-(?:btn|button|checkbox|drawer|empty|form|input|popover|tag|space|card|list|divider|alert|segmented|progress|switch|slider|typography|radio|select|tabs|modal)(?:[A-Za-z0-9_-]*)\b/g

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
    if (
      filePath !== allowedVendorIconFile &&
      /from\s+['"]@ant-design\/icons['"]/.test(source)
    ) {
      violations.push(`${relativePath}: 图标实现只能在 Purr UI 出口引用`)
    }
    if (/from\s+['"]lucide-react['"]/.test(source)) {
      violations.push(`${relativePath}: 不允许导入已移除的 Lucide 图标库`)
    }
    if (
      !filePath.startsWith(path.join(sourceRoot, 'ui') + path.sep) &&
      /from\s+['"]@base-ui\/react(?:\/[^'"]*)?['"]/.test(source)
    ) {
      violations.push(`${relativePath}: Base UI 只能在 src/ui 内部使用`)
    }

    for (const match of source.matchAll(legacyComponentClass)) {
      violations.push(`${relativePath}: 发现旧组件类名 ${match[0]}`)
    }
  }
}

visit(sourceRoot)

if (violations.length) {
  console.error('Purr UI 边界检查失败：')
  for (const violation of violations) console.error(`- ${violation}`)
  process.exit(1)
}

console.log('Purr UI 边界检查通过')

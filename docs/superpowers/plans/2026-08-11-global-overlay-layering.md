# Global Overlay Layering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `1000–1999` 内建立统一的全局弹出层协议，让所有公共弹层把层级写到真实的层叠根节点，并恢复剧本审阅项单项与批量菜单的可见、可点击行为。

**Architecture:** 用一个全局 SCSS 令牌表提供默认语义层级，用一个很小的 TypeScript helper 负责显式 `zIndex` 的校验和阻塞层前景/遮罩计算。Base UI 仍只存在于 `src/purr-components`，业务组件继续依赖公共封装；不引入动态 LayerProvider、自动递增层级或新依赖。

**Tech Stack:** React 18、TypeScript、SCSS、Base UI 1.6、Node 内置 test runner、现有 Purr Components 边界检查、Vite 浏览器验收。

## Global Constraints

- 全局竞争层级只使用 `1000–1999`；当前默认值固定为：floating `1000`、diagnostics `1050`、drawer backdrop `1100`、drawer `1110`、modal backdrop `1200`、modal `1210`、popup `1220`、tooltip `1230`、toast `1300`。
- 相同语义组件共享权重，不为实例创建 `1211`、`1212` 等编号。
- 独立层叠上下文内的正向局部排序只使用 `0–9`；现有隔离背景使用的 `-1` 只作为背景哨兵，不参与全局优先级。
- Dropdown、ContextMenu、Popover、Popconfirm、Select、MultiSelect、Tooltip、Dialog、Modal、Confirm、Drawer 支持可选 `zIndex?: number`。
- Toast 只允许 `PurrToastProvider` 配置 `zIndex`，单条 toast 不支持覆盖。
- 锚定弹层必须把覆盖值写到 Base UI `Positioner`；Dialog/Drawer 必须把前景值写到 viewport，并让 backdrop 使用 `zIndex - 10`。
- 显式值超出 `1000–1999`、不是整数或不是有限数时仅在开发环境报警；不得静默裁剪，仍应用调用方传值。
- 业务代码不得直接向浮层节点写 `style={{ zIndex }}`；公共组件 helper 是唯一适配边界。
- 不修改审阅裁决 API、审阅状态机或用户现有审阅数据。
- 不新增依赖；复用现有 TypeScript、Node test runner、Base UI 和 Purr Components 检查脚本。

---

## File Map

### New files

- `src/styles/layers.scss`：全局语义层级的唯一数值注册表。
- `src/purr-components/overlayLayer.ts`：显式覆盖校验、普通弹层 style 和阻塞层前景/遮罩 style 计算。
- `src/purr-components/overlayLayer.test.ts`：纯函数层级契约测试。

### Public overlay components

- `src/purr-components/PurrDropdown/PurrDropdown.tsx`
- `src/purr-components/PurrPopover/PurrPopover.tsx`
- `src/purr-components/PurrPopconfirm/PurrPopconfirm.tsx`
- `src/purr-components/PurrSelect/PurrSelect.tsx`
- `src/purr-components/PurrMultiSelect/PurrMultiSelect.tsx`
- `src/purr-components/PurrTooltip/PurrTooltip.tsx`
- `src/purr-components/PurrDialog/PurrDialog.tsx`
- `src/purr-components/PurrModal/PurrModal.tsx`
- `src/purr-components/PurrConfirm/PurrConfirm.tsx`
- `src/purr-components/PurrDrawer/PurrDrawer.tsx`
- `src/purr-components/PurrToast/PurrToast.tsx`

### Shared and business styles

- `src/index.scss`
- `src/purr-components/styles/purr.scss`
- `src/purr-components/styles/dialog.scss`
- `src/purr-components/styles/purr-floating-panel.scss`
- `src/App.scss`
- `src/components/AppHeader/index.scss`
- `src/components/AiDevInspector/index.scss`
- `src/Workspace/CommandPalette/CommandPalette.scss`
- `src/Workspace/DockedPanel.scss`
- `src/Workspace/EditorPanel/GhostCompletion.scss`
- `src/Workspace/EditorPanel/InlineEditLayer.scss`
- `src/Workspace/EditorPanel/index.scss`
- `src/Workspace/OutlinePanel/index.scss`
- `src/Workspace/OutlinePanel/MindMapView.tsx`
- `src/Workspace/OutlinePanel/CharacterTab.tsx`
- `src/Workspace/OutlinePanel/WorldEntityTab.tsx`
- `src/Workspace/diff/diff.scss`
- `src/Workspace/workspaceSearch.scss`

### Guardrails and test wiring

- `scripts/check-purr-components.cjs`
- `package.json`

### Dead files removed during local-layer normalization

- `src/Workspace/FloatingPanel.tsx`
- `src/Workspace/FloatingPanel.scss`

These two files have no imports or render sites. The active floating surface is `PurrFloatingPanel`; keeping the unused implementation would preserve an unbounded `zIndexCounter` and a forbidden business-side inline `zIndex` path.

---

### Task 1: Add the semantic registry and tested layer helper

**Files:**
- Create: `src/styles/layers.scss`
- Create: `src/purr-components/overlayLayer.ts`
- Create: `src/purr-components/overlayLayer.test.ts`
- Modify: `src/index.scss:1`
- Modify: `package.json:23`

**Interfaces:**
- Produces: `isValidOverlayZIndex(zIndex: number): boolean`.
- Produces: `getOverlayLayerStyle(componentName: string, zIndex?: number): React.CSSProperties | undefined`.
- Produces: `getBlockingLayerStyles(componentName: string, zIndex?: number): { backdrop?: React.CSSProperties; surface?: React.CSSProperties }`.
- Produces: CSS variables `--purr-z-floating`, `--purr-z-diagnostics`, `--purr-z-drawer-backdrop`, `--purr-z-drawer`, `--purr-z-modal-backdrop`, `--purr-z-modal`, `--purr-z-popup`, `--purr-z-tooltip`, `--purr-z-toast`.

- [ ] **Step 1: Write the failing helper test**

Create `src/purr-components/overlayLayer.test.ts`:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getBlockingLayerStyles,
  getOverlayLayerStyle,
  isValidOverlayZIndex,
} from './overlayLayer.ts'

test('overlay z-index validation accepts only finite integers from 1000 through 1999', () => {
  assert.equal(isValidOverlayZIndex(1000), true)
  assert.equal(isValidOverlayZIndex(1999), true)
  assert.equal(isValidOverlayZIndex(999), false)
  assert.equal(isValidOverlayZIndex(2000), false)
  assert.equal(isValidOverlayZIndex(1200.5), false)
  assert.equal(isValidOverlayZIndex(Number.NaN), false)
})

test('overlay style preserves explicit values and leaves semantic defaults to CSS', () => {
  assert.equal(getOverlayLayerStyle('PurrTooltip'), undefined)
  assert.deepEqual(getOverlayLayerStyle('PurrTooltip', 1250), { zIndex: 1250 })
  assert.deepEqual(getOverlayLayerStyle('PurrTooltip', 0), { zIndex: 0 })
})

test('blocking layers place the backdrop ten below the requested surface', () => {
  assert.deepEqual(getBlockingLayerStyles('PurrDialog'), {})
  assert.deepEqual(getBlockingLayerStyles('PurrDialog', 1250), {
    backdrop: { zIndex: 1240 },
    surface: { zIndex: 1250 },
  })
})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
node --experimental-strip-types --test src/purr-components/overlayLayer.test.ts
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `src/purr-components/overlayLayer.ts`.

- [ ] **Step 3: Implement the minimal TypeScript helper**

Create `src/purr-components/overlayLayer.ts`:

```ts
import type { CSSProperties } from 'react'

const GLOBAL_LAYER_MIN = 1000
const GLOBAL_LAYER_MAX = 1999

export function isValidOverlayZIndex(zIndex: number): boolean {
  return Number.isFinite(zIndex)
    && Number.isInteger(zIndex)
    && zIndex >= GLOBAL_LAYER_MIN
    && zIndex <= GLOBAL_LAYER_MAX
}

function warnInvalidOverlayZIndex(componentName: string, zIndex: number): void {
  if (!import.meta.env?.DEV || isValidOverlayZIndex(zIndex)) return
  console.warn(
    `[${componentName}] zIndex 应为 ${GLOBAL_LAYER_MIN}–${GLOBAL_LAYER_MAX} 的有限整数；当前值仍会按显式覆盖应用：${String(zIndex)}`,
  )
}

export function getOverlayLayerStyle(
  componentName: string,
  zIndex?: number,
): CSSProperties | undefined {
  if (zIndex === undefined) return undefined
  warnInvalidOverlayZIndex(componentName, zIndex)
  return { zIndex }
}

export function getBlockingLayerStyles(
  componentName: string,
  zIndex?: number,
): { backdrop?: CSSProperties; surface?: CSSProperties } {
  if (zIndex === undefined) return {}
  warnInvalidOverlayZIndex(componentName, zIndex)
  return {
    backdrop: { zIndex: zIndex - 10 },
    surface: { zIndex },
  }
}
```

- [ ] **Step 4: Add the single SCSS registry and root import**

Create `src/styles/layers.scss`:

```scss
:root {
  --purr-z-floating: 1000;
  --purr-z-diagnostics: 1050;
  --purr-z-drawer-backdrop: 1100;
  --purr-z-drawer: 1110;
  --purr-z-modal-backdrop: 1200;
  --purr-z-modal: 1210;
  --purr-z-popup: 1220;
  --purr-z-tooltip: 1230;
  --purr-z-toast: 1300;
}
```

At the top of `src/index.scss`, import the registry before the existing atomics import:

```scss
@use './styles/layers.scss';
@use './styles/atomics.scss';
```

- [ ] **Step 5: Wire the focused test into the existing unit command**

Add `src/purr-components/overlayLayer.test.ts` to the explicit file list in `package.json`'s `test:unit` script. Do not add a new test framework or package.

- [ ] **Step 6: Verify GREEN and type safety**

Run:

```bash
node --experimental-strip-types --test src/purr-components/overlayLayer.test.ts
npm run typecheck
```

Expected: 3 focused tests PASS; TypeScript exits 0.

- [ ] **Step 7: Commit the layer primitives**

```bash
git add src/styles/layers.scss src/index.scss src/purr-components/overlayLayer.ts src/purr-components/overlayLayer.test.ts package.json
git commit -m "feat: add overlay layer primitives"
```

---

### Task 2: Put anchored overlay layers on every Positioner

**Files:**
- Modify: `src/purr-components/PurrDropdown/PurrDropdown.tsx`
- Modify: `src/purr-components/PurrPopover/PurrPopover.tsx`
- Modify: `src/purr-components/PurrPopconfirm/PurrPopconfirm.tsx`
- Modify: `src/purr-components/PurrSelect/PurrSelect.tsx`
- Modify: `src/purr-components/PurrMultiSelect/PurrMultiSelect.tsx`
- Modify: `src/purr-components/PurrTooltip/PurrTooltip.tsx`
- Modify: `src/purr-components/styles/purr.scss:1144-1177`

**Interfaces:**
- Consumes: `getOverlayLayerStyle(componentName, zIndex)` from Task 1.
- Produces: optional `zIndex?: number` on all anchored popup props.
- Produces: `.purr-dropdown__positioner`, `.purr-popover__positioner`, `.purr-select__positioner`, `.purr-tooltip__positioner` as the default CSS layer roots.

- [ ] **Step 1: Add the public prop contracts and verify the old implementation cannot satisfy them**

Add `zIndex?: number` to `PurrDropdownProps`, `DropdownButtonProps`, `PurrPopoverProps`, `PurrPopconfirmProps`, `PurrSelectProps`, and `PurrMultiSelectProps`. Keep the existing `PurrTooltipProps.zIndex` declaration.

Before changing Positioners, run:

```bash
rg -n "Positioner|zIndex" src/purr-components/PurrDropdown/PurrDropdown.tsx src/purr-components/PurrPopover/PurrPopover.tsx src/purr-components/PurrSelect/PurrSelect.tsx src/purr-components/PurrMultiSelect/PurrMultiSelect.tsx src/purr-components/PurrTooltip/PurrTooltip.tsx
```

Expected RED evidence: Tooltip applies `zIndex` to `Popup`; Dropdown and MultiSelect Positioners have no layer class; Popover and Select Positioners have no explicit override style.

- [ ] **Step 2: Move Dropdown and ContextMenu layering to their Positioners**

Import the helper, destructure `zIndex`, compute one style, and use it in both branches:

```tsx
import { getOverlayLayerStyle } from '../overlayLayer'

function DropdownBase({
  children,
  menu,
  placement = 'bottomLeft',
  disabled,
  trigger,
  zIndex,
}: PurrDropdownProps) {
  const layerStyle = getOverlayLayerStyle('PurrDropdown', zIndex)

  // ContextMenu branch
  <ContextMenu.Positioner
    className="purr-dropdown__positioner"
    style={layerStyle}
  >

  // Menu branch
  <Menu.Positioner
    className="purr-dropdown__positioner"
    style={layerStyle}
    side={position.side}
    align={position.align}
    sideOffset={6}
  >
}
```

Pass `zIndex` through `PurrDropdown.Button` to its inner `DropdownBase`:

```tsx
<DropdownBase menu={menu} trigger={trigger} placement={placement} zIndex={zIndex}>
```

- [ ] **Step 3: Put Popover and Popconfirm overrides on the Popover Positioner**

In `PurrPopover.tsx`, import the helper, destructure `zIndex`, and update the existing Positioner:

```tsx
<BasePopover.Positioner
  className="purr-popover__positioner"
  style={getOverlayLayerStyle('PurrPopover', zIndex)}
  side={position.side}
  align={position.align}
  sideOffset={8 + (align?.offset?.[1] ?? 0)}
  alignOffset={align?.offset?.[0]}
>
```

In `PurrPopconfirm.tsx`, pass its public prop through without a second implementation:

```tsx
<PurrPopover
  zIndex={zIndex}
  open={open}
  onOpenChange={setOpen}
  placement={placement}
  disabled={disabled}
```

The existing `content` and child nodes remain byte-for-byte unchanged; this edit only adds the `zIndex` prop and its destructuring entry.

- [ ] **Step 4: Put Select and MultiSelect overrides on their Positioners**

In `PurrSelect.tsx`, destructure `zIndex` and change the existing Positioner:

```tsx
<BaseSelect.Positioner
  className="purr-select__positioner"
  style={getOverlayLayerStyle('PurrSelect', zIndex)}
  side="bottom"
  align="start"
  sideOffset={4}
  alignItemWithTrigger={false}
>
```

`mode="tags"` has no Portal popup, so it accepts the compatibility prop but performs no layer write.

In `PurrMultiSelect.tsx`, destructure `zIndex` and add the missing root class and style:

```tsx
<BaseSelect.Positioner
  className="purr-select__positioner"
  style={getOverlayLayerStyle('PurrMultiSelect', zIndex)}
  side="bottom"
  align="start"
  sideOffset={4}
>
```

- [ ] **Step 5: Move Tooltip override from Popup to Positioner**

Keep content styles on Popup and make the Positioner the only global layer root:

```tsx
<BaseTooltip.Positioner
  className="purr-tooltip__positioner"
  style={getOverlayLayerStyle('PurrTooltip', zIndex)}
  side={position.side}
  align={position.align}
  sideOffset={8}
>
  <BaseTooltip.Popup
    className={['purr-tooltip', className].filter(Boolean).join(' ')}
    style={{ ...styles?.root, ...styles?.container }}
  >
```

- [ ] **Step 6: Move semantic defaults from Popup content to Positioner roots**

Replace the old numeric popup rules in `src/purr-components/styles/purr.scss` with:

```scss
.purr-dropdown__positioner,
.purr-popover__positioner,
.purr-select__positioner {
  z-index: var(--purr-z-popup);
}

.purr-tooltip__positioner {
  z-index: var(--purr-z-tooltip);
}

.purr-tooltip,
.purr-popover,
.purr-dropdown,
.purr-select__popup {
  color: var(--text-primary);
}
```

Keep every current visual declaration in that selector group; remove only its numeric `z-index` declaration.

- [ ] **Step 7: Verify the anchored component contract**

Run:

```bash
npm run typecheck
node --experimental-strip-types --test src/purr-components/overlayLayer.test.ts
rg -n "purr-(dropdown|popover|select|tooltip)__positioner" src/purr-components
rg -n "z-index: (1040|1050)" src/purr-components/styles/purr.scss
```

Expected: typecheck and tests PASS; each Positioner class is present; the final `rg` returns no matches.

- [ ] **Step 8: Commit anchored overlays**

```bash
git add src/purr-components/PurrDropdown/PurrDropdown.tsx src/purr-components/PurrPopover/PurrPopover.tsx src/purr-components/PurrPopconfirm/PurrPopconfirm.tsx src/purr-components/PurrSelect/PurrSelect.tsx src/purr-components/PurrMultiSelect/PurrMultiSelect.tsx src/purr-components/PurrTooltip/PurrTooltip.tsx src/purr-components/styles/purr.scss
git commit -m "fix: layer anchored overlays at their positioners"
```

---

### Task 3: Apply the blocking-layer contract and repair Toast

**Files:**
- Modify: `src/purr-components/PurrDialog/PurrDialog.tsx`
- Modify: `src/purr-components/PurrModal/PurrModal.tsx`
- Modify: `src/purr-components/PurrConfirm/PurrConfirm.tsx`
- Modify: `src/purr-components/PurrDrawer/PurrDrawer.tsx`
- Modify: `src/purr-components/PurrToast/PurrToast.tsx`
- Modify: `src/purr-components/styles/dialog.scss:27-40,128-136`
- Modify: `src/purr-components/styles/purr.scss:1076-1142`

**Interfaces:**
- Consumes: `getBlockingLayerStyles(componentName, zIndex)` and `getOverlayLayerStyle(componentName, zIndex)` from Task 1.
- Produces: `PurrDialogProps.zIndex`, `PurrModalProps.zIndex`, `PurrConfirmOptions.zIndex`, `PurrDrawerProps.zIndex`, `PurrToastProviderProps.zIndex`.
- Produces: corrected Toast DOM classes `.purr-toast-region`, `.purr-toast`, `.purr-toast--{level}`.

- [ ] **Step 1: Add Dialog foreground/backdrop support**

Add `zIndex?: number`, compute once, and apply the two returned styles:

```tsx
const layerStyles = getBlockingLayerStyles('PurrDialog', zIndex)

<BaseDialog.Backdrop
  className="purr-dialog-backdrop"
  style={layerStyles.backdrop}
/>
<BaseDialog.Viewport
  className="purr-dialog-viewport"
  style={layerStyles.surface}
>
```

- [ ] **Step 2: Pass the Modal and Confirm public APIs into Dialog**

Add `zIndex?: number` to `PurrModalProps` and pass it unchanged:

```tsx
<PurrDialog
  zIndex={zIndex}
  open={open}
  title={title}
```

Add `zIndex?: number` to `PurrConfirmOptions`, then pass each invocation's value:

```tsx
<PurrDialog
  zIndex={options.zIndex}
  open
  title={options.title}
```

- [ ] **Step 3: Add Drawer foreground/backdrop support**

Add `zIndex?: number`, compute with the Drawer component name, and apply it to the existing Backdrop and Viewport:

```tsx
const layerStyles = getBlockingLayerStyles('PurrDrawer', zIndex)

<BaseDialog.Backdrop
  className="purr-dialog-backdrop purr-drawer-backdrop"
  style={layerStyles.backdrop}
/>
<BaseDialog.Viewport
  className="purr-drawer-viewport"
  style={layerStyles.surface}
>
```

- [ ] **Step 4: Replace blocking-layer numeric defaults with semantic variables**

Use these exact defaults in `dialog.scss`:

```scss
.purr-dialog-backdrop { z-index: var(--purr-z-modal-backdrop); }
.purr-dialog-viewport { z-index: var(--purr-z-modal); }
.purr-drawer-backdrop { z-index: var(--purr-z-drawer-backdrop); }
.purr-drawer-viewport { z-index: var(--purr-z-drawer); }
```

Keep `.purr-drawer-backdrop` after `.purr-dialog-backdrop` so the more specific Drawer default wins.

- [ ] **Step 5: Repair Toast classes and add Provider-level override**

Define the Provider props and apply only the region-level override:

```tsx
export interface PurrToastProviderProps {
  children: React.ReactNode
  zIndex?: number
}
```

Change the Provider signature without changing its queue, timeout, or dismiss logic:

```diff
-export function PurrToastProvider({ children }: { children: React.ReactNode }) {
+export function PurrToastProvider({ children, zIndex }: PurrToastProviderProps) {
```

Replace the region opening tag with:

```tsx
<div
  className="purr-toast-region"
  style={getOverlayLayerStyle('PurrToastProvider', zIndex)}
  aria-live="polite"
  aria-atomic="true"
>
```

Replace each message opening tag with:

```tsx
<div
  key={message.key}
  className={`purr-toast purr-toast--${message.level}`}
  role={message.level === 'error' ? 'alert' : 'status'}
>
```

Change `.purr-toast-region` to `z-index: var(--purr-z-toast)`; do not add per-message `zIndex`.

- [ ] **Step 6: Verify blocking and feedback components**

Run:

```bash
npm run typecheck
node --experimental-strip-types --test src/purr-components/overlayLayer.test.ts
rg -n "purr-purrToast|z-index: (1000|1001|1100)" src/purr-components/PurrToast src/purr-components/styles/dialog.scss src/purr-components/styles/purr.scss
```

Expected: typecheck and tests PASS; the final `rg` returns no obsolete Toast class or numeric blocking-layer rule.

- [ ] **Step 7: Commit blocking overlays and Toast**

```bash
git add src/purr-components/PurrDialog/PurrDialog.tsx src/purr-components/PurrModal/PurrModal.tsx src/purr-components/PurrConfirm/PurrConfirm.tsx src/purr-components/PurrDrawer/PurrDrawer.tsx src/purr-components/PurrToast/PurrToast.tsx src/purr-components/styles/dialog.scss src/purr-components/styles/purr.scss
git commit -m "feat: unify blocking overlay and toast layers"
```

---

### Task 4: Migrate remaining layers and make the boundary check enforce the protocol

**Files:**
- Modify: `scripts/check-purr-components.cjs`
- Modify: `src/App.scss`
- Modify: `src/components/AppHeader/index.scss`
- Modify: `src/components/AiDevInspector/index.scss`
- Modify: `src/purr-components/styles/purr-floating-panel.scss`
- Modify: `src/Workspace/CommandPalette/CommandPalette.scss`
- Modify: `src/Workspace/DockedPanel.scss`
- Modify: `src/Workspace/EditorPanel/GhostCompletion.scss`
- Modify: `src/Workspace/EditorPanel/InlineEditLayer.scss`
- Modify: `src/Workspace/EditorPanel/index.scss`
- Modify: `src/Workspace/OutlinePanel/index.scss`
- Modify: `src/Workspace/OutlinePanel/MindMapView.tsx`
- Modify: `src/Workspace/OutlinePanel/CharacterTab.tsx`
- Modify: `src/Workspace/OutlinePanel/WorldEntityTab.tsx`
- Modify: `src/Workspace/diff/diff.scss`
- Modify: `src/Workspace/workspaceSearch.scss`
- Delete: `src/Workspace/FloatingPanel.tsx`
- Delete: `src/Workspace/FloatingPanel.scss`

**Interfaces:**
- Consumes: all CSS variables from Task 1.
- Produces: `npm run check:purr-components` enforcement for the exact registry, raw CSS layer values, and business-side inline `style.zIndex`.
- Produces: no unbounded or arbitrary global z-index values outside `src/styles/layers.scss`.

- [ ] **Step 1: Add the registry and raw-value checks before migration**

At the top of `scripts/check-purr-components.cjs`, use the already-installed TypeScript parser and declare the required registry:

```js
const ts = require('typescript')

const layerRegistryPath = path.join(sourceRoot, 'styles', 'layers.scss')
const requiredLayerTokens = new Map([
  ['--purr-z-floating', 1000],
  ['--purr-z-diagnostics', 1050],
  ['--purr-z-drawer-backdrop', 1100],
  ['--purr-z-drawer', 1110],
  ['--purr-z-modal-backdrop', 1200],
  ['--purr-z-modal', 1210],
  ['--purr-z-popup', 1220],
  ['--purr-z-tooltip', 1230],
  ['--purr-z-toast', 1300],
])
```

Add these validators and call `validateLayerRegistry()` before `visit(sourceRoot)`:

```js
function validateLayerRegistry() {
  const source = fs.readFileSync(layerRegistryPath, 'utf8')
  for (const [token, value] of requiredLayerTokens) {
    const declaration = new RegExp(`${token}\\s*:\\s*${value}\\s*;`)
    if (!declaration.test(source)) {
      violations.push(`src/styles/layers.scss: ${token} 必须固定为 ${value}`)
    }
  }
}

function validateNumericZIndex(source, relativePath) {
  for (const match of source.matchAll(/z-index\s*:\s*(-?\d+)\b/g)) {
    const value = Number(match[1])
    if (value < -1 || value > 9) {
      violations.push(`${relativePath}: z-index ${value} 必须改用全局语义变量或局部 -1–9`)
    }
  }
}

function validateInlineBusinessZIndex(source, filePath, relativePath) {
  if (filePath.startsWith(purrComponentsRoot + path.sep)) return
  if (path.extname(filePath) !== '.tsx') return
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  )
  function inspect(node) {
    if (ts.isJsxAttribute(node) && node.name.text === 'style') {
      const expression = node.initializer?.expression
      if (expression && ts.isObjectLiteralExpression(expression)) {
        const hasZIndex = expression.properties.some((property) => {
          if (!ts.isPropertyAssignment(property) && !ts.isShorthandPropertyAssignment(property)) return false
          return property.name?.getText(sourceFile) === 'zIndex'
        })
        if (hasZIndex) {
          violations.push(`${relativePath}: 业务浮层不得直接写 style.zIndex，请使用 Purr 组件的 zIndex 参数`)
        }
      }
    }
    ts.forEachChild(node, inspect)
  }
  inspect(sourceFile)
}
```

Inside `visit`, after reading each supported source file, call both per-file validators.

- [ ] **Step 2: Run the boundary check and verify RED**

Run:

```bash
npm run check:purr-components
```

Expected: FAIL listing current raw values such as App `200`, Command Palette `1300`, AiDevInspector `1400`, inline-edit `1100/1200`, mind-map `9999`, and the unused Workspace FloatingPanel inline `zIndex`.

- [ ] **Step 3: Replace remaining global surfaces with their semantic variables**

Apply this exact mapping:

```text
src/App.scss .app-settings-overlay                         -> var(--purr-z-modal)
src/components/AiDevInspector/index.scss                  -> var(--purr-z-diagnostics)
src/purr-components/styles/purr-floating-panel.scss       -> var(--purr-z-floating)
src/Workspace/CommandPalette/CommandPalette.scss          -> var(--purr-z-modal)
src/Workspace/EditorPanel/GhostCompletion.scss            -> var(--purr-z-floating)
src/Workspace/EditorPanel/InlineEditLayer.scss toolbar    -> var(--purr-z-floating)
src/Workspace/EditorPanel/InlineEditLayer.scss popover    -> var(--purr-z-floating)
src/Workspace/EditorPanel/index.scss .ai-float-box         -> var(--purr-z-floating)
src/Workspace/workspaceSearch.scss                        -> var(--purr-z-floating)
src/Workspace/OutlinePanel/MindMapView.tsx note tooltip   -> var(--purr-z-tooltip)
```

In the mind-map third-party configuration, change `nodeTextEditZIndex: 1000` to local `nodeTextEditZIndex: 9`. Remove the obsolete `zIndex={1301}` props from `CharacterTab.tsx` and `WorldEntityTab.tsx`; the fixed Tooltip Positioner default now provides `1230`.

- [ ] **Step 4: Normalize remaining positive local values to `0–9`**

Apply these local replacements only after confirming their existing parent stacking contexts remain intact:

```text
src/App.scss .app-global-actions               100 -> 8
src/App.scss .config-pop                        100 -> 1
src/App.scss .workspace-chapter-hover-panel      12 -> 1
src/App.scss .workspace-editor-hover-panel       12 -> 1
src/App.scss .divider                            10 -> 2
src/components/AppHeader/index.scss              20 -> 8
src/Workspace/DockedPanel.scss fullscreen        24 -> 9
src/Workspace/OutlinePanel/index.scss switcher   100 -> 1
src/Workspace/diff/diff.scss overlay              10 -> 9
```

Keep already-valid local values `-1` and `0–9` unchanged.

- [ ] **Step 5: Remove the unused unbounded floating-panel implementation**

First prove it has no caller:

```bash
rg -n "from ['\"]\./FloatingPanel|Workspace/FloatingPanel|<FloatingPanel" src
```

Expected: no imports or render sites; only the component definition itself may match.

Delete `src/Workspace/FloatingPanel.tsx` and `src/Workspace/FloatingPanel.scss`. In `src/Workspace/EditorPanel/index.scss`, remove the stale comment that refers to `.floating-rail--right` and `z-index: 30`; that rail has no render site.

- [ ] **Step 6: Run the boundary check and verify GREEN**

Run:

```bash
npm run check:purr-components
rg -n --glob '*.{css,scss,tsx,ts}' "z-index\s*:\s*(?:[1-9][0-9]|[0-9]{3,})|style=\{\{[^}]*zIndex" src
npm run typecheck
```

Expected: boundary check and typecheck PASS; the audit `rg` returns no unclassified raw global/local value or business inline layer style. CSS variables in `src/styles/layers.scss` are custom-property values and are not matched by the `z-index:` audit.

- [ ] **Step 7: Commit migration and guardrail together**

```bash
git add scripts/check-purr-components.cjs src/App.scss src/components/AppHeader/index.scss src/components/AiDevInspector/index.scss src/purr-components/styles/purr-floating-panel.scss src/Workspace/CommandPalette/CommandPalette.scss src/Workspace/DockedPanel.scss src/Workspace/EditorPanel/GhostCompletion.scss src/Workspace/EditorPanel/InlineEditLayer.scss src/Workspace/EditorPanel/index.scss src/Workspace/OutlinePanel/index.scss src/Workspace/OutlinePanel/MindMapView.tsx src/Workspace/OutlinePanel/CharacterTab.tsx src/Workspace/OutlinePanel/WorldEntityTab.tsx src/Workspace/diff/diff.scss src/Workspace/workspaceSearch.scss src/Workspace/FloatingPanel.tsx src/Workspace/FloatingPanel.scss
git commit -m "refactor: enforce semantic overlay layers"
```

---

### Task 5: Verify review adjudication and all release gates

**Files:**
- Verify only: `src/ScreenplayAgentPage/ReviewAdjudicationPanel.tsx`
- Verify only: `src/ScreenplayAgentPage/index.tsx`
- Verify only: all files changed by Tasks 1–4

**Interfaces:**
- Consumes: the existing `ReviewAdjudicationPanel` Dropdown flows and existing `adjudicateReview` service call.
- Produces: evidence that visual stacking and pointer hit-testing are fixed without changing review data.

- [ ] **Step 1: Run focused and full automated checks**

Run:

```bash
node --experimental-strip-types --test src/purr-components/overlayLayer.test.ts
npm run check:purr-components
npm run typecheck
npm run test:unit
npm run build:web
git diff --check
```

Expected: every command exits 0. If `build:web` produces ignored build output, do not stage it.

- [ ] **Step 2: Start an isolated browser-verification environment**

Use a copied database and unused ports so the user's normal `5173/18321` services and persisted review state are untouched:

```bash
PURR_LAYER_TEST_DATA=$(mktemp -d /tmp/purr-layer-test.XXXXXX)
cp "/Users/liuyubin/Library/Application Support/purrtypos/purrtypos.db" "$PURR_LAYER_TEST_DATA/purrtypos.db"
PURRTYPOS_DATA_DIR="$PURR_LAYER_TEST_DATA" PURRTYPOS_PORT=18322 PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python node scripts/run-web-backend.cjs
VITE_API_BASE_URL=http://127.0.0.1:18322 npx vite --host 127.0.0.1 --port 5174 --strictPort
```

Run backend and Vite in separate managed terminal sessions. If either port is occupied, select the next free pair and update `VITE_API_BASE_URL` consistently.

- [ ] **Step 3: Verify the exact review-dropdown failure path without persisting a decision**

Open `http://127.0.0.1:5174`, enter the screenplay project containing pending review findings, and open “审阅与定稿”. Then perform both paths:

1. Open a finding's current-status button. Confirm the menu is visibly above the modal and clickable.
2. In DevTools or browser evaluation, select `.purr-dropdown__positioner`, assert `getComputedStyle(positioner).zIndex === '1220'`, and assert `document.elementFromPoint()` at a menu item's center returns the item or one of its descendants.
3. Choose a different status and confirm the nested decision Modal opens. Click “取消” so the copied database is not mutated.
4. Select at least one pending finding, open “批量处理”, choose a status, confirm the batch decision Modal opens, then click “取消”.

Expected: neither menu click falls through to `.screenplay-review-finding__content`; both existing flows reach their confirmation Modal.

- [ ] **Step 4: Verify the layer hierarchy and explicit escape hatch**

Using existing product surfaces, verify:

- Dialog backdrop computes to `1200`, Dialog viewport to `1210`.
- Drawer backdrop computes to `1100`, Drawer viewport to `1110`.
- Dropdown/Popover/Select/MultiSelect Positioners compute to `1220`.
- Tooltip Positioner computes to `1230`.
- Toast region computes to `1300` and uses `.purr-toast-region` / `.purr-toast` classes.
- AiDevInspector computes to `1050` and remains below an open Dialog.

Temporarily exercise component props through an existing local call site or browser component state with `zIndex={1250}`; verify the real Positioner or viewport computes to `1250`. Revert the temporary call-site edit before continuing. Do not commit a demo harness.

- [ ] **Step 5: Stop every process started for verification**

Terminate the two managed sessions, then verify the selected ports have no listeners:

```bash
lsof -nP -iTCP:5174 -sTCP:LISTEN
lsof -nP -iTCP:18322 -sTCP:LISTEN
```

Expected: both commands return no listeners. Remove the temporary copied-data directory only after resolving its exact path from `PURR_LAYER_TEST_DATA`; the original application data directory must not be touched.

- [ ] **Step 6: Inspect final scope and commit only if browser verification required a real fix**

Run:

```bash
git status --short
git diff --stat
git diff --check
```

Expected: clean worktree after the four planned commits. If browser verification exposed a real implementation defect, add one focused regression check, apply the smallest shared-component fix, rerun Steps 1–5, and commit only those files with a message describing that defect.

---

## Completion Criteria

- The review finding status menu and batch menu are above the review Modal and receive pointer events.
- All public popup components in scope expose the approved optional `zIndex` contract at the correct stacking root.
- Defaults match the approved `1000–1300` semantic table, and no current component uses a global value above `1300`.
- Toast's DOM classes match its SCSS selectors.
- `npm run check:purr-components` prevents raw layer numbers and business-side inline `zIndex` from returning.
- The existing review business flow and persisted user database remain unchanged.
- Automated checks, browser hit-testing, build, and port cleanup all pass.

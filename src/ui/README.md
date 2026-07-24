# Purr UI

Purr UI 是 PurrTypos 自有的通用组件库。所有基础交互、视觉状态和无障碍行为都应在这里实现，业务页面只负责布局和领域逻辑。

## 边界

- `src/ui`：无业务含义的基础组件、弹层、反馈、表单与图标出口。
- `src/components`：跨页面复用、但带有产品语义的组合组件。
- 页面和 `Workspace` 子目录：领域组件，不应反向进入基础组件库。

## 约定

- DOM 类名统一使用 `purr-*` 品牌命名空间。
- 组件通过 `src/ui/index.ts` 统一导出。
- 业务代码不得直接导入 `antd`、`@base-ui/react`、`@ant-design/icons` 或其他图标供应方。
- `@ant-design/icons` 仅允许在 `src/ui/icons.tsx` 中作为图形供应方使用；业务代码仍统一从 `src/ui` 导入图标。
- 第三方无样式行为库只能在 `src/ui` 内部使用，并由 Purr UI 掌控 DOM、样式和公开 API。
- 业务样式可以通过组件根 `className` 和公开的 `purr-*` 结构类组合布局，但不得依赖第三方类名。

运行 `npm run check:purr-ui` 可以检查这些边界。

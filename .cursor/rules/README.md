# Cursor Rules 索引

规则文件位于 `.cursor/rules/`，按 **类型** 命名，便于查找与维护。

| 类型 | 文件 | 说明 |
|------|------|------|
| **Ant Design** | `antd.mdc` | 优先使用 antd；表单、输入、弹窗、列表等约定 |
| **设计 · Token** | `design-tokens.mdc` | 字体、颜色、间距等 CSS 变量，禁止硬编码 |
| **设计 · 图标** | `design-icons.mdc` | 图标与文字同色，禁止单独语义色 |
| **UI · 热区** | `ui-hot-zone.mdc` | 点击区域尺寸与 hover，原子类 `hot-zone-*` |
| **UI · 操作按钮** | `ui-actions.mdc` | 图标按钮、Tooltip、添加/删除按钮样式 |
| **样式 · Sass** | `style-sass-use.mdc` | `@use` / 禁止 `@import` |
| **React** | `react-hooks.mdc` | `React.useXxx` 与导入规范 |

## 说明

- `alwaysApply: true` 的规则会在每次对话中自动带上（当前为 `antd.mdc`、`design-icons.mdc`）。
- 其余规则在匹配 `globs` 或手动启用时生效。
- 本文件为人工维护目录，**不参与** Cursor 规则解析。

# Purr Components

Purr Components 是 PurrTypos 的前端基础设施层。它提供无业务含义、可在任意页面复用的组件、交互能力、反馈机制和图标出口；页面只负责布局和领域逻辑。

## 分层边界

- `src/purr-components`：底层设计系统和交互基础设施。
- `src/components`：跨页面复用、但带有产品语义或业务规则的组合组件。
- 页面和 `Workspace` 子目录：具体领域组件。

判断一个组件是否属于底层 Purr Components，可以问三个问题：

1. 它是否能脱离“书籍、剧本、章节、工作台”等产品概念独立使用？
2. 它的属性是否描述通用交互，而不是业务数据和业务动作？
3. 它是否值得成为所有业务页面共同依赖的稳定 API？

只有三个答案都为“是”，才进入 Purr Components。`PurrButton`、`PurrInput`、`PurrSteps` 和 `PurrFloatingPanel` 属于这一层；`AppHeader`、`ExportModal` 和带删除规则的 `ConfirmModal` 仍属于业务组合层。

## 源码结构

源码参考 MUI、Chakra UI 和 Radix Primitives 的组织方式：分类只用于文档导航，组件源码不按 `data-entry`、`display` 等概念继续嵌套，而是在同一层为每个组件建立独立目录。

```text
src/purr-components/
├── PurrButton/
│   ├── PurrButton.tsx
│   └── index.ts
├── PurrInput/
│   ├── PurrInput.tsx
│   └── index.ts
├── PurrChoiceCard/
│   ├── PurrChoiceCard.tsx
│   └── index.ts
├── PurrSteps/
│   ├── PurrSteps.tsx
│   └── index.ts
├── PurrCollapse/
│   ├── PurrCollapse.tsx
│   └── index.ts
├── icons/
├── styles/
└── index.ts
```

- 每个 `Purr*` 目录只负责一个公开组件或一个紧密组合的组件家族。
- 组件实现、类型、后续测试和组件专属样式都放在自己的目录中。
- 组件目录的 `index.ts` 是组件级出口。
- 根目录 `index.ts` 是业务代码唯一使用的公共出口。
- `icons` 和 `styles` 只存放真正跨组件共享的基础资源。

## 图标规范

- 图标视觉语言统一为 **Purr Softline（柔线）**：温和、清晰、轻量，以轮廓识别为主。
- 图标由项目内 SVG 实现，不依赖第三方图标组件库。
- 基础图标使用 24 × 24 光学网格、1.75 描边、圆角线帽和圆角转角。
- 常规图标只使用统一的 `PurrIcon` 画布，不自行定义 SVG 画布或描边。
- 默认使用线性轮廓；实心只用于小型识别点或明确的激活、停止状态。
- 不使用虚线、阴影、渐变和无语义装饰，优先保证 16px 下仍然清晰。
- 公共名称使用 `ArrowLeftIcon`、`CheckCircleIcon` 这样的语义命名，不使用供应商特有的 `Outlined`、`Filled` 命名体系。
- `PurrIcon` 统一处理尺寸、旋转动画、样式扩展和无障碍属性。
- 业务代码仍统一从 `@/purr-components` 导入图标。

完整的造型、尺寸和验收标准见 [`icons/STYLE.md`](./icons/STYLE.md)。

## 约定

- 公开组件和公开组件类型使用 `Purr` 前缀，Hook 使用 `usePurr*`。
- 图标名称保持通用语义，不重复添加 `Purr` 前缀。
- DOM 类名统一使用 `purr-*` 品牌命名空间。
- 业务代码统一从 `@/purr-components` 导入，不深入引用内部文件。
- 项目不得引入 `antd`、`@ant-design/icons` 或其他第三方图标组件库。
- `@base-ui/react` 只能作为 Purr Components 内部的无样式行为基础。
- 第三方无样式行为库只能在 Purr Components 内部使用，由 Purr 掌控 DOM、样式和公开 API。

## 单选控件

- `PurrRadio` 用于紧凑的行内单选，选中状态统一为圆形对勾。
- `PurrRadio.Button` 用于分段式短选项，不显示额外选择标记。
- `PurrChoiceCard` 用于“标题 + 说明”的单选卡片，与 `PurrRadio` 共用选择标记和原生单选语义。

## 折叠内容

- `PurrCollapse` 用于页面内按需展开的补充内容，支持多项独立展开或手风琴模式。
- 业务页面不直接实现 `details/summary` 的替代样式；新交互统一复用该组件。

运行 `npm run check:purr-components` 可以检查这些边界。

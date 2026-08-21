# PurrA 独立仓库迁移：阶段 C 验证记录

> 日期：2026-08-21
>
> 结论：PurrA 已成为独立公开仓库；PurrTypos 已删除内置源码并锁定外部提交。

## 权威来源

- 公开仓库：`https://github.com/Lybrands/purra`；
- 许可证：MIT；
- PurrTypos 锁定提交：`fdbada4f38b55aacbd66a57e5ba6f3cc7a3c2569`；
- 集中依赖文件：`backend/requirements-purra.txt`；
- PurrA 包级结构、产品中立性和一致性测试由独立仓库拥有。

## PurrA 验证

- 独立测试：201 passed；
- GitHub Actions：通过；
- wheel 与 sdist：构建通过；
- 干净环境 wheel 安装及公共 API 冒烟：通过；
- Runtime/README 产品中立性门禁通过；个人邮箱和强凭据特征扫描无命中。

## PurrTypos 验证

- `.venv` 中的 PurrA 从 `site-packages` 加载，不再是 editable Monorepo 包；
- 宿主公共导入和组合边界测试：39 passed；
- 全部宿主后端测试：1535 passed；
- 前端/Electron 单元测试：387 passed；
- `npm run check:agent-refactor`：通过；
- `npm run prepare:backend-resources`：通过，PurrA 展开到
  `build-resources/backend/purra`；
- `npm run build:web`：通过，1098 modules transformed；
- `packages/purra`：已删除；
- 源码路径注入扫描：除反向断言外无命中；
- `git diff --check`：通过。

## 未覆盖项

- macOS 上未执行 Windows PyInstaller 构建；其安装入口已统一为
  `backend/requirements.txt`；
- 未创建 PurrA tag 或 GitHub Release，当前以完整提交 SHA 锁定；
- 未使用真实 Provider 凭据执行 E2E；它仍是 PurrTypos 产品发布门禁，不属于拆仓成功
  的替代证据。

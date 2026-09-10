# 续写入口界面与返回路径修正

来源库卡片的创建入口原先将 returnTo 写为作品分析详情路径；书架的续写页面又直接 navigate(returnTo)，因此点击返回并不执行历史后退。现记录实际入口 pathname/search/hash，从来源页进入的续写流程返回及取消执行 navigate(-1)。书架本地展开的续写流程仍关闭表单回到书架。

从指定作品进入时显示固定来源名称并限制来源变更；从书架通用入口进入时保留来源选择。来源版本、正式分析与章末分叉点全部使用 PurrSelect。原先 continuation-wizard 下所有 label 都被设为 grid，覆盖 PurrCheckbox 外壳，现仅 continuation-field 使用网格布局。切换来源/版本同时清除下游分析与章节选项。

验证：类型检查、Purr Components 边界检查、466 项前端回归与 git diff --check 通过。浏览器在 localhost:5174 验证来源库卡片进入、固定来源、公共版本菜单选择 v1、分卷同行布局；顶部返回与取消均回到 /novel-sources。没有提交预览或创建作品，没有修改来源文件、模型配置或调用 Provider。

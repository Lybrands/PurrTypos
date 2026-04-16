/**
 * 提示词模版：内置模版与占位符自动填充。
 *
 * 占位符约定（仅自动填充，非用户手填）：
 *   {书名}         → 当前书籍名
 *   {当前章节}     → 当前章节标题
 *   {关联章节}     → 已勾选的关联章节标题（顿号分隔，空时退化）
 *   {关联大纲}     → 已勾选的关联大纲标题（顿号分隔，空时退化）
 *
 * 兼容 lowerCamelCase 别名：{bookTitle} {chapterTitle} {associatedChapters} {associatedOutlines}
 *
 * 内置模版来自常见写作辅助场景，字段与用户自定义模版结构一致，仅靠 builtin 标记区分只读。
 */

export interface PromptTemplateItem {
  /** 数据库自增 id；内置模版使用负值避免与用户模版冲突 */
  id: number;
  title: string;
  content: string;
  sort?: number;
  /** 只读内置标记；用户模版始终为 false */
  builtin?: boolean;
}

/** 可用占位符清单：用于模版编辑器快捷插入与 UI 提示 */
export interface PromptPlaceholderSpec {
  /** 最终写入内容的占位字符，如 "{书名}" */
  token: string;
  /** UI 上显示的简短标签 */
  label: string;
  /** 悬浮提示 */
  description: string;
}

export const PROMPT_PLACEHOLDERS: PromptPlaceholderSpec[] = [
  {
    token: "{书名}",
    label: "书名",
    description: "当前书籍名称",
  },
  {
    token: "{当前章节}",
    label: "当前章节",
    description: "当前正在写作的章节标题",
  },
  {
    token: "{关联章节}",
    label: "关联章节",
    description: "输入框上方「关联章节」选中的标题（顿号分隔）",
  },
  {
    token: "{关联大纲}",
    label: "关联大纲",
    description: "输入框上方「关联大纲」选中的标题（顿号分隔）",
  },
];

export const BUILTIN_PROMPT_TEMPLATES: PromptTemplateItem[] = [
  {
    id: -1,
    title: "续写本章（300 字）",
    content:
      "请沿《{书名}·{当前章节}》现有情节与语气，续写 300 字左右的正文。\n要求：\n- 遵循本章既有叙述人称与节奏；\n- 推进一个小冲突或情绪变化；\n- 不要总结、不要元叙述。",
    builtin: true,
  },
  {
    id: -2,
    title: "给出 3 个反转方向",
    content:
      "基于当前章节《{当前章节}》以及关联上下文：{关联章节}\n请给出 3 个可落地的反转方向，每条包含：\n1) 反转点一句话；\n2) 前置伏笔；\n3) 对人物弧光的影响。",
    builtin: true,
  },
  {
    id: -3,
    title: "审校穿帮与矛盾",
    content:
      "请审校《{书名}·{当前章节}》是否存在与以下大纲/章节冲突：\n关联大纲：{关联大纲}\n关联章节：{关联章节}\n\n输出：\n- 时间线 / 人物设定 / 物件逻辑 任一维度的穿帮；\n- 每条指出具体句子并给出修改建议。",
    builtin: true,
  },
  {
    id: -4,
    title: "人物对话打磨",
    content:
      "请在不改动情节的前提下，润色《{当前章节}》中的人物对话，让每个角色的语气更贴合其身份与性格；\n同时指出 2–3 处最值得保留/重写的段落。",
    builtin: true,
  },
  {
    id: -5,
    title: "起 3 个章节标题",
    content:
      "基于《{书名}》当前章节《{当前章节}》的内容，给出 3 个更有张力的标题候选，每条 8 字以内，并附一句选择理由。",
    builtin: true,
  },
  {
    id: -6,
    title: "下一章大纲",
    content:
      "基于关联大纲：{关联大纲}\n以及已写章节：{关联章节}\n请为《{书名}》的下一章产出 3 段式大纲（开局/冲突/结尾钩子），每段 60–100 字。",
    builtin: true,
  },
];

/** 构造替换上下文 */
export interface PromptTemplateContext {
  bookTitle?: string | null;
  currentChapterTitle?: string | null;
  associatedChapterTitles?: string[];
  associatedOutlineTitles?: string[];
}

/** 将内容中的自动占位符替换为上下文值 */
export function applyPromptPlaceholders(
  content: string,
  ctx: PromptTemplateContext,
): string {
  if (!content) return content;
  const book = (ctx.bookTitle || "").trim();
  const chapter = (ctx.currentChapterTitle || "").trim();
  const chapters = (ctx.associatedChapterTitles ?? [])
    .map((s) => s.trim())
    .filter(Boolean)
    .join("、");
  const outlines = (ctx.associatedOutlineTitles ?? [])
    .map((s) => s.trim())
    .filter(Boolean)
    .join("、");

  const map: Record<string, string> = {
    "书名": book || "（未命名书籍）",
    "当前章节": chapter || "（未选择章节）",
    "关联章节": chapters || "（未关联章节）",
    "关联大纲": outlines || "（未关联大纲）",
    bookTitle: book || "",
    chapterTitle: chapter || "",
    associatedChapters: chapters,
    associatedOutlines: outlines,
  };

  return content.replace(/\{([^{}]+)\}/g, (match, key: string) => {
    const trimmed = key.trim();
    if (trimmed in map) return map[trimmed];
    return match;
  });
}

/** 检查模版是否用到未填充的已知占位符（用于 UI 提示） */
export function hasUnfilledPlaceholders(
  content: string,
  ctx: PromptTemplateContext,
): string[] {
  if (!content) return [];
  const missing = new Set<string>();
  const rx = /\{([^{}]+)\}/g;
  let m: RegExpExecArray | null;
  while ((m = rx.exec(content))) {
    const key = m[1].trim();
    switch (key) {
      case "书名":
      case "bookTitle":
        if (!(ctx.bookTitle || "").trim()) missing.add("书名");
        break;
      case "当前章节":
      case "chapterTitle":
        if (!(ctx.currentChapterTitle || "").trim()) missing.add("当前章节");
        break;
      case "关联章节":
      case "associatedChapters":
        if (!(ctx.associatedChapterTitles ?? []).filter(Boolean).length)
          missing.add("关联章节");
        break;
      case "关联大纲":
      case "associatedOutlines":
        if (!(ctx.associatedOutlineTitles ?? []).filter(Boolean).length)
          missing.add("关联大纲");
        break;
      default:
        break;
    }
  }
  return Array.from(missing);
}

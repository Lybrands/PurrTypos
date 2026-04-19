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
    token: "{本章大纲}",
    label: "本章大纲",
    description:
      "当前章节绑定的大纲 Markdown 正文（自动从本章对应的大纲读取，无需手动关联）",
  },
  {
    token: "{前文}",
    label: "前文",
    description: "当前章节之前的最近 5 章标题（顿号分隔，自动取，无需手动关联）",
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
  {
    id: -7,
    title: "按本章大纲写正文（去 AI 味）",
    content:
      "请严格按本章大纲生成《{书名}·{当前章节}》的正文。\n\n" +
      "【本章大纲】\n{本章大纲}\n\n" +
      "【前文衔接参考（最近 5 章）】{前文}\n\n" +
      "硬性要求：\n" +
      "1. 按大纲节拍依次落地，不跳节拍、不加大纲外的关键剧情；用大纲的逻辑骨架，但用小说语言写出血肉。\n" +
      "2. 与前后章连贯：人物动机、称谓、时间线、战力/设定、已埋伏笔与前文一致；不重复前章已交代的事实，也不透支后章悬念。\n" +
      "3. 对白像真人讲话，自然生动，绝不能像机器人对话——\n" +
      "   - 按人物身份、教育程度、当前情绪选词；同一个人在熟人面前 vs 陌生人面前的语气要区分；\n" +
      "   - 真人会有半句话、停顿、打断对方、走神、岔题、沉默、重复、口头禅、语气词（'啊''嗯''诶''那个'），适度用，不要密集到滑稽；\n" +
      "   - 答非所问、绕开话题、欲言又止、阴阳怪气都比'问什么答什么'真实；\n" +
      "   - 别让人物每句都说完整长句，别每句都补主语；短句、断句、省略主语都是常态；\n" +
      "   - 避免'温柔地说''轻声道''冷冷地说''意味深长地''若有所思地'这类情绪副词堆砌；动作或环境侧写比副词更可信；\n" +
      "   - 避免'您好''请问''多谢您''承蒙关照'连发的礼貌敬语墙——除非身份明确要求；\n" +
      "   - 避免对白里大段解释设定、复述前情，那是说明文，不是说话；让信息通过冲突、追问、误解自然漏出；\n" +
      "   - 称呼要稳定也要随情境波动（亲密时变称呼、生气时变称呼），不要全章一个口径。\n" +
      "4. 去 AI 味（明确禁忌）：\n" +
      "   - 不要'是…是…也是…''不是…而是…'的排比堆砌；\n" +
      "   - 不要'仿佛 / 宛如 / 犹如 / 像极了'等比喻成串出现；\n" +
      "   - 不要套路化氛围烘托（'空气仿佛凝固''时间似乎静止''心跳漏了一拍''一种说不出的感觉'）；\n" +
      "   - 不要每段以总结句收尾，不要'这一刻他明白了…''他终于懂得…'式煽情；\n" +
      "   - 不要给所有人物都加心理活动旁白，留白比解释更可信。\n" +
      "5. 节奏：长短句交替，能用一句不用三句；细节该铺则铺，转场该跳则跳。\n" +
      "6. 直接产出正文，**不复述大纲、不加章节标题、不写「以下是」「希望对你有帮助」之类话**。",
    builtin: true,
  },
];

/** 构造替换上下文 */
export interface PromptTemplateContext {
  bookTitle?: string | null;
  currentChapterTitle?: string | null;
  /** 当前章节绑定的大纲 markdown 正文；对应 {本章大纲} */
  currentChapterOutline?: string | null;
  /** 当前章节之前的最近 N 章（默认 5）标题列表；对应 {前文} */
  previousChapterTitles?: string[];
  associatedChapterTitles?: string[];
  associatedOutlineTitles?: string[];
  /** Inline 改写场景下传入：当前选区文本，对应模板变量 {选中} / selection */
  selection?: string | null;
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
  const selection = (ctx.selection || "").trim();
  const chapterOutline = (ctx.currentChapterOutline || "").trim();
  const previousTitles = (ctx.previousChapterTitles ?? [])
    .map((s) => s.trim())
    .filter(Boolean)
    .join("、");

  const map: Record<string, string> = {
    "书名": book || "（未命名书籍）",
    "当前章节": chapter || "（未选择章节）",
    "本章大纲": chapterOutline || "（本章暂无大纲，建议先在大纲面板补一份）",
    "前文": previousTitles || "（无前文章节）",
    "关联章节": chapters || "（未关联章节）",
    "关联大纲": outlines || "（未关联大纲）",
    "选中": selection || "（未选中文本）",
    bookTitle: book || "",
    chapterTitle: chapter || "",
    chapterOutline: chapterOutline,
    previousChapters: previousTitles,
    associatedChapters: chapters,
    associatedOutlines: outlines,
    selection: selection,
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
      // 本章大纲 / 前文 是「自动从当前章上下文取」的占位符，模板里已带友好兜底
      // 文案，再弹 warning 是双重提醒，体验差，这里不报缺。
      case "本章大纲":
      case "chapterOutline":
      case "前文":
      case "previousChapters":
        break;
      case "选中":
      case "selection":
        if (!(ctx.selection || "").trim()) missing.add("选中");
        break;
      default:
        break;
    }
  }
  return Array.from(missing);
}

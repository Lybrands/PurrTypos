/**
 * Agent 工具定义（合并策略 A：主对话模型真源）
 *
 * - 下发给主对话模型的 tools（name / description / parameters）**仅来自本文件**，不拼接 SKILL.md 正文。
 * - electron/skills/<name>/SKILL.md 由 toolRouter 用 gray-matter 读取（frontmatter `description`），**仅用于** embed 召回与 Ollama 意图提示（不写进 API tools）。
 * - 实际执行在 toolExecutor.js；新增工具请同步本文件、toolExecutor 与（可选）SKILL.md。
 */

/** @typedef {{ name: string, description: string, parameters: object }} RouterSkillItem */

/** @type {RouterSkillItem[]} */
const ROUTER_SKILL_ITEMS = [
  {
    name: 'getBookContext',
    description:
      '一次性获取当前书籍的完整写作上下文：当前章节正文、写作大纲、小说背景正文、全部人物卡片。适合开场全面了解本书，或用户同时问到「写到哪、大纲、设定、角色」时调用，可减少多次单独查询。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        currentChapterId: {
          type: 'number',
          description: '当前正在写的章节 ID，不传则只取大纲/背景/人物',
        },
        currentChapterTitle: { type: 'string', description: '当前章节标题，用于展示' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'getAllOutlines',
    description:
      '获取本书全部大纲类型（总纲、卷大纲、章节大纲、其他大纲、写作大纲）及每类下的子章节树与层级文本。用于查看全书结构、对比不同大纲类型，或需要完整大纲全景而非仅写作目录时。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'getWritingOutlineWithChapters',
    description:
      '获取本书「写作大纲」及左侧写作目录中的章节列表。返回的 chapters[].id 是读取/编辑章节正文时唯一合法 ID；其他大纲（总纲、章节大纲等）里的章节 id 不能用于 getChapterContent 或 editChapterContent，否则会取不到正文。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'getChapterContent',
    description:
      '只读：获取写作大纲中某一章的纯文本正文。必须先调用 getWritingOutlineWithChapters(bookId) 取得 chapters[].id 再传入 chapterId；可选 maxTextLength。用于引用现状、续写依据、单章摘要等，不修改书稿。',
    parameters: {
      type: 'object',
      properties: {
        chapterId: {
          type: 'number',
          description: '写作大纲中的章节 ID（来自 getWritingOutlineWithChapters 的 chapters[].id）',
        },
        title: { type: 'string', description: '章节标题，用于展示' },
        maxTextLength: { type: 'number', description: '纯文本最大长度，默认 12000' },
      },
      required: ['chapterId'],
    },
  },
  {
    name: 'batchGetChapterContents',
    description:
      '只读：批量获取多章正文（纯文本）。每个 chapterId 均须来自写作大纲 chapters[].id。适合跨章对比、连续多章摘要、精读若干章而无需逐次调用 getChapterContent。',
    parameters: {
      type: 'object',
      properties: {
        chapterIds: {
          type: 'array',
          items: { type: 'number' },
          description: '章节 ID 列表，如 [1,2,3]',
        },
        maxTextLength: { type: 'number', description: '每章纯文本最大长度，默认 12000' },
      },
      required: ['chapterIds'],
    },
  },
  {
    name: 'getBookCharacters',
    description:
      '只读：返回纯文本人物摘要（每人一行），含姓名、性别、年龄、职业、性格、外貌、背景、经历、标签等已有字段，并含「人物ID:」供 addMemory(characterId)；不是只返回 ID。默认全部人物；可选 characterIds 子集；可选 names 按角色名模糊匹配。可与 getStoryBackground 组合。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        characterIds: {
          type: 'array',
          items: { type: 'number' },
          description:
            '可选。只返回这些人物 ID；ID 来自 getBookCharacters 返回中的「人物ID:数字」，或 listBookCharacters 返回的 JSON 每项的 id。与 names 二选一优先用本参数。',
        },
        names: {
          type: 'array',
          items: { type: 'string' },
          description:
            '可选。按角色名模糊匹配（包含即命中），如 ["林月","主角"]。未传 characterIds 时使用；二者都未传则返回全部。',
        },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'listBookCharacters',
    description:
      '只读：返回 JSON 数组字符串，元素形如 {"id":number,"name":string}，按创建顺序列出本书人物，不含性别/背景等详情。用于先拿 id 与姓名，再 getBookCharacters(characterIds) 或 addMemory(characterId)。空列表为 []。完整人设用 getBookCharacters。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'getStoryBackground',
    description:
      '只读：获取本书「小说背景」正文（世界观、时代、基调、总设定等）。用户要看、总结或讨论小说背景、世界观、设定总述时使用；读正文不是 searchMemories 的主要场景（后者是碎片化长期记忆）。常与 getBookCharacters 搭配。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'getAvailableOutlines',
    description:
      '只读：获取本书可关联到对话的大纲列表（总纲、章节大纲、其他大纲，扁平 id 与标题）。用于挑选要带入上下文的大纲，或作为 batchGetOutlineDetails 的 outlineIds 来源。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'batchGetOutlineDetails',
    description:
      '只读：按大纲 ID 列表获取每个大纲的详情（子章节树与层级文本）。须已知 outlineIds（来自 getAvailableOutlines 或 getAllOutlines）。用于精读特定几条大纲的结构与内容，而非只列标题。',
    parameters: {
      type: 'object',
      properties: {
        outlineIds: {
          type: 'array',
          items: { type: 'number' },
          description: '大纲 ID 列表',
        },
        bookId: { type: 'number', description: '当前书籍 ID，用于解析 allOutlines' },
      },
      required: ['outlineIds', 'bookId'],
    },
  },
  {
    name: 'getTextOutline',
    description:
      '只读：获取左侧大纲各条目中「文本大纲」标签页保存的 Markdown 正文（剧情提纲、结构说明等）。与 batchGetOutlineDetails（XMind/子章节树 chaptersText）互补；用户讨论文本层大纲、总纲文档、卷/章文字说明时使用。可先 getAvailableOutlines 拿 id 再传 outlineIds 子集。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        outlineIds: {
          type: 'array',
          items: { type: 'number' },
          description:
            '可选。只拉取这些大纲 ID 的文本；不传则返回本书所有已填写文本大纲（按总纲→卷→章节大纲→其他→写作顺序）。',
        },
        maxTextLength: {
          type: 'number',
          description: '可选。合并后纯文本最大长度，默认 32000，超出截断。',
        },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'editChapterContent',
    description:
      '写入：将新正文（纯文本，段落用换行符 \\n）保存到写作大纲中的指定章。chapterId 必须来自 getWritingOutlineWithChapters(bookId) 的 chapters[].id。用于按用户要求改写、替换整章、润色扩写等会修改书稿的操作。',
    parameters: {
      type: 'object',
      properties: {
        chapterId: {
          type: 'number',
          description: '写作大纲中的章节 ID（来自 getWritingOutlineWithChapters 的 chapters[].id）',
        },
        content: {
          type: 'string',
          description: '章节新正文，纯文本，段落之间用换行符分隔',
        },
      },
      required: ['chapterId', 'content'],
    },
  },
  {
    name: 'addMemory',
    description:
      '写入：向本书长期记忆添加一条要点（设定摘要、人物 note、章节要点等）。layer：0=全局 1=大纲 2=人物 3=章节；章节记忆可传 chapterId，人物记忆可传 characterId。后续可由 searchMemories 检索。用户仅想「查看」已有内容时不要调用——应使用 getStoryBackground、getBookCharacters 或 searchMemories 只读查询。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        layer: { type: 'number', description: '层级：0=全局 1=大纲 2=人物 3=章节' },
        content: { type: 'string', description: '记忆内容，简明扼要' },
        chapterId: {
          type: 'number',
          description: '可选。章节记忆时关联的写作章节 ID（来自 getWritingOutlineWithChapters）',
        },
        characterId: { type: 'number', description: '可选。人物记忆时关联的人物 ID' },
      },
      required: ['bookId', 'layer', 'content'],
    },
  },
  {
    name: 'addForeshadowing',
    description:
      '写入：添加一条伏笔记录（悬念、道具、线索、对话等），并标明埋入章节。chapterId 须为写作大纲章节 id（来自 getWritingOutlineWithChapters）。仅在用户明确要「记录/埋下伏笔」时使用；若用户只要看小说背景或总结人物，应使用 getStoryBackground、getBookCharacters 等只读工具，不要用本工具。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        chapterId: {
          type: 'number',
          description: '埋入伏笔的章节 ID（来自 getWritingOutlineWithChapters 的 chapters[].id）',
        },
        content: { type: 'string', description: '伏笔内容，简明扼要' },
        type: {
          type: 'string',
          description: '可选。类型：悬念 / 道具 / 线索 / 对话，默认 悬念',
        },
      },
      required: ['bookId', 'chapterId', 'content'],
    },
  },
  {
    name: 'searchMemories',
    description:
      '只读：按关键词检索本书长期记忆（含全局/大纲/人物/章节/伏笔等层），用于回忆设定、伏笔回收、前后一致。可限定 layer。注意：「小说背景」成文档的正文优先用 getStoryBackground；本工具偏碎片化已存记忆条目。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        query: {
          type: 'string',
          description: '检索关键词，与当前问题或要回忆的内容相关；不传则返回近期记忆',
        },
        layer: {
          type: 'string',
          description: '可选。限定层级：全局 / 大纲 / 人物 / 章节 / 伏笔；不传则检索所有层',
        },
        chapterId: { type: 'number', description: '可选。当前章节 ID，检索章节记忆时可优先本章' },
        limit: { type: 'number', description: '可选。最多返回条数，默认 15' },
      },
      required: ['bookId'],
    },
  },
]

/**
 * OpenAI 兼容的 tools 数组（完整 function 声明）
 * @param {RouterSkillItem[]} items
 */
function toOpenAiTools(items) {
  return items.map((s) => ({
    type: 'function',
    function: {
      name: s.name,
      description: s.description || '',
      parameters: s.parameters,
    },
  }))
}

/** 全部工具（供导出文档或调试） */
const ALL_OPENAI_TOOLS = toOpenAiTools(ROUTER_SKILL_ITEMS)

module.exports = {
  ROUTER_SKILL_ITEMS,
  ALL_OPENAI_TOOLS,
  toOpenAiTools,
}

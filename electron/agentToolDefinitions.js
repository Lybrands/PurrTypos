/**
 * Agent 工具定义（合并策略 A：主对话模型真源）
 *
 * - 下发给主对话模型的 tools（name / description / parameters）**仅来自本文件**，不拼接 SKILL.md 正文。
 * - electron/skills/<name>/SKILL.md 由 toolRouter 用 gray-matter 读取（frontmatter `description`），**仅用于** embed 召回与 Ollama 意图提示（不写进 API tools）。
 * - 实际执行在 toolExecutor.js；新增工具请同步本文件、toolExecutor 与（可选）SKILL.md。
 */

/**
 * @typedef {{
 *   name: string,
 *   description: string,
 *   parameters: object,
 *   skillSpec?: SkillSpec
 * }} RouterSkillItem
 */
/**
 * @typedef {{
 *   requires?: string[],
 *   provides?: string[],
 *   consumes?: string[],
 *   riskLevel: 'read'|'write',
 *   autoResolveArgs?: string[]
 * }} SkillSpec
 */

/** @type {RouterSkillItem[]} */
const ROUTER_SKILL_ITEMS = [
  {
    name: 'listWritingChapters',
    description:
      '只读：获取本书写作目录的章节列表（区分卷/章节）。返回结构化 JSON，包含每项 id、title、parentId、level、nodeType(volume|chapter)、hasChildren、sort。用于先定位章节 id，再调用 getChapterContent / editChapterContent / batchGetChapterContents。',
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
      '只读：获取写作目录中某一章的纯文本正文。chapterId 须为左侧写作章节目录对应的章节 id（勿用总纲/章节大纲/其他大纲树里的节点 id）。可选 maxTextLength。用于引用现状、续写依据、单章摘要等，不修改书稿。',
    parameters: {
      type: 'object',
      properties: {
        chapterId: {
          type: 'number',
          description: '写作目录章节 ID（与界面左侧写作章节目录一致，非其他大纲子节点 id）',
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
      '只读：批量获取多章正文（纯文本）。每个 chapterId 均须为写作目录章节 id（与左侧写作章节目录一致，勿用其他大纲树节点 id）。适合跨章对比、连续多章摘要、精读若干章而无需逐次调用 getChapterContent。',
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
    name: 'getGlobalOutline',
    description:
      '只读：获取本书总纲（global）文本内容。若总纲不存在会自动创建空总纲后返回，便于后续补写与编辑。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        maxTextLength: {
          type: 'number',
          description: '可选。总纲 Markdown 最大长度，默认 32000。',
        },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'editGlobalOutline',
    description:
      '写入：编辑本书总纲（global）Markdown 内容，整体覆盖保存。若总纲不存在会自动创建后写入。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID（用于归属校验）' },
        markdownContent: { type: 'string', description: '总纲 Markdown 全文（覆盖写入）' },
      },
      required: ['bookId', 'markdownContent'],
    },
  },
  {
    name: 'queryOutline',
    description:
      '只读：查询本书大纲详情。适用场景：用户要看/查/读/拉取「大纲、提纲、剧情结构、XMind 目录树、章节树、子节点列表」；要看「文本大纲、Markdown 提纲、剧情梗概」；要对比或浏览「总纲、章节大纲、其他大纲、卷大纲」的正文与结构。返回每条大纲的 id/title/type、可选 chaptersText（思维导图式层级文本）、可选 markdown（文本大纲页内容）。可先 listOutlines 拿 id 再传 outlineIds 精确查；不传 outlineIds 则返回本书可关联大纲全集。注意：与「写作目录章节正文」无关，正文请用 getChapterContent。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        outlineIds: {
          type: 'array',
          items: { type: 'number' },
          description: '可选。只查询这些大纲 ID；不传则返回可用大纲列表并附带结构/文本信息。',
        },
        includeChapters: {
          type: 'boolean',
          description: '可选。是否返回章节树文本（chaptersText），默认 true。',
        },
        includeText: {
          type: 'boolean',
          description: '可选。是否返回文本大纲 Markdown，默认 true。',
        },
        maxTextLength: {
          type: 'number',
          description: '可选。文本大纲最大长度，默认 32000。',
        },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'listOutlines',
    description:
      '只读：获取本书大纲列表（id、title、type），包含总纲。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
      },
      required: ['bookId'],
    },
  },
  {
    name: 'updateOutline',
    description:
      '写入：修改/保存本书某条大纲元数据或内容。适用场景：用户要「改大纲标题、重写文本大纲、更新 Markdown 提纲、替换 XMind 数据、改关联文件路径」；要「保存/写入/覆盖」某条大纲（非总纲专用时可走本工具；仅总纲正文也可考虑 editGlobalOutline）。参数：outlineId 必填，且须先用 listOutlines 确认 id；可选 title、markdown_content（文本大纲全文覆盖）、xmind_data+file_path（导图 JSON 与源文件路径）。不要用于修改章节正文（用 editChapterContent）。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID（用于归属校验）' },
        outlineId: { type: 'number', description: '目标大纲 ID（可来自 queryOutline 返回）' },
        title: { type: 'string', description: '可选。更新大纲标题' },
        xmind_data: { type: 'string', description: '可选。更新 XMind JSON 文本' },
        file_path: { type: 'string', description: '可选。更新源文件路径' },
        markdown_content: { type: 'string', description: '可选。更新文本大纲 Markdown' },
      },
      required: ['bookId', 'outlineId'],
    },
  },
  {
    name: 'editChapterContent',
    description:
      '写入：将新正文（纯文本，段落用换行符 \\n）保存到写作目录中的指定章。chapterId 须为左侧写作章节目录对应的章节 id（勿用其他大纲树节点 id）。用于按用户要求改写、替换整章、润色扩写等会修改书稿的操作。',
    parameters: {
      type: 'object',
      properties: {
        chapterId: {
          type: 'number',
          description: '写作目录章节 ID（与界面左侧写作章节目录一致）',
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
          description: '可选。章节记忆时关联的写作目录章节 ID（左侧写作章节目录）',
        },
        characterId: { type: 'number', description: '可选。人物记忆时关联的人物 ID' },
      },
      required: ['bookId', 'layer', 'content'],
    },
  },
  {
    name: 'addForeshadowing',
    description:
      '写入：添加一条伏笔记录（悬念、道具、线索、对话等），并标明埋入章节。chapterId 须为写作目录章节 id（左侧写作章节目录）。仅在用户明确要「记录/埋下伏笔」时使用；若用户只要看小说背景或总结人物，应使用 getStoryBackground、getBookCharacters 等只读工具，不要用本工具。',
    parameters: {
      type: 'object',
      properties: {
        bookId: { type: 'number', description: '当前书籍 ID' },
        chapterId: {
          type: 'number',
          description: '埋入伏笔的写作目录章节 ID（左侧写作章节目录）',
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

/**
 * 技能结构化元信息（DAG 规划/执行器使用）。
 * - 未显式配置的技能：默认 read + 无依赖。
 * - 先覆盖大纲链路高频路径，其余工具使用默认值。
 * @type {Record<string, SkillSpec>}
 */
const SKILL_SPECS = {
  getGlobalOutline: {
    riskLevel: 'read',
    provides: ['globalOutlineMarkdown'],
    consumes: ['bookId'],
  },
  editGlobalOutline: {
    riskLevel: 'write',
    requires: ['getGlobalOutline'],
    consumes: ['bookId', 'markdownContent'],
    autoResolveArgs: ['bookId'],
  },
  listOutlines: {
    riskLevel: 'read',
    provides: ['outlinesIndex'],
    consumes: ['bookId'],
  },
  queryOutline: {
    riskLevel: 'read',
    requires: ['listOutlines'],
    provides: ['outlineDetails'],
    consumes: ['bookId'],
    autoResolveArgs: ['bookId', 'outlineIds'],
  },
  updateOutline: {
    riskLevel: 'write',
    requires: ['listOutlines', 'queryOutline'],
    consumes: ['bookId', 'outlineId'],
    autoResolveArgs: ['bookId', 'outlineId'],
  },
  editChapterContent: {
    riskLevel: 'write',
    consumes: ['chapterId', 'content'],
    autoResolveArgs: ['chapterId'],
  },
  listWritingChapters: {
    riskLevel: 'read',
    provides: ['writingChaptersIndex'],
    consumes: ['bookId'],
  },
}

/**
 * 输出完整技能规格：若未配置 spec，则提供安全默认值。
 * @returns {Record<string, SkillSpec>}
 */
function getSkillSpecs() {
  const out = {}
  for (const item of ROUTER_SKILL_ITEMS) {
    const raw = SKILL_SPECS[item.name] || {}
    out[item.name] = {
      requires: Array.isArray(raw.requires) ? raw.requires : [],
      provides: Array.isArray(raw.provides) ? raw.provides : [],
      consumes: Array.isArray(raw.consumes) ? raw.consumes : [],
      riskLevel: raw.riskLevel === 'write' ? 'write' : 'read',
      autoResolveArgs: Array.isArray(raw.autoResolveArgs) ? raw.autoResolveArgs : [],
    }
  }
  return out
}

/** 全部工具（供导出文档或调试） */
const ALL_OPENAI_TOOLS = toOpenAiTools(ROUTER_SKILL_ITEMS)

module.exports = {
  ROUTER_SKILL_ITEMS,
  SKILL_SPECS,
  getSkillSpecs,
  ALL_OPENAI_TOOLS,
  toOpenAiTools,
}

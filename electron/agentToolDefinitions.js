/**
 * Agent 工具定义（编排元数据与格式转换）
 *
 * - 发给大模型的 `description` / `parameters`：**仅**来自 `electron/skills/<name>/SKILL.md`（由 `toolRouter` 扫描目录加载），**不再**使用本文件的重复清单。
 * - 本文件保留 **`SKILL_SPECS`**（DAG / 编排）与 **`toOpenAiTools`**；新增工具：在 `toolExecutor` 实现 + 增加 `SKILL.md` + 按需补 `SKILL_SPECS`。
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
  listWritingChapters: {
    riskLevel: 'read',
    provides: ['writingChaptersIndex'],
    consumes: ['bookId'],
  },
  getChapterContent: {
    riskLevel: 'read',
    requires: ['listWritingChapters'],
    consumes: ['bookId', 'chapterId'],
  },
  batchGetChapterContents: {
    riskLevel: 'read',
    requires: ['listWritingChapters'],
    consumes: ['chapterIds'],
  },
  editChapterContent: {
    riskLevel: 'write',
    requires: ['listWritingChapters'],
    consumes: ['chapterId', 'content'],
    autoResolveArgs: ['chapterId'],
  },
  addForeshadowing: {
    riskLevel: 'write',
    requires: ['listWritingChapters'],
    consumes: ['bookId', 'chapterId', 'content'],
  },
}

/**
 * 与 `toolRouter.getApiSkillItems()` 中的工具名对齐；须在 main 已 `setSkillsPath` 之后调用。
 * @returns {Record<string, SkillSpec>}
 */
function getSkillSpecs() {
  const toolRouter = require('./toolRouter')
  const items = toolRouter.getApiSkillItems()
  const out = {}
  for (const item of items) {
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

module.exports = {
  SKILL_SPECS,
  getSkillSpecs,
  toOpenAiTools,
}

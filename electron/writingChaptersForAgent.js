/**
 * 从写作目录快照中筛出「可读写正文」的章节行。
 * 分卷模式下「卷」在左侧为父节点，子行的 parent_id 指向卷 id；若把卷与章混在同一序号表里，
 * chapterIndex 会误指向卷，getArticle 行为错误。
 * 规则：任意行若被其它行的 parent_id 引用，则视为容器（卷/分组），不参与附录序号与 chapterTitle 解析。
 * 非分卷（全部为平铺、无父子）时，没有任何 id 出现在 parent_id 中，结果与原始列表一致。
 *
 * @param {Array<{ id: unknown, parent_id?: unknown }>} writingChapters
 * @returns {typeof writingChapters}
 */
function getWritableChaptersForAgent(writingChapters) {
  const wc = Array.isArray(writingChapters) ? writingChapters : []
  if (wc.length === 0) return []
  const idsThatAreParents = new Set()
  for (const c of wc) {
    const pid = c.parent_id
    if (pid != null && String(pid).trim() !== '') {
      idsThatAreParents.add(String(pid))
    }
  }
  return wc.filter((c) => !idsThatAreParents.has(String(c.id)))
}

module.exports = { getWritableChaptersForAgent }

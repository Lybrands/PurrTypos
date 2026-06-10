"""
Pure helpers for writing / subagent flows (prior-chapter hints).
"""

from __future__ import annotations

import re

from utils.writing_chapters import get_writable_chapters_for_agent


def build_prior_chapter_hint(
    tool_ctx: dict,
    *,
    max_chapters: int = 5,
    purpose_line: str = "请参考以下前文章节以辅助判断",
    instruction_suffix: str = "",
) -> str:
    """Generate a hint listing prior chapter IDs for any stage that needs context."""
    if not tool_ctx or tool_ctx.get("bookId") is None:
        return ""
    wc_raw = tool_ctx.get("writingChapters") or []
    wc = get_writable_chapters_for_agent(wc_raw)
    cur_id = tool_ctx.get("chapterId")
    if not cur_id or not wc:
        return ""
    idx = next((i for i, c in enumerate(wc) if str(c.get("id")) == str(cur_id)), -1)
    if idx <= 0:
        return "【宿主提示】当前章为第 1 章或未在目录中，无前文可供参照。"
    n_read = min(max_chapters, idx)
    start = idx - n_read
    lines = [f"【宿主推算】当前章在写作目录中为第 {idx + 1} 章。{purpose_line}（紧邻当前章向前的连续 {n_read} 章）："]
    for j in range(start, idx):
        t = re.sub(r"\r?\n", " ", str(wc[j].get("title") or ""))[:120]
        lines.append(f"- chapterId={str(wc[j].get('id', '')).strip()} 《{t}》")
    lines.append("请优先使用 batchGetChapterContents 一次传入多个 chapterId；或多次 getChapterContent。")
    lines.append("batchGetChapterContents.chapterIds 参数类型必须是 string[]，且每项都来自上方 chapterId。")
    if instruction_suffix:
        lines.append(instruction_suffix)
    return "\n".join(lines)


def build_style_unify_prior_chapter_hint(tool_ctx: dict) -> str:
    if not tool_ctx or tool_ctx.get("bookId") is None:
        return "【宿主提示】未绑定书籍时仍应用工具列出章节并自行选取前文。"
    wc_raw = tool_ctx.get("writingChapters") or []
    wc = get_writable_chapters_for_agent(wc_raw)
    cur_id = tool_ctx.get("chapterId")
    if not cur_id or not wc:
        return "【宿主提示】未选可写章节或目录为空：请先 listWritingChapters；若无前文则在 styleAnchors 中说明。"
    idx = next((i for i, c in enumerate(wc) if str(c.get("id")) == str(cur_id)), -1)
    if idx < 0:
        return "【宿主提示】当前章节不在可写目录中：请核对 chapterId。"
    if idx == 0:
        return "【宿主提示】当前章为目录中第 1 章，无前文：styleAnchors 须注明「无前文可参照」，仅做语气与蓝图内约束下的统一。"
    n_prior = idx
    n_read = min(5, n_prior) if n_prior >= 3 else n_prior
    start = idx - n_read
    lines = [f"【宿主推算】当前章在写作目录中为第 {idx + 1} 章。请必读以下 {n_read} 章正文以萃取文风（紧邻当前章向前的连续章）："]
    for j in range(start, idx):
        t = re.sub(r"\r?\n", " ", str(wc[j].get("title") or ""))[:120]
        lines.append(f"- chapterId={str(wc[j].get('id', '')).strip()} 《{t}》")
    lines.append("请优先使用 batchGetChapterContents 一次传入多个 chapterId；或多次 getChapterContent。归纳 styleAnchors 后再改写下方「待统一初稿」。")
    lines.append("batchGetChapterContents.chapterIds 参数类型必须是 string[]，且每项都来自上方 chapterId。")
    return "\n".join(lines)

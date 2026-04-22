"""
Session-bound tooling context appendix for writing prompts.

Extracted from the former subagent pipeline so all writing flows can share
one implementation.
"""

from __future__ import annotations

import re

from utils.writing_chapters import get_writable_chapters_for_agent


def build_tooling_context_appendix(tool_ctx: dict) -> str:
    """Append chapter/outline catalog and binding rules for tool-using agents."""
    if not tool_ctx or tool_ctx.get("bookId") is None:
        return ""
    wc_raw = tool_ctx.get("writingChapters") or []
    wc = get_writable_chapters_for_agent(wc_raw)
    ao = tool_ctx.get("availableOutlines") or []
    acc_ch = tool_ctx.get("associatedChapterIds") or []
    acc_ol = tool_ctx.get("associatedOutlineIds") or []

    lines: list[str] = []
    lines.append("【会话同步 — 工具与界面上下文】")
    lines.append(
        "宿主已为当前会话绑定书籍与章节；**请勿在工具参数中手写 bookId**（一律由工具层使用当前会话书籍）。"
        "章节工具仅允许 chapterId；大纲查询仅允许 outlineId/outlineIds；禁止使用序号或标题作为定位参数。"
    )
    lines.append("若需操作非当前章节，必须先 listWritingChapters 读取真实 chapterId，再传 chapterId。")

    ch_desc = (
        f"《{re.sub(chr(10), ' ', str(tool_ctx.get('currentChapterTitle') or ''))}》"
        if tool_ctx.get("chapterId") else "（未选章节）"
    )
    lines.append(f"当前写作章节：{ch_desc}")

    # ── 关联章节：列出 chapterId 并强约束「回复前必须读取」──────────
    # 旧版只给标题，LLM 经常无视；这里直接给 id，并明确要求一次性 batchGetChapterContents
    if acc_ch:
        valid_pairs: list[tuple[str, str]] = []
        for cid in acc_ch:
            sid = str(cid).strip()
            if not sid:
                continue
            c = next((x for x in wc if str(x.get("id")) == sid), None) if wc else None
            title = re.sub(r"\r?\n", " ", str(c.get("title") or "")).strip() if c else ""
            valid_pairs.append((sid, title or "（未匹配章节标题）"))
        if valid_pairs:
            lines.append(
                "【用户在本轮已关联以下写作章节 — 回复前必须读取其正文，不读视为忽略用户上下文，回答无效】"
            )
            for sid, title in valid_pairs:
                lines.append(f"- chapterId={sid} 《{title}》")
            id_list_literal = "[" + ",".join(f'"{sid}"' for sid, _ in valid_pairs) + "]"
            lines.append(
                f"→ 立即调用 batchGetChapterContents，参数 chapterIds={id_list_literal}，"
                "一次性把上述全部章节正文读入；若已在前序工具结果中读过可复用，否则不得跳过。"
            )

    # ── 关联大纲：同样的硬约束 ─────────────────────────────────────
    if acc_ol:
        valid_pairs_ol: list[tuple[str, str]] = []
        for oid in acc_ol:
            sid = str(oid).strip()
            if not sid:
                continue
            o = next((x for x in ao if str(x.get("id")) == sid), None) if ao else None
            title = re.sub(r"\r?\n", " ", str(o.get("title") or "")).strip() if o else ""
            valid_pairs_ol.append((sid, title or "（未匹配大纲标题）"))
        if valid_pairs_ol:
            lines.append(
                "【用户在本轮已关联以下大纲 — 回复前必须读取其完整内容，不读视为忽略用户上下文，回答无效】"
            )
            for sid, title in valid_pairs_ol:
                lines.append(f"- outlineId={sid} 《{title}》")
            id_list_literal = "[" + ",".join(f'"{sid}"' for sid, _ in valid_pairs_ol) + "]"
            lines.append(
                f"→ 立即调用 queryOutline，参数 outlineIds={id_list_literal}、includeText=true、"
                "includeChapters=false，一次性把上述全部大纲读入；若已在前序工具结果中读过可复用，否则不得跳过。"
            )

    WC_CAP = 100
    if wc:
        lines.append(f"写作章节目录（仅含可写正文的章节，每条给出 chapterId）。共 {len(wc)} 条：")
        for idx, c in enumerate(wc[:WC_CAP]):
            lines.append(f"- chapterId={str(c.get('id', '')).strip()} {str(c.get('title', '')).replace(chr(10), ' ')[:120]}")
        if len(wc) > WC_CAP:
            lines.append(f"… 余 {len(wc) - WC_CAP} 条略")

    AO_CAP = 60
    if ao:
        lines.append(f"大纲列表（每项含 outlineId；queryOutline 仅传 outlineId/outlineIds，勿传纯数字序号）。共 {len(ao)} 条：")
        for idx, o in enumerate(ao[:AO_CAP]):
            typ = str(o.get("type", "")) if o.get("type") is not None else ""
            oid = str(o.get("id", "")).strip()
            t = re.sub(r"\r?\n", " ", str(o.get("title") or ""))[:80]
            lines.append(f"- [{idx + 1}] outlineId={oid} {t}" + (f"\t{typ}" if typ else ""))
        if len(ao) > AO_CAP:
            lines.append(f"… 余 {len(ao) - AO_CAP} 条略")

    return "\n".join(lines)

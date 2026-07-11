"""
对话前置上下文：宿主在发给模型前直接预取并注入的内容块。

取代两个旧模式：
1. 「命令模型自己去读」—— 旧版在 system 里写"必须立即调用 batchGetChapterContents/
   queryOutline，不读视为无效"，多烧一轮工具往返且依赖模型听话。现在宿主明知用户
   勾选了什么，就直接把内容读出来注入；超长部分截断并提示可用读取工具拿全文。
2. 前端拼 prompt —— 勾选的设定/伏笔原先由前端 fetch 后拼文案（memoryContext.ts），
   现在前端只传 id，文案统一由本模块生成。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from database.crud.articles import get_article
from dependencies import get_db
from utils.outline_text import collect_text_outline_entries
from utils.text import extract_text_from_lexical

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class AssociatedContextBudget:
    total: int
    per_chapter: int
    per_outline: int


CONTEXT_WINDOW_CHARS: dict[str, int] = {
    "200k": 200_000,
    "300k": 300_000,
    "1m": 1_000_000,
}


def _clean_title(s: Any) -> str:
    return re.sub(r"\r?\n", " ", str(s or "")).strip()


def _title_of(items: list[dict] | None, target_id: str) -> str:
    for it in items or []:
        if str(it.get("id")) == target_id:
            return _clean_title(it.get("title")) or "（无标题）"
    return "（未匹配标题）"


async def build_associated_context_block(tool_ctx: dict) -> str:
    """预取用户勾选的关联章节正文 + 关联大纲 markdown，渲染为注入块。

    无关联时返回空串。单条失败跳过（拉不到的条目降级为提示用工具读取），
    不让预取失败阻断对话。
    """
    ctx = tool_ctx or {}
    book_id = ctx.get("bookId")
    acc_ch = [str(x).strip() for x in (ctx.get("associatedChapterIds") or []) if str(x).strip()]
    acc_ol = [str(x).strip() for x in (ctx.get("associatedOutlineIds") or []) if str(x).strip()]
    if book_id is None or (not acc_ch and not acc_ol):
        return ""

    budget_cfg = _associated_context_budget(ctx.get("contextWindow"))
    lines: list[str] = [
        "【关联上下文 — 用户在本轮勾选的章节/大纲，内容已由宿主注入】",
        "以下内容是用户明确要求你参考的素材，直接依据它们回答；"
        "除标注「已截断」或「未注入」的条目外，无需再调工具重复读取。",
    ]
    budget = budget_cfg.total
    deferred: list[str] = []

    for cid in acc_ch:
        title = _title_of(ctx.get("writingChapters"), cid)
        if budget <= 0:
            deferred.append(f"- 章节 chapterId={cid} 《{title}》")
            continue
        text = ""
        try:
            row = await get_article(get_db(), cid)
            raw = (row or {}).get("content") or ""
            text = extract_text_from_lexical(raw) if raw else ""
        except Exception:
            logger.warning("[chat-preflight] 读取关联章节失败 chapterId=%s", cid, exc_info=True)
            deferred.append(f"- 章节 chapterId={cid} 《{title}》")
            continue

        cap = min(budget_cfg.per_chapter, budget)
        truncated = len(text) > cap
        shown = text[:cap]
        budget -= len(shown)
        lines.append(f"\n## 关联章节《{title}》(chapterId={cid})")
        lines.append(shown if shown.strip() else "（本章暂无正文）")
        if truncated:
            lines.append(
                f"…（已截断，全文共 {len(text)} 字，"
                f"可用 getChapterContent 读取，参数 chapterId=\"{cid}\"）"
            )

    if acc_ol:
        entries: list[dict] = []
        try:
            entries = await collect_text_outline_entries(get_db(), str(book_id), acc_ol)
        except Exception:
            logger.warning("[chat-preflight] 读取关联大纲失败", exc_info=True)
        by_id = {str(e.get("id")): e for e in entries}
        for oid in acc_ol:
            entry = by_id.get(oid)
            title = (
                _clean_title(entry.get("title")) if entry
                else _title_of(ctx.get("availableOutlines"), oid)
            )
            md = str(entry.get("markdown") or "") if entry else ""
            if not md.strip():
                lines.append(f"\n## 关联大纲《{title}》(outlineId={oid})")
                lines.append("（该大纲暂无文本内容）")
                continue
            if budget <= 0:
                deferred.append(f"- 大纲 outlineId={oid} 《{title}》")
                continue
            cap = min(budget_cfg.per_outline, budget)
            truncated = len(md) > cap
            shown = md[:cap]
            budget -= len(shown)
            lines.append(f"\n## 关联大纲《{title}》(outlineId={oid})")
            lines.append(shown)
            if truncated:
                lines.append(
                    f"…（已截断，可用 queryOutline 读取完整内容，"
                    f"参数 outlineIds=[\"{oid}\"]、includeText=true）"
                )

    if deferred:
        lines.append("\n以下条目本轮未注入内容（超出注入预算或读取失败），回复前请用工具读取：")
        lines.extend(deferred)

    return "\n".join(lines)


async def build_selected_memory_block(
    memory_ids: list[Any] | None,
    foreshadowing_ids: list[Any] | None,
    book_id: Any | None = None,
    user_prompt: str = "",
    mode: str = "",
    context_window: str | None = None,
) -> str:
    """用户在 AiContextBar 勾选的设定/伏笔条目，前置 fetch 后注入。

    失败时返回空串 —— 拉记忆失败不应阻断对话发送（与旧前端实现语义一致）。
    """
    try:
        from services import memory_orchestrator

        mids = [str(x) for x in (memory_ids or []) if str(x).strip()]
        fids = [str(x) for x in (foreshadowing_ids or []) if str(x).strip()]
        if not book_id:
            return ""
        memory_budget, memory_recall_limit = _memory_budget(context_window)
        block = await memory_orchestrator.build_memory_context(
            {
                "bookId": book_id,
                "selectedMemoryIds": mids,
                "selectedForeshadowingIds": fids,
                "memoryBudget": memory_budget,
                "memoryRecallLimit": memory_recall_limit,
                "contextWindow": context_window,
            },
            user_prompt,
            mode,
        )
        return block.text
    except Exception:
        logger.warning("[chat-preflight] 读取勾选记忆失败", exc_info=True)
        return ""


def _context_window_chars(value: Any) -> int:
    key = str(value or "").strip().lower()
    return CONTEXT_WINDOW_CHARS.get(key, CONTEXT_WINDOW_CHARS["200k"])


def _associated_context_budget(value: Any) -> AssociatedContextBudget:
    total_window = _context_window_chars(value)
    return AssociatedContextBudget(
        total=min(120_000, max(24_000, total_window // 5)),
        per_chapter=min(30_000, max(6_000, total_window // 25)),
        per_outline=min(20_000, max(4_000, total_window // 35)),
    )


def _memory_budget(value: Any) -> tuple[int, int]:
    total_window = _context_window_chars(value)
    budget = min(40_000, max(6_000, total_window // 25))
    if total_window >= 1_000_000:
        recall_limit = 64
    elif total_window >= 300_000:
        recall_limit = 32
    else:
        recall_limit = 16
    return budget, recall_limit


def build_session_binding_prompt(tool_ctx: dict, tools_enabled: bool) -> str:
    """会话绑定说明（原前端 useChatSubmit 里的 systemSuffix，文案权收归后端）。

    写作专家模式不要调这个 —— 其 system prompt 里的 tooling appendix 已包含
    同等绑定规则，重复注入浪费 token。
    """
    ctx = tool_ctx or {}
    if ctx.get("bookId") is None:
        return ""
    chapter_name = _clean_title(ctx.get("currentChapterTitle")) or "（未选章节）"
    if tools_enabled:
        return (
            f"当前写作章节：《{chapter_name}》。"
            "宿主已为当前会话绑定作品上下文；需要 bookId/chapterId/outlineId 的工具参数"
            "由宿主按当前界面自动注入；若需操作**非当前**章节或大纲，只能先读取列表中的"
            "真实 id，再传 **chapterId** / **outlineId(outlineIds)**。"
            "不支持 chapterTitle/chapterIndex/outlineTitle/outlineIndex。勿猜测数据库 id。"
            "向用户回复时使用章节名等界面可见名称，不要暴露 id。"
        )
    return (
        f"当前写作章节：《{chapter_name}》。"
        "你无法调用工具访问书籍内容，仅能基于用户描述、用户主动提供的信息"
        "以及宿主已注入的上下文作答。回复时使用章节名等界面可见名称，不暴露 id。"
    )

"""Build budgeted long-term memory context for AI requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services import long_term_memory_service

DEFAULT_MEMORY_BUDGET = 6000
CONTEXT_WINDOW_CHARS = {
    "200k": 200_000,
    "300k": 300_000,
    "1m": 1_000_000,
}

GROUP_LABELS = {
    "canon": "必须遵循的设定",
    "plot": "已发生的剧情事实",
    "character": "人物当前状态",
    "foreshadowing": "待铺垫/待回收伏笔",
    "style": "风格约束",
    "world": "必须遵循的设定",
    "summary": "已发生的剧情事实",
}


@dataclass
class MemoryContextBlock:
    text: str
    included_ids: list[int] = field(default_factory=list)
    deferred_ids: list[int] = field(default_factory=list)
    token_estimate: int = 0
    diagnostics: dict[str, int] = field(default_factory=dict)


async def build_memory_context(
    tool_ctx: dict,
    user_prompt: str,
    mode: str,
) -> MemoryContextBlock:
    ctx = tool_ctx or {}
    book_id = ctx.get("bookId") or ctx.get("book_id")
    if book_id is None:
        return MemoryContextBlock("", diagnostics=_diagnostics())

    budget = _memory_budget(ctx)
    forced = await _resolve_forced_items(ctx, str(book_id))
    recalled = await long_term_memory_service.search_memory_items(
        str(book_id),
        _build_query(ctx, user_prompt),
        options={"limit": _memory_recall_limit(ctx)},
    )

    by_id: dict[int, dict] = {}
    forced_ids = {int(item["id"]) for item in forced if item.get("id") is not None}
    for item in forced:
        by_id[int(item["id"])] = item
    for item in recalled:
        iid = int(item["id"])
        if iid not in by_id:
            by_id[iid] = item

    ordered = sorted(
        by_id.values(),
        key=lambda item: (
            0 if int(item.get("id")) in forced_ids else 1,
            -int(item.get("pinned") or 0),
            -int(item.get("importance") or 0),
            int(item.get("id") or 0),
        ),
    )

    text, included_ids, deferred_ids = _format_budgeted(ordered, forced_ids, budget)
    if included_ids:
        await long_term_memory_service.mark_memory_items_used(included_ids)

    diagnostics = _diagnostics(
        forced=len(forced_ids),
        recalled=max(0, len([i for i in recalled if int(i.get("id")) not in forced_ids])),
        included=len(included_ids),
        deferred=len(deferred_ids),
    )
    return MemoryContextBlock(
        text=text,
        included_ids=included_ids,
        deferred_ids=deferred_ids,
        token_estimate=len(text),
        diagnostics=diagnostics,
    )


async def _resolve_forced_items(ctx: dict, book_id: str) -> list[dict]:
    memory_item_ids: list[Any] = []
    memory_item_ids.extend(ctx.get("selectedLongTermMemoryIds") or [])
    memory_item_ids.extend(ctx.get("selectedForeshadowingMemoryIds") or [])

    rows = await long_term_memory_service.get_memory_items_by_ids(memory_item_ids)
    rows.extend(await long_term_memory_service.get_memory_items_by_source_ids(
        "spark_idea",
        ctx.get("selectedMemoryIds") or [],
    ))
    rows.extend(await long_term_memory_service.get_memory_items_by_source_ids(
        "foreshadowing",
        ctx.get("selectedForeshadowingIds") or [],
    ))

    seen: set[int] = set()
    result: list[dict] = []
    for row in rows:
        iid = int(row["id"])
        if iid in seen:
            continue
        seen.add(iid)
        if str(row.get("book_id") or "") != str(book_id):
            continue
        if row.get("status") in ("active", "pending"):
            result.append(row)
    return result


def _build_query(ctx: dict, user_prompt: str) -> str:
    parts = [
        user_prompt,
        ctx.get("currentChapterTitle"),
        ctx.get("currentOutlineTitle"),
        ctx.get("currentCharacterName"),
        ctx.get("recentConversationSummary"),
    ]
    return " ".join(str(p).strip() for p in parts if str(p or "").strip())


def _format_budgeted(
    items: list[dict],
    forced_ids: set[int],
    budget: int,
) -> tuple[str, list[int], list[int]]:
    if not items:
        return "", [], []

    groups: dict[str, list[str]] = {}
    included_ids: list[int] = []
    deferred_ids: list[int] = []
    used = 0

    for item in items:
        rendered = _render_item(item)
        item_len = len(rendered) + 16
        iid = int(item["id"])
        if included_ids and used + item_len > budget:
            deferred_ids.append(iid)
            continue
        if not included_ids and item_len > budget:
            rendered = rendered[: max(80, budget - 120)] + "…"
            item_len = len(rendered) + 16
        used += item_len
        label = _label_for_item(item)
        if iid in forced_ids and item.get("kind") not in ("foreshadowing",):
            label = "必须遵循的设定"
        groups.setdefault(label, []).append(rendered)
        included_ids.append(iid)

    lines = ["【长期记忆 — 已由宿主按当前写作意图召回】"]
    label_order = [
        "必须遵循的设定",
        "已发生的剧情事实",
        "人物当前状态",
        "待铺垫/待回收伏笔",
        "大纲计划，非既成事实",
        "风格约束",
    ]
    for label in label_order:
        rows = groups.get(label)
        if rows:
            lines.append(f"\n## {label}")
            lines.extend(rows)

    if deferred_ids:
        deferred_line = f"\n另有 {len(deferred_ids)} 条相关长期记忆因预算限制未注入，可用 searchMemories 继续查询。"
        lines.append(deferred_line)

    text = "\n".join(lines)
    if len(text) > budget:
        if deferred_ids:
            footer = f"\n另有 {len(deferred_ids)} 条相关长期记忆未注入，可用 searchMemories 查询。"
            head_budget = max(0, budget - len(footer) - 1)
            text = text[:head_budget] + "…" + footer
        else:
            text = text[: max(0, budget - 1)] + "…"
    return text, included_ids, deferred_ids


def _render_item(item: dict) -> str:
    prefix = f"- [id:{item.get('id')}|{item.get('kind')}]"
    content = str(item.get("content") or "").strip()
    summary = str(item.get("summary") or "").strip()
    body = content or summary
    return f"{prefix} {body}"


def _label_for_item(item: dict) -> str:
    if item.get("scope_type") == "outline":
        return "大纲计划，非既成事实"
    return GROUP_LABELS.get(str(item.get("kind") or ""), "已发生的剧情事实")


def _diagnostics(
    *,
    forced: int = 0,
    recalled: int = 0,
    included: int = 0,
    deferred: int = 0,
) -> dict[str, int]:
    return {
        "forced": forced,
        "recalled": recalled,
        "included": included,
        "deferred": deferred,
    }


def _context_window_chars(value: Any) -> int:
    key = str(value or "").strip().lower()
    return CONTEXT_WINDOW_CHARS.get(key, CONTEXT_WINDOW_CHARS["200k"])


def _memory_budget(ctx: dict) -> int:
    explicit = ctx.get("memoryBudget")
    if explicit:
        return int(explicit)
    total_window = _context_window_chars(ctx.get("contextWindow"))
    return min(40_000, max(DEFAULT_MEMORY_BUDGET, total_window // 25))


def _memory_recall_limit(ctx: dict) -> int:
    explicit = ctx.get("memoryRecallLimit")
    if explicit:
        return int(explicit)
    total_window = _context_window_chars(ctx.get("contextWindow"))
    if total_window >= 1_000_000:
        return 64
    if total_window >= 300_000:
        return 32
    return 16

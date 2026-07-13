"""Build budgeted long-term memory context for AI requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services import long_term_memory_service
from utils.context_budget import estimate_text_tokens

DEFAULT_MEMORY_BUDGET = 6000
CONTEXT_WINDOW_CHARS = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
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
    suppressed_ids: list[int] = field(default_factory=list)
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

    # Forced items are explicit user intent and always lead.  Automatic recall
    # preserves the service's FTS/BM25 order; sorting again by id would destroy
    # the relevance signal that retrieval already computed.
    forced_ordered = sorted(
        (item for item in forced if int(item.get("id")) in by_id),
        key=lambda item: (
            -int(item.get("pinned") or 0),
            -int(item.get("importance") or 0),
            int(item.get("id") or 0),
        ),
    )
    recalled_ordered = [
        item for item in recalled
        if int(item.get("id")) in by_id and int(item.get("id")) not in forced_ids
    ]
    ordered = [*forced_ordered, *recalled_ordered]

    links = await long_term_memory_service.get_memory_links_for_items(
        str(book_id), list(by_id),
    )
    related_ids = {
        int(endpoint)
        for link in links
        for endpoint in (link.get("from_memory_id"), link.get("to_memory_id"))
        if endpoint is not None and int(endpoint) not in by_id
    }
    relation_expanded: list[dict] = []
    if related_ids:
        related_rows = await long_term_memory_service.get_memory_items_by_ids(
            sorted(related_ids),
        )
        related_by_id = {
            int(item["id"]): item
            for item in related_rows
            if str(item.get("book_id") or "") == str(book_id)
            and item.get("status") == "active"
        }
        for link in links:
            for endpoint in (link.get("from_memory_id"), link.get("to_memory_id")):
                if endpoint is None:
                    continue
                item = related_by_id.get(int(endpoint))
                if item and int(item["id"]) not in by_id:
                    by_id[int(item["id"])] = item
                    relation_expanded.append(item)
    ordered.extend(relation_expanded)
    ordered, suppressed_ids, relation_warnings, conflict_count = _apply_relation_policy(
        ordered,
        links,
        forced_ids,
    )

    text, included_ids, deferred_ids = _format_budgeted(
        ordered,
        forced_ids,
        budget,
        relation_warnings=relation_warnings,
    )
    if included_ids:
        await long_term_memory_service.mark_memory_items_used(included_ids)

    diagnostics = _diagnostics(
        forced=len(forced_ids),
        recalled=max(0, len([i for i in recalled if int(i.get("id")) not in forced_ids])),
        included=len(included_ids),
        deferred=len(deferred_ids),
        suppressed=len(suppressed_ids),
        conflicts=conflict_count,
        relation_expanded=len(relation_expanded),
        character_count=len(text),
    )
    return MemoryContextBlock(
        text=text,
        included_ids=included_ids,
        deferred_ids=deferred_ids,
        suppressed_ids=sorted(suppressed_ids),
        token_estimate=estimate_tokens(text),
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
    *,
    relation_warnings: list[str] | None = None,
) -> tuple[str, list[int], list[int]]:
    if not items:
        return "", [], []

    groups: dict[str, list[str]] = {}
    included_ids: list[int] = []
    included_rows: list[tuple[int, str, str]] = []
    deferred_ids: list[int] = []
    # Keep room for a deferred footer from the beginning. This avoids a final
    # whole-block slice that could claim an id was included after cutting it out.
    footer_reserve = min(160, max(48, budget // 5))
    selection_budget = max(0, budget - footer_reserve)
    fitted_warnings = _fit_relation_warnings(relation_warnings or [], selection_budget)

    for item in items:
        rendered = _render_item(item)
        iid = int(item["id"])
        label = _label_for_item(item)
        if iid in forced_ids and item.get("kind") not in ("foreshadowing",):
            label = "必须遵循的设定"
        candidate_groups = {key: list(rows) for key, rows in groups.items()}
        candidate_groups.setdefault(label, []).append(rendered)
        candidate_text = _compose_memory_text(candidate_groups, fitted_warnings, 0)
        if len(candidate_text) <= selection_budget:
            groups = candidate_groups
            included_ids.append(iid)
            included_rows.append((iid, label, rendered))
            continue

        # Preserve at least a bounded representation of the first/highest
        # priority item when one item alone exceeds its allocation.
        if not included_ids:
            prefix = f"- [id:{iid}|{item.get('kind')}]"
            overhead = (
                len(_compose_memory_text({label: [prefix]}, fitted_warnings, 0))
                - len(prefix)
            )
            available = selection_budget - overhead - 2
            if available > len(prefix) + 8:
                shortened = rendered[: max(len(prefix) + 4, available - 1)] + "…"
                truncated_groups = {label: [shortened]}
                if len(_compose_memory_text(truncated_groups, fitted_warnings, 0)) <= selection_budget:
                    groups = truncated_groups
                    included_ids.append(iid)
                    included_rows.append((iid, label, shortened))
                    continue
        deferred_ids.append(iid)

    text = _compose_memory_text(groups, fitted_warnings, len(deferred_ids))
    # ``footer_reserve`` is conservative, but keep a final invariant guard for
    # unusually large headings/counts. Remove a complete item at a time so an
    # id can never be reported as included after its text has been sliced away.
    while len(text) > budget and included_rows:
        iid, label, rendered = included_rows.pop()
        rows = groups.get(label) or []
        if rendered in rows:
            rows.remove(rendered)
        if not rows:
            groups.pop(label, None)
        included_ids.remove(iid)
        deferred_ids.append(iid)
        text = _compose_memory_text(groups, fitted_warnings, len(deferred_ids))

    # With no included items there is no id/text consistency left to violate.
    # This only protects pathological budgets smaller than the fixed headings.
    if len(text) > budget:
        text = text[:budget]
    return text, included_ids, deferred_ids


def _compose_memory_text(
    groups: dict[str, list[str]],
    relation_warnings: list[str],
    deferred_count: int,
) -> str:
    lines = ["【长期记忆 — 已由宿主按当前写作意图召回】"]
    if relation_warnings:
        lines.append("\n## 记忆关系警告")
        lines.append("以下关系已由宿主检测；不要把存在冲突的内容同时视为确定事实。")
        lines.extend(relation_warnings)
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
    if deferred_count:
        lines.append(
            f"\n另有 {deferred_count} 条相关长期记忆因预算限制未注入，"
            "可用 searchMemories 继续查询。"
        )
    return "\n".join(lines)


def _fit_relation_warnings(warnings: list[str], budget: int) -> list[str]:
    if not warnings or budget <= 0:
        return []
    warning_budget = min(1200, max(80, budget // 4))
    result: list[str] = []
    used = 0
    for warning in warnings[:8]:
        row = str(warning).strip()
        if not row:
            continue
        remaining = warning_budget - used
        if remaining <= 8:
            break
        if len(row) > remaining:
            row = row[: remaining - 1] + "…"
        result.append(row)
        used += len(row) + 1
    return result


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


def _apply_relation_policy(
    ordered: list[dict],
    links: list[dict],
    forced_ids: set[int],
) -> tuple[list[dict], set[int], list[str], int]:
    """Resolve supersedes/contradicts links before context injection.

    Explicit user selection wins over automatic recall.  A newer memory linked
    with ``supersedes`` hides the older one unless the user selected the older
    record.  Unresolved contradictions remain visible with a warning, while a
    conflict against one forced item drops only the automatic candidate.
    """
    candidate_ids = {int(item["id"]) for item in ordered if item.get("id") is not None}
    suppressed: set[int] = set()
    warnings: list[str] = []
    conflict_count = 0

    for link in links:
        try:
            from_id = int(link.get("from_memory_id"))
            to_id = int(link.get("to_memory_id"))
        except (TypeError, ValueError):
            continue
        if from_id not in candidate_ids or to_id not in candidate_ids:
            continue
        relation = str(link.get("relation") or "")
        note = " ".join(str(link.get("note") or "").split()).strip()

        if relation == "supersedes":
            if to_id in forced_ids and from_id not in forced_ids:
                suppressed.add(from_id)
                warnings.append(
                    f"- 用户选择的 [id:{to_id}] 已被 [id:{from_id}] 标记为取代；"
                    "本轮按用户选择保留旧版本。"
                )
            elif to_id not in forced_ids:
                suppressed.add(to_id)
            elif from_id in forced_ids:
                warnings.append(
                    f"- [id:{from_id}] 标记为取代 [id:{to_id}]，但两条均由用户选择。"
                )
            continue

        if relation == "contradicts":
            conflict_count += 1
            from_forced = from_id in forced_ids
            to_forced = to_id in forced_ids
            if from_forced != to_forced:
                automatic_id = to_id if from_forced else from_id
                selected_id = from_id if from_forced else to_id
                suppressed.add(automatic_id)
                warning = (
                    f"- 自动召回的 [id:{automatic_id}] 与用户选择的 [id:{selected_id}] 冲突，"
                    "已优先保留用户选择。"
                )
            else:
                warning = f"- [id:{from_id}] 与 [id:{to_id}] 存在未解决冲突。"
            if note:
                warning += f" 说明：{note}"
            warnings.append(warning)

    kept = [item for item in ordered if int(item.get("id")) not in suppressed]
    return kept, suppressed, warnings, conflict_count


def estimate_tokens(text: str) -> int:
    """Backward-compatible alias for the shared context estimator."""
    return estimate_text_tokens(text)


def _diagnostics(
    *,
    forced: int = 0,
    recalled: int = 0,
    included: int = 0,
    deferred: int = 0,
    suppressed: int = 0,
    conflicts: int = 0,
    relation_expanded: int = 0,
    character_count: int = 0,
) -> dict[str, int]:
    return {
        "forced": forced,
        "recalled": recalled,
        "included": included,
        "deferred": deferred,
        "suppressed": suppressed,
        "conflicts": conflicts,
        "relationExpanded": relation_expanded,
        "characterCount": character_count,
    }


def _context_window_chars(value: Any) -> int:
    key = str(value or "").strip().lower()
    return CONTEXT_WINDOW_CHARS.get(key, CONTEXT_WINDOW_CHARS["200k"])


def _memory_budget(ctx: dict) -> int:
    explicit = ctx.get("memoryBudget")
    if explicit is not None:
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

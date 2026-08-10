"""Book-scoped associated chapter and outline context rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from purra.context_budget import estimate_text_tokens
from domains.writing.contracts import WritingDomainContext
from domains.writing.repositories import AssociatedContextRepository


MAX_ASSOCIATED_IDS_PER_KIND = 128
CONTEXT_WINDOW_TOKENS = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
    "200k": 200_000,
    "256k": 256_000,
    "300k": 300_000,
    "1m": 1_000_000,
}


OUTLINE_CONTEXT_STATUSES = frozenset({
    "complete",
    "truncated",
    "not_injected",
})


@dataclass(frozen=True, slots=True)
class OutlineContextFact:
    """Host-observed injection state for one selected outline."""

    outline_id: str
    status: str
    read_tool: str = "queryOutline"
    locator_available_to_execution: bool = False

    def __post_init__(self) -> None:
        outline_id = str(self.outline_id or "").strip()
        status = str(self.status or "").strip()
        if not outline_id:
            raise ValueError("outline context fact requires an outline id")
        if status not in OUTLINE_CONTEXT_STATUSES:
            raise ValueError(f"unsupported outline context status: {status!r}")
        object.__setattr__(self, "outline_id", outline_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "read_tool", str(self.read_tool or "queryOutline"))
        object.__setattr__(
            self,
            "locator_available_to_execution",
            bool(self.locator_available_to_execution),
        )

    def to_planning_value(self, *, ordinal: int) -> dict[str, str | int | bool]:
        return {
            "ordinal": int(ordinal),
            "status": self.status,
            "readTool": self.read_tool,
            "locatorAvailableToExecution": self.locator_available_to_execution,
        }


@dataclass(frozen=True, slots=True)
class ChapterContextFact:
    """Host-observed injection state for one selected chapter."""

    chapter_id: str
    status: str
    read_tool: str = "getChapterContent"
    locator_available_to_execution: bool = False

    def __post_init__(self) -> None:
        chapter_id = str(self.chapter_id or "").strip()
        status = str(self.status or "").strip()
        if not chapter_id:
            raise ValueError("chapter context fact requires a chapter id")
        if status not in OUTLINE_CONTEXT_STATUSES:
            raise ValueError(f"unsupported chapter context status: {status!r}")
        object.__setattr__(self, "chapter_id", chapter_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "read_tool",
            str(self.read_tool or "getChapterContent"),
        )
        object.__setattr__(
            self,
            "locator_available_to_execution",
            bool(self.locator_available_to_execution),
        )

    def to_planning_value(self, *, ordinal: int) -> dict[str, str | int | bool]:
        return {
            "ordinal": int(ordinal),
            "status": self.status,
            "readTool": self.read_tool,
            "locatorAvailableToExecution": self.locator_available_to_execution,
        }


@dataclass(frozen=True, slots=True)
class AssociatedContextResult:
    """Rendered retrieval text plus facts derived from that exact rendering."""

    text: str = ""
    chapter_facts: tuple[ChapterContextFact, ...] = ()
    outline_facts: tuple[OutlineContextFact, ...] = ()
    outline_source_records: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "chapter_facts", tuple(self.chapter_facts))
        object.__setattr__(self, "outline_facts", tuple(self.outline_facts))
        object.__setattr__(
            self,
            "outline_source_records",
            tuple(
                (str(outline_id).strip(), str(text))
                for outline_id, text in self.outline_source_records
                if str(outline_id).strip() and str(text).strip()
            ),
        )

    def with_outer_truncation(
        self,
    ) -> "AssociatedContextResult":
        """Conservatively revoke every completeness claim after outer fitting."""

        return AssociatedContextResult(
            text=self.text,
            chapter_facts=tuple(
                ChapterContextFact(
                    chapter_id=fact.chapter_id,
                    status=(
                        "truncated"
                        if fact.status == "complete"
                        else fact.status
                    ),
                    read_tool=fact.read_tool,
                    locator_available_to_execution=False,
                )
                for fact in self.chapter_facts
            ),
            outline_facts=tuple(
                OutlineContextFact(
                    outline_id=fact.outline_id,
                    status=(
                        "truncated"
                        if fact.status == "complete"
                        else fact.status
                    ),
                    read_tool=fact.read_tool,
                    # Once a later fitter cuts the host-rendered envelope we
                    # cannot prove which locator markers survived without
                    # trusting user-authored prose. Fail closed instead.
                    locator_available_to_execution=False,
                )
                for fact in self.outline_facts
            ),
            # A later outer fitter may have cut any source at an unknown
            # boundary. Revoke the machine-readable grounding receipt rather
            # than claiming evidence the generation model did not receive.
            outline_source_records=(),
        )


class AssociatedContextBuilder:
    def __init__(self, repository: AssociatedContextRepository):
        self._repository = repository

    async def build(
        self,
        context: WritingDomainContext,
        token_budget: int,
    ) -> AssociatedContextResult:
        book_id = str(context.book_id or "").strip()
        all_chapters = _unique_text(context.associated_chapter_ids)
        all_outlines = _unique_text(context.associated_outline_ids)
        chapter_ids = all_chapters[:MAX_ASSOCIATED_IDS_PER_KIND]
        outline_ids = all_outlines[:MAX_ASSOCIATED_IDS_PER_KIND]
        if not book_id or (not chapter_ids and not outline_ids):
            return AssociatedContextResult()

        budget = max(0, int(token_budget))
        if budget <= 0:
            return AssociatedContextResult(
                chapter_facts=tuple(
                    ChapterContextFact(chapter_id, "not_injected")
                    for chapter_id in all_chapters
                ),
                outline_facts=tuple(
                    OutlineContextFact(outline_id, "not_injected")
                    for outline_id in all_outlines
                ),
            )
        window = _context_window_tokens(context.context_window_label)
        per_chapter = min(budget, min(30_000, max(6_000, window // 25)))
        per_outline = min(budget, min(20_000, max(4_000, window // 35)))

        try:
            chapters = {
                item.id: item
                for item in await self._repository.load_chapters(book_id, chapter_ids)
            }
        except Exception:
            chapters = {}
        try:
            outlines = {
                item.id: item
                for item in await self._repository.load_outlines(book_id, outline_ids)
            }
        except Exception:
            outlines = {}
        chapter_facts: list[ChapterContextFact] = []
        outline_facts: list[OutlineContextFact] = []
        outline_source_records: list[tuple[str, str]] = []

        lines = [
            "【关联上下文 — 用户在本轮勾选的章节/大纲，内容已由宿主注入】",
            "以下内容是用户明确要求你参考的素材，直接依据它们回答；"
            "除标注「已截断」或「未注入」的条目外，无需再调工具重复读取。",
        ]
        remaining = budget
        outline_reserve = 0
        if chapter_ids and outline_ids:
            outline_reserve = min(
                max(1, budget // 2),
                max(min(per_outline, max(1, budget // 2)), budget // 3),
            )
        chapter_budget = max(0, budget - outline_reserve)
        deferred: list[str] = []
        omitted_chapters = len(all_chapters) - len(chapter_ids)
        omitted_outlines = len(all_outlines) - len(outline_ids)
        if omitted_chapters:
            deferred.append(f"- 另有 {omitted_chapters} 个关联章节超过宿主预取数量上限")
        if omitted_outlines:
            deferred.append(f"- 另有 {omitted_outlines} 个关联大纲超过宿主预取数量上限")

        for index, chapter_id in enumerate(chapter_ids):
            item = chapters.get(chapter_id)
            title = (
                _clean_title(item.title)
                if item is not None
                else _title_of(context.writing_chapters, chapter_id)
            )
            if item is None:
                deferred.append(f"- 章节 chapterId={chapter_id} 《{title}》")
                chapter_facts.append(ChapterContextFact(
                    chapter_id,
                    "not_injected",
                    locator_available_to_execution=True,
                ))
                continue
            if chapter_budget <= 0:
                deferred.append(f"- 章节 chapterId={chapter_id} 《{title}》")
                chapter_facts.append(ChapterContextFact(
                    chapter_id,
                    "not_injected",
                    locator_available_to_execution=True,
                ))
                continue
            text = item.text
            remaining_chapters = max(1, len(chapter_ids) - index)
            cap = min(per_chapter, max(1, chapter_budget // remaining_chapters))
            shown = text[:cap]
            remaining -= len(shown)
            chapter_budget -= len(shown)
            lines.append(f"\n## 关联章节《{title}》(chapterId={chapter_id})")
            lines.append(shown if shown.strip() else "（本章暂无正文）")
            if len(text) > cap:
                chapter_facts.append(ChapterContextFact(
                    chapter_id,
                    "truncated",
                    locator_available_to_execution=True,
                ))
                lines.append(
                    f"…（已截断，全文共 {len(text)} 字，"
                    f"可用 getChapterContent 读取，参数 chapterId=\"{chapter_id}\"）"
                )
            else:
                chapter_facts.append(ChapterContextFact(
                    chapter_id,
                    "complete",
                    locator_available_to_execution=True,
                ))

        chapter_facts.extend(
            ChapterContextFact(chapter_id, "not_injected")
            for chapter_id in all_chapters[len(chapter_ids):]
        )

        for index, outline_id in enumerate(outline_ids):
            item = outlines.get(outline_id)
            title = (
                _clean_title(item.title)
                if item is not None
                else _title_of(context.available_outlines, outline_id)
            )
            markdown = item.markdown if item is not None else ""
            lines.append(f"\n## 关联大纲《{title}》(outlineId={outline_id})")
            if item is None:
                lines.append("（该大纲暂无文本内容）")
                outline_facts.append(OutlineContextFact(
                    outline_id,
                    "not_injected",
                    locator_available_to_execution=True,
                ))
                continue
            if not markdown.strip():
                lines.append("（该大纲暂无文本内容）")
                outline_facts.append(OutlineContextFact(
                    outline_id,
                    "complete",
                    locator_available_to_execution=True,
                ))
                continue
            if remaining <= 0:
                lines.pop()
                deferred.append(f"- 大纲 outlineId={outline_id} 《{title}》")
                outline_facts.append(OutlineContextFact(
                    outline_id,
                    "not_injected",
                    locator_available_to_execution=True,
                ))
                continue
            remaining_outlines = max(1, len(outline_ids) - index)
            cap = min(per_outline, max(1, remaining // remaining_outlines))
            shown = markdown[:cap]
            remaining -= len(shown)
            lines.append(shown)
            if shown.strip():
                outline_source_records.append((outline_id, shown))
            if len(markdown) > cap:
                outline_facts.append(OutlineContextFact(
                    outline_id,
                    "truncated",
                    locator_available_to_execution=True,
                ))
                lines.append(
                    "…（已截断，可用 queryOutline 读取完整内容，"
                    f"参数 outlineIds=[\"{outline_id}\"]、includeText=true）"
                )
            else:
                outline_facts.append(OutlineContextFact(
                    outline_id,
                    "complete",
                    locator_available_to_execution=True,
                ))

        outline_facts.extend(
            OutlineContextFact(outline_id, "not_injected")
            for outline_id in all_outlines[len(outline_ids):]
        )

        if deferred:
            lines.append("\n以下条目本轮未注入内容（超出注入预算或读取失败），回复前请用工具读取：")
            lines.extend(deferred)
        rendered = "\n".join(lines)
        fitted = _fit_text_to_token_budget(rendered, budget)
        result = AssociatedContextResult(
            text=fitted,
            chapter_facts=tuple(chapter_facts),
            outline_facts=tuple(outline_facts),
            outline_source_records=tuple(outline_source_records),
        )
        return (
            result.with_outer_truncation()
            if fitted != rendered
            else result
        )


def _unique_text(values: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip() for value in values if str(value).strip()
    ))


def _clean_title(value: Any) -> str:
    return re.sub(r"\r?\n", " ", str(value or "")).strip() or "（无标题）"


def _title_of(items: Sequence[Any], target_id: str) -> str:
    for item in items:
        if isinstance(item, dict) and str(item.get("id")) == target_id:
            return _clean_title(item.get("title"))
        if hasattr(item, "get") and str(item.get("id")) == target_id:
            return _clean_title(item.get("title"))
    return "（未匹配标题）"


def _context_window_tokens(label: str | None) -> int:
    return CONTEXT_WINDOW_TOKENS.get(
        str(label or "").strip().lower(),
        CONTEXT_WINDOW_TOKENS["200k"],
    )


def _fit_text_to_token_budget(text: str, token_budget: int) -> str:
    if estimate_text_tokens(text) <= token_budget:
        return text
    marker = "\n…（关联上下文已按统一 token 预算截断）"
    if estimate_text_tokens(marker) >= token_budget:
        marker = ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_text_tokens(text[:middle] + marker) <= token_budget:
            low = middle
        else:
            high = middle - 1
    return text[:low] + marker

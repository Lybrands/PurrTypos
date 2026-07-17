"""Writing-domain eligibility policy for Agent planning."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from agent_core.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
)
from domains.writing.contracts import WritingDomainContext


WRITING_TOOL_PLANNING_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "editGlobalOutline": ("getGlobalOutline",),
    "queryOutline": ("listOutlines",),
    "updateOutline": ("listOutlines", "queryOutline"),
    "createWritingChapter": ("listWritingChapters",),
    "getChapterContent": ("listWritingChapters",),
    "batchGetChapterContents": ("listWritingChapters",),
    "editChapterContent": ("listWritingChapters",),
    "addForeshadowing": ("listWritingChapters",),
    "getBookCharacters": ("listBookCharacters",),
    "createCharacter": ("listBookCharacters",),
    "updateCharacter": ("listBookCharacters",),
    "deleteCharacter": ("listBookCharacters",),
    "editStoryBackground": ("getStoryBackground",),
    "getSettingEntities": ("listSettingEntities",),
    "createSettingEntity": ("listSettingEntities",),
    "updateSettingEntity": ("listSettingEntities",),
    "deleteSettingEntity": ("listSettingEntities",),
}

class WritingPlanningPolicy:
    """Keep writing eligibility rules outside the business-agnostic Core."""

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        satisfied = set(
            capabilities.constraints.context_satisfied_tool_names
        )
        planning_excluded = set(
            capabilities.constraints.planning_excluded_tool_names
        )
        satisfied_edges = set(
            capabilities.constraints.satisfied_tool_dependency_edges
        )
        context = WritingDomainContext.from_core_context(request.domain_context)
        user_text = request.latest_user_text().strip()

        if _bound_current_chapter_satisfies_catalog_dependency(
            user_text,
            context,
            capabilities,
        ):
            # The host-bound chapter id satisfies only the locator edge needed
            # by getChapterContent.  listWritingChapters itself remains
            # available for explicit catalogue requests and other consumers.
            satisfied_edges.add((
                "getChapterContent",
                "listWritingChapters",
            ))

        complete_selected_memory_tools = _complete_selected_memory_tool_names(
            capabilities.host_planning_facts,
            context,
        )
        selected_memory_tools = (
            complete_selected_memory_tools
            & capabilities.available_tool_names
        )
        if (
            selected_memory_tools
            and _request_uses_selected_memories(user_text, context)
            and not _requests_fresh_selected_memory_read(
                user_text,
                selected_memory_tools,
            )
            and not _requests_other_memory_scope(user_text)
        ):
            satisfied.update(selected_memory_tools)

        complete_associated_chapter_tools = (
            _complete_associated_chapter_tool_names(
                capabilities.host_planning_facts,
                context,
            )
        )
        associated_chapter_tools = (
            complete_associated_chapter_tools
            & capabilities.available_tool_names
        )
        if (
            associated_chapter_tools
            and _request_uses_associated_chapters(user_text, context)
            and not _requests_fresh_associated_chapter_read(
                user_text,
                associated_chapter_tools,
            )
            and not _requests_other_chapter_scope(user_text, context)
        ):
            satisfied.update(associated_chapter_tools)

        if (
            "queryOutline" in capabilities.available_tool_names
            and _all_associated_outlines_are_complete(
                capabilities.host_planning_facts,
                context,
            )
            and _request_uses_selected_outlines(user_text, context)
            and not _requests_fresh_outline_read(user_text)
            and not _requests_other_outline_scope(user_text, context)
        ):
            satisfied.add("queryOutline")

        if _request_is_bounded_selected_evidence_analysis(
            user_text,
            context,
            capabilities.host_planning_facts,
            complete_selected_memory=bool(complete_selected_memory_tools),
            complete_associated_chapters=bool(
                complete_associated_chapter_tools
            ),
            available_tool_names=capabilities.available_tool_names,
            context_satisfied_tool_names=frozenset(satisfied),
        ):
            planning_excluded.update(
                capabilities.available_tool_names - satisfied
            )

        return PlanningConstraints(
            context_satisfied_tool_names=frozenset(satisfied),
            planning_excluded_tool_names=frozenset(planning_excluded),
            satisfied_tool_dependency_edges=frozenset(satisfied_edges),
        )

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        context = WritingDomainContext.from_core_context(request.domain_context)
        text = request.latest_user_text().strip()
        if not text or not context.book_id:
            return False
        mode = (request.mode or "").strip().lower()
        # Eligibility follows the user's explicit tools setting.  Catalog
        # contents are validated separately by Core and must not silently
        # change whether the application presents a task plan.
        del capabilities
        if mode == "ask" and not request.tools_enabled:
            return False
        if mode != "agent" and not request.tools_enabled:
            return False
        return len(text) >= 8


def _bound_current_chapter_satisfies_catalog_dependency(
    text: str,
    context: WritingDomainContext,
    capabilities: PlanningCapabilities,
) -> bool:
    """Narrowly waive locator discovery for an unambiguous bound-chapter read.

    This does not claim that the whole chapter catalogue was fetched.  It only
    treats its locator-discovery dependency as satisfied when the host has
    already supplied the exact current chapter locator.  Every uncertain or
    broader request deliberately fails open to the normal catalogue path.
    """

    available = capabilities.available_tool_names
    if not {"getChapterContent", "listWritingChapters"} <= available:
        return False
    if not str(context.chapter_id or "").strip():
        return False
    if not _bound_chapter_locator_is_consistent(context):
        return False

    current_fact = capabilities.host_planning_facts.get("currentChapter")
    if not isinstance(current_fact, Mapping):
        return False
    if current_fact.get("bound") is not True:
        return False
    if current_fact.get("singleChapterToolsMayOmitChapterId") is not True:
        return False
    if _requests_chapter_catalog(text):
        return False
    if _requests_other_or_multiple_chapters(text, context):
        return False
    if _negates_bound_current_chapter(text):
        return False
    return _request_targets_bound_current_chapter(text, context)


def _bound_chapter_locator_is_consistent(
    context: WritingDomainContext,
) -> bool:
    """Reject a stale or non-leaf bound locator when a host catalog exists."""

    if not context.writing_chapters:
        # Some current-chapter entry points intentionally omit the UI catalog.
        # The book-scoped handler remains the authoritative existence check.
        return True
    current_id = str(context.chapter_id or "").strip()
    current = next(
        (
            item
            for item in context.writing_chapters
            if str(item.get("id") or "").strip() == current_id
        ),
        None,
    )
    if current is None or not str(current.get("title") or "").strip():
        return False
    parent_ids = {
        str(
            item.get("parent_id")
            if item.get("parent_id") is not None
            else item.get("parentId")
        ).strip()
        for item in context.writing_chapters
        if (
            item.get("parent_id") is not None
            or item.get("parentId") is not None
        )
        and str(
            item.get("parent_id")
            if item.get("parent_id") is not None
            else item.get("parentId")
        ).strip()
    }
    return current_id not in parent_ids


def _negates_bound_current_chapter(text: str) -> bool:
    current_reference = (
        r"(?:当前|本|这|选中|已选|正在编辑(?:的)?|已打开(?:的)?)"
        r"(?:章节|章|正文)"
    )
    if re.search(
        r"(?:不要|无需|无须|不用|不必|别|禁止|跳过|"
        r"不(?:读取|读|看|分析|总结|修改|改写|使用))"
        r"[^，。；;!?！？\n]{0,20}" + current_reference,
        text,
    ):
        return True
    lowered = text.casefold()
    return bool(re.search(
        r"(?:do\s+not|don't|without|skip)\b"
        r"[^.!?\n]{0,40}\b(?:(?:current|this|bound|selected|open)\s+chapter"
        r"|currently\s+open\s+chapter)\b",
        lowered,
    ))


def _request_targets_bound_current_chapter(
    text: str,
    context: WritingDomainContext,
) -> bool:
    if re.search(
        r"(?:当前|本|这|选中|已选|正在编辑(?:的)?|已打开(?:的)?)"
        r"(?:章节|章|正文)",
        text,
    ):
        return True

    lowered = text.casefold()
    if re.search(
        r"\b(?:current|this|bound|selected|open)\s+chapter\b"
        r"|\bcurrently\s+open\s+chapter\b",
        lowered,
    ):
        return True

    chapter_id = str(context.chapter_id or "").strip()
    if len(chapter_id) >= 3 and _contains_identifier_reference(
        lowered,
        chapter_id.casefold(),
    ):
        return True
    title = str(context.current_chapter_title or "").strip()
    return _contains_explicit_title_reference(lowered, title.casefold())


def _contains_identifier_reference(text: str, identifier: str) -> bool:
    return bool(re.search(
        rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])",
        text,
    ))


def _contains_explicit_title_reference(text: str, title: str) -> bool:
    if len(title) < 3 or title not in text:
        return False
    escaped = re.escape(title)
    if re.search(rf"(?:《|『|「|\")\s*{escaped}\s*(?:》|』|」|\")", text):
        return True
    return bool(re.search(
        r"(?:读取|读|查看|打开|分析|总结|审阅|校对|修改|改写|续写|"
        r"对照|比较|review|read|open|analy[sz]e|summari[sz]e|edit)"
        rf"[^，。；;!?！？\n]{{0,16}}{escaped}"
        r"(?=$|[\s，。；;!?！？、》』」\"']|并|后|的|内容|正文|and\b|then\b)",
        text,
        flags=re.IGNORECASE,
    ))


def _contains_selected_title_reference(
    text: str,
    title: str,
    *,
    kind: str,
) -> bool:
    normalized_title = str(title or "").strip()
    lowered = text.casefold()
    lowered_title = normalized_title.casefold()
    if len(normalized_title) < 3 or lowered_title not in lowered:
        return False
    escaped = re.escape(normalized_title)
    if re.search(
        rf"(?:《|『|「|\")\s*{escaped}\s*(?:》|』|」|\")",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    if kind == "chapter":
        if re.search(
            r"第\s*[零一二三四五六七八九十百千万两\d]+\s*(?:章|节|卷)",
            normalized_title,
        ):
            return True
        return bool(re.search(
            rf"(?:章节|正文|chapter)\s*(?:标题|名为|named|titled)?"
            rf"[^，。；;!?！？\n]{{0,8}}{escaped}"
            rf"|{escaped}\s*(?:章节|正文|chapter)",
            text,
            flags=re.IGNORECASE,
        ))
    if kind == "outline":
        return bool(re.search(
            rf"(?:大纲|纲要|outline)\s*(?:标题|名为|named|titled)?"
            rf"[^，。；;!?！？\n]{{0,8}}{escaped}"
            rf"|{escaped}\s*(?:大纲|纲要|outline)",
            text,
            flags=re.IGNORECASE,
        ))
    return False


def _requests_chapter_catalog(text: str) -> bool:
    if "listWritingChapters" in text:
        return True
    if re.search(
        r"(?:章节|章)(?:列表|目录|清单|树)"
        r"|(?:列出|罗列|展示|查看|浏览|查询)"
        r"(?:本书|作品)?(?:所有|全部)?(?:的)?章节"
        r"|(?:有哪些|多少个?)章节"
        r"|(?:全书|本书|作品)(?:的)?(?:章节|目录)"
        r"(?:结构|层级|顺序|编排|组织)"
        r"|目录(?:结构|层级|顺序|编排)"
        r"|章节(?:顺序|编排|层级)"
        r"|(?:各|每|所有|全部)(?:个)?(?:章节|章)"
        r"(?:标题|顺序|结构|梗概)"
        r"|(?:全书|本书|作品)?(?:有|共)?(?:几|多少)(?:个)?(?:章节|章)"
        r"|(?:查看|打开)(?:本书|作品)?(?:的)?"
        r"(?:章节目录|章节列表|目录)",
        text,
    ):
        return True
    lowered = text.casefold()
    return bool(re.search(
        r"\bchapter\s+(?:list|catalog|directory)\b"
        r"|\b(?:list|show|display|browse)\s+(?:all\s+)?chapters\b"
        r"|\bhow\s+many\s+chapters\b"
        r"|\bchapters?\s+(?:order|sequence|hierarchy|catalog)\b"
        r"|\b(?:book|novel)\s+chapter\s+(?:structure|order|sequence)\b"
        r"|\btable\s+of\s+contents\b",
        lowered,
    ))


def _requests_other_or_multiple_chapters(
    text: str,
    context: WritingDomainContext,
) -> bool:
    if re.search(
        r"(?:其他|其它|另一个|另外|上一|下一|前一|后一|"
        r"前几|后几|相邻|邻近|周边|多个|若干|"
        r"全部|所有|每个|各个)(?:的)?(?:章节|章|正文)"
        r"|(?:前|后|最近|最前|最后|头|首)\s*"
        r"[零一二三四五六七八九十百千万两\d]+\s*(?:个)?"
        r"(?:章节|章|正文)"
        r"|(?:前文|后文|上文|下文|此前(?:内容|章节)|后续(?:内容|章节))"
        r"|(?:跨章|多章|前后章节|前后章)",
        text,
    ):
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:other|another|previous|next|adjacent|neighboring|surrounding|"
        r"earlier|later|multiple|several|all|every|both)\s+chapters?\b"
        r"|\bacross\s+(?:the\s+)?chapters\b"
        r"|\bchapters?\s+(?:before|after)\s+(?:this|the\s+current)\b",
        lowered,
    ):
        return True

    current_title = str(context.current_chapter_title or "").strip()
    normalized_title = re.sub(r"\s+", "", current_title)
    for match in re.finditer(
        r"第\s*[零一二三四五六七八九十百千万两\d]+\s*(?:章|节|卷)",
        text,
    ):
        if re.sub(r"\s+", "", match.group()) not in normalized_title:
            return True

    normalized_english_title = re.sub(r"\s+", " ", current_title.casefold())
    for match in re.finditer(
        r"\bchapter\s+(?:\d+|one|two|three|four|five|six|seven|eight|"
        r"nine|ten|eleven|twelve)\b",
        lowered,
    ):
        if re.sub(r"\s+", " ", match.group()) not in normalized_english_title:
            return True

    current_id = str(context.chapter_id or "").strip()
    for item in context.writing_chapters:
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id == current_id:
            continue
        title = str(item.get("title") or "").strip()
        if len(item_id) >= 3 and item_id.casefold() in lowered:
            return True
        if title and title.casefold() in lowered:
            return True
    return False


def _complete_selected_memory_tool_names(
    host_facts: Mapping[str, object],
    context: WritingDomainContext,
) -> frozenset[str]:
    raw = host_facts.get("selectedMemories")
    if not isinstance(raw, Mapping):
        return frozenset()
    requested = _strict_nonnegative_int(raw.get("requestedCount"))
    complete = _strict_nonnegative_int(raw.get("completeCount"))
    truncated = _strict_nonnegative_int(raw.get("truncatedCount"))
    not_injected = _strict_nonnegative_int(raw.get("notInjectedCount"))
    selected_count = len({
        *(('memory', str(value).strip()) for value in context.selected_memory_ids
          if str(value).strip()),
        *(('foreshadowing', str(value).strip())
          for value in context.selected_foreshadowing_ids
          if str(value).strip()),
    })
    if (
        requested is None
        or complete is None
        or truncated is None
        or not_injected is None
        or requested <= 0
        or selected_count != requested
        or str(raw.get("status") or "") != "complete"
        or complete != requested
        or truncated != 0
        or not_injected != 0
        or complete + truncated + not_injected != requested
    ):
        return frozenset()
    raw_tools = raw.get("searchTools")
    if (
        not isinstance(raw_tools, Sequence)
        or isinstance(raw_tools, (str, bytes))
    ):
        return frozenset()
    tools = tuple(dict.fromkeys(
        str(value).strip() for value in raw_tools if str(value).strip()
    ))
    allowed = {"searchMemories", "searchSparkIdeas"}
    if not tools or any(tool not in allowed for tool in tools):
        return frozenset()
    return frozenset(tools)


def _complete_associated_chapter_tool_names(
    host_facts: Mapping[str, object],
    context: WritingDomainContext,
) -> frozenset[str]:
    raw = host_facts.get("associatedChapters")
    if not isinstance(raw, Mapping):
        return frozenset()
    selected = _strict_nonnegative_int(raw.get("selectedCount"))
    complete = _strict_nonnegative_int(raw.get("completeCount"))
    associated_count = len({
        str(value).strip()
        for value in context.associated_chapter_ids
        if str(value).strip()
    })
    items = raw.get("items")
    if (
        selected is None
        or complete is None
        or selected <= 0
        or selected != associated_count
        or complete != selected
        or not isinstance(items, Sequence)
        or isinstance(items, (str, bytes))
        or len(items) != selected
    ):
        return frozenset()
    tools: set[str] = set()
    for index, item in enumerate(items, start=1):
        if (
            not isinstance(item, Mapping)
            or _strict_nonnegative_int(item.get("ordinal")) != index
            or str(item.get("status") or "") != "complete"
            or str(item.get("readTool") or "") != "getChapterContent"
        ):
            return frozenset()
        tools.add("getChapterContent")
    return frozenset(tools)


def _strict_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _request_uses_selected_memories(
    text: str,
    context: WritingDomainContext,
) -> bool:
    if not (context.selected_memory_ids or context.selected_foreshadowing_ids):
        return False
    if re.search(
        r"(?:关联|已选|选中|所选|勾选|给定)(?:的)?"
        r"(?:(?!(?:章节|章|正文))[^，。；;!?！？\n]){0,20}"
        r"(?:记忆|设定|伏笔)",
        text,
    ):
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:associated|selected|checked|provided)\b"
        r"(?:(?!\bchapters?\b)[^.!?\n]){0,40}"
        r"\b(?:memories|memory|settings|ideas|foreshadowing)\b",
        lowered,
    ):
        return True
    return any(
        len(identifier) >= 3 and _contains_identifier_reference(lowered, identifier)
        for identifier in (
            str(value).strip().casefold()
            for value in (
                *context.selected_memory_ids,
                *context.selected_foreshadowing_ids,
            )
        )
    )


def _request_uses_associated_chapters(
    text: str,
    context: WritingDomainContext,
) -> bool:
    associated_ids = {
        str(value).strip()
        for value in context.associated_chapter_ids
        if str(value).strip()
    }
    if not associated_ids:
        return False
    if re.search(
        r"(?:关联|已选|选中|所选|勾选|给定)(?:的)?"
        r"[^。；;!?！？\n]{0,30}(?:章节|正文)",
        text,
    ):
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:associated|selected|checked|provided)\b"
        r"[^.!?\n]{0,40}\bchapters?\b",
        lowered,
    ):
        return True
    for item in context.writing_chapters:
        if str(item.get("id") or "").strip() not in associated_ids:
            continue
        title = str(item.get("title") or "").strip()
        if _contains_selected_title_reference(text, title, kind="chapter"):
            return True
    return any(
        len(identifier) >= 3
        and _contains_identifier_reference(lowered, identifier.casefold())
        for identifier in associated_ids
    )


def _mentions_chapter_reference(text: str) -> bool:
    if re.search(
        r"(?:章节|正文)"
        r"|第\s*[零一二三四五六七八九十百千万两\d]+\s*(?:章|节|卷)"
        r"|(?:本|这|当前|关联|已选|选中|所选|勾选|上一|下一|其他)\s*章",
        text,
    ):
        return True
    return bool(re.search(r"\bchapters?\b", text.casefold()))


def _requests_fresh_selected_memory_read(
    text: str,
    tool_names: frozenset[str],
) -> bool:
    if _explicit_tool_use_requested(text, tool_names):
        return True
    chinese_patterns = (
        r"(?:重新|再次|再|刷新|重载|同步|重搜|重查|重读)\s*"
        r"(?:搜索|检索|查询|读取|加载)?\s*(?:已选|选中|所选|勾选)?"
        r"(?:的)?(?:记忆|设定|伏笔)",
        r"(?:搜索|检索|查询|获取)\s*(?:最新(?:的|版本)?)?\s*"
        r"(?:已选|选中|所选|勾选)?(?:的)?(?:记忆|设定|伏笔)",
        r"最新(?:的|版本|内容)?\s*(?:已选|选中|所选|勾选)?"
        r"(?:的)?(?:记忆|设定|伏笔)",
    )
    if any(
        _has_non_negated_match(text, pattern, chinese=True)
        for pattern in chinese_patterns
    ):
        return True
    if _has_non_negated_match(
        text,
        r"(?:搜索|检索|查询|查找|搜寻)",
        chinese=True,
    ):
        return True
    lowered = text.casefold()
    english_patterns = (
        r"\b(?:refresh|reload|re-?read|search\s+again|sync)\b"
        r"[^.!?\n]{0,36}\b(?:memories|memory|settings|ideas|foreshadowing)\b",
        r"\b(?:search|fetch|load|read)\b[^.!?\n]{0,20}\b(?:latest|fresh)\b"
        r"[^.!?\n]{0,24}\b(?:memories|memory|settings|ideas|foreshadowing)\b",
    )
    if any(
        _has_non_negated_match(lowered, pattern, chinese=False)
        for pattern in english_patterns
    ):
        return True
    return _has_non_negated_match(
        lowered,
        r"\b(?:search|query|look\s+up)\b",
        chinese=False,
    )


def _requests_other_memory_scope(text: str) -> bool:
    narrowed = re.sub(
        r"(?:全部|所有|每条)(?:的)?(?:已选|选中|所选|勾选)(?:的)?"
        r"(?:记忆|设定|伏笔)",
        "",
        text,
    )
    if re.search(
        r"(?:其他|其它|另外|额外|更多|未选|未勾选|非关联|任意|"
        r"全部|所有|全书)(?:的)?(?:记忆|设定|伏笔)",
        narrowed,
    ):
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:other|additional|more|unselected|unchecked|all|any|book-wide)\b"
        r"[^.!?\n]{0,24}\b(?:memories|memory|settings|ideas|foreshadowing)\b",
        lowered,
    ):
        return True
    generic_broadening = _has_non_negated_match(
        text,
        r"(?:扩大|扩展|补充)(?:证据|资料|上下文|检索)(?:范围)?",
        chinese=True,
    ) or _has_non_negated_match(
        lowered,
        r"\b(?:broaden|expand|supplement)\b[^.!?\n]{0,24}"
        r"\b(?:evidence|context|search)\b",
        chinese=False,
    )
    return generic_broadening and not _mentions_known_broader_evidence_source(text)


def _requests_fresh_associated_chapter_read(
    text: str,
    tool_names: frozenset[str],
) -> bool:
    if _explicit_tool_use_requested(text, tool_names):
        return True
    chinese_patterns = (
        r"(?:重新|再次|再|刷新|重载|同步|重读|重查)\s*"
        r"(?:读取|查询|获取|加载)?\s*(?:关联|已选|选中|所选|勾选)?"
        r"(?:的)?(?:章节|章|正文)",
        r"(?:读取|查询|获取|加载)\s*(?:当前)?最新(?:的|版本)?\s*"
        r"(?:关联|已选|选中|所选|勾选)?(?:的)?(?:章节|章|正文)",
        r"最新(?:的|版本|内容)?\s*(?:关联|已选|选中|所选|勾选)?"
        r"(?:的)?(?:章节|章|正文)",
    )
    if any(
        _has_non_negated_match(text, pattern, chinese=True)
        for pattern in chinese_patterns
    ):
        return True
    lowered = text.casefold()
    return any(
        _has_non_negated_match(lowered, pattern, chinese=False)
        for pattern in (
            r"\b(?:refresh|reload|re-?read|read\s+again|sync)\b"
            r"[^.!?\n]{0,32}\bchapters?\b",
            r"\b(?:fetch|read|load)\b[^.!?\n]{0,16}\b(?:latest|fresh)\b"
            r"[^.!?\n]{0,20}\bchapters?\b",
        )
    )


def _requests_other_chapter_scope(
    text: str,
    context: WritingDomainContext,
) -> bool:
    if _requests_chapter_catalog(text):
        return True
    narrowed = re.sub(
        r"(?:全部|所有|每个|各个)(?:的)?(?:关联|已选|选中|所选|勾选)"
        r"(?:的)?(?:章节|章|正文)",
        "",
        text,
    )
    if re.search(
        r"(?:其他|其它|另一个|另外|未选|未关联|非关联|上一|下一|"
        r"相邻|前后|全部|所有|全书|每个|各个)(?:的)?(?:章节|章|正文)"
        r"|(?:跨章|前文|后文|上文|(?<!上)下文)",
        narrowed,
    ):
        return True
    lowered = text.casefold()
    if re.search(
        r"\b(?:other|another|previous|next|adjacent|unselected|unassociated|"
        r"all|every|book-wide)\b[^.!?\n]{0,20}\bchapters?\b"
        r"|\bacross\s+(?:the\s+)?chapters\b",
        lowered,
    ):
        return True
    associated_ids = {
        str(value).strip()
        for value in context.associated_chapter_ids
        if str(value).strip()
    }
    associated_titles = [
        str(item.get("title") or "").strip()
        for item in context.writing_chapters
        if str(item.get("id") or "").strip() in associated_ids
    ]
    associated_chinese_ordinals = {
        re.sub(r"\s+", "", match.group())
        for title in associated_titles
        for match in re.finditer(
            r"第\s*[零一二三四五六七八九十百千万两\d]+\s*(?:章|节|卷)",
            title,
        )
    }
    for match in re.finditer(
        r"第\s*[零一二三四五六七八九十百千万两\d]+\s*(?:章|节|卷)",
        text,
    ):
        if re.sub(r"\s+", "", match.group()) not in associated_chinese_ordinals:
            return True
    associated_english_ordinals = {
        re.sub(r"\s+", " ", match.group().casefold())
        for title in associated_titles
        for match in re.finditer(
            r"\bchapter\s+(?:\d+|one|two|three|four|five|six|seven|eight|"
            r"nine|ten|eleven|twelve)\b",
            title,
            flags=re.IGNORECASE,
        )
    }
    for match in re.finditer(
        r"\bchapter\s+(?:\d+|one|two|three|four|five|six|seven|eight|"
        r"nine|ten|eleven|twelve)\b",
        lowered,
    ):
        if re.sub(r"\s+", " ", match.group()) not in associated_english_ordinals:
            return True
    current_id = str(context.chapter_id or "").strip()
    if (
        _request_targets_bound_current_chapter(text, context)
        and current_id not in associated_ids
    ):
        return True
    for item in context.writing_chapters:
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in associated_ids:
            continue
        title = str(item.get("title") or "").strip()
        if len(item_id) >= 3 and _contains_identifier_reference(
            lowered,
            item_id.casefold(),
        ):
            return True
        if title and title.casefold() in lowered:
            return True
    return False


def _explicit_tool_use_requested(
    text: str,
    tool_names: frozenset[str],
) -> bool:
    for tool_name in tool_names:
        for match in re.finditer(re.escape(tool_name), text, flags=re.IGNORECASE):
            prefix = text[max(0, match.start() - 28):match.start()]
            if re.search(
                r"(?:不要|无需|无须|不用|不必|别|禁止)\s*"
                r"(?:调用|使用|执行)?\s*$",
                prefix,
            ):
                continue
            if re.search(
                r"(?:do\s+not|don't|without|no\s+need\s+to|never)\s*"
                r"(?:call|use|invoke)?\s*$",
                prefix,
                flags=re.IGNORECASE,
            ):
                continue
            return True
    return False


def _request_is_bounded_selected_evidence_analysis(
    text: str,
    context: WritingDomainContext,
    host_facts: Mapping[str, object],
    *,
    complete_selected_memory: bool,
    complete_associated_chapters: bool,
    available_tool_names: frozenset[str],
    context_satisfied_tool_names: frozenset[str],
) -> bool:
    uses_memory = _request_uses_selected_memories(text, context)
    uses_chapters = _request_uses_associated_chapters(text, context)
    uses_outlines = _request_uses_selected_outlines(text, context)
    if not (uses_memory or uses_chapters or uses_outlines):
        return False
    if not _explicitly_bounds_selected_evidence_analysis(text):
        return False
    if _mentions_chapter_reference(text):
        if not uses_chapters:
            return False
    if re.search(r"(?:记忆|设定|伏笔)|\b(?:memories|memory|settings|ideas|foreshadowing)\b", text, re.IGNORECASE):
        if not uses_memory:
            return False
    if re.search(r"大纲|\boutlines?\b", text, re.IGNORECASE):
        if not uses_outlines:
            return False
    if uses_memory and not complete_selected_memory:
        return False
    if uses_chapters and not complete_associated_chapters:
        return False
    if uses_outlines and not _all_associated_outlines_are_complete(
        host_facts,
        context,
    ):
        return False
    if not _all_present_selected_evidence_is_complete(
        host_facts,
        context,
        complete_selected_memory=complete_selected_memory,
        complete_associated_chapters=complete_associated_chapters,
    ):
        return False
    if uses_memory and (
        _requests_fresh_selected_memory_read(
            text,
            _complete_selected_memory_tool_names(host_facts, context),
        )
        or _requests_other_memory_scope(text)
    ):
        return False
    if uses_chapters and (
        _requests_fresh_associated_chapter_read(
            text,
            _complete_associated_chapter_tool_names(host_facts, context),
        )
        or _requests_other_chapter_scope(text, context)
    ):
        return False
    if uses_outlines and (
        _requests_fresh_outline_read(text)
        or _requests_other_outline_scope(text, context)
    ):
        return False
    explicitly_requested_tools = {
        name
        for name in available_tool_names
        if _explicit_tool_use_requested(text, frozenset({name}))
    }
    if explicitly_requested_tools - context_satisfied_tool_names:
        return False
    if _requests_broader_evidence_source(text):
        return False
    if _request_targets_bound_current_chapter(text, context):
        current_id = str(context.chapter_id or "").strip()
        associated_ids = {
            str(value).strip()
            for value in context.associated_chapter_ids
            if str(value).strip()
        }
        if not complete_associated_chapters or current_id not in associated_ids:
            return False
    if any(
        _has_non_negated_match(text, pattern, chinese=True)
        for pattern in (
            r"(?:修改|改写|保存|删除|创建|新增|更新|写入|替换|应用修改)",
        )
    ):
        return False
    lowered = text.casefold()
    if any(
        _has_non_negated_match(lowered, pattern, chinese=False)
        for pattern in (
            r"\b(?:edit|modify|save|delete|create|update|rewrite|write\s+back)\b",
            r"\bapply\s+(?:the\s+)?changes?\b",
        )
    ):
        return False
    return bool(re.search(
        r"(?:分析|列出|说明|对照|比较|总结|审阅|检查|找出|识别|评估|建议)"
        r"|\b(?:analy[sz]e|list|explain|compare|summari[sz]e|review|"
        r"inspect|identify|assess|suggest)\b",
        text,
        flags=re.IGNORECASE,
    ))


def _explicitly_bounds_selected_evidence_analysis(text: str) -> bool:
    chinese_patterns = (
        r"(?:只|仅|只能|仅仅)[^，。；;!?！？\n]{0,24}"
        r"(?:分析|引用|依据|根据|基于|按照|使用|对照|比较|检查)",
        r"(?:只|仅|只能|仅仅)(?:依据|根据|基于|按照|对照)\s*"
        r"(?:当前)?(?:关联|已选|选中|所选|勾选|给定)",
        r"(?:不要|无需|无须|不用|不必)"
        r"[^，。；;!?！？\n]{0,12}(?:扩大|扩展|补充)"
        r"(?:证据|资料|上下文|范围)?",
    )
    if any(
        _has_non_negated_match(text, pattern, chinese=True)
        for pattern in chinese_patterns
    ):
        return True
    lowered = text.casefold()
    english_patterns = (
        r"\b(?:only|solely|exclusively)\b[^.!?\n]{0,32}"
        r"\b(?:analy[sz]e|cite|use|compare|review|inspect|base)\b",
        r"\b(?:do\s+not|don't)\b[^.!?\n]{0,20}"
        r"\b(?:broaden|expand|supplement)\b",
    )
    return any(
        _has_non_negated_match(lowered, pattern, chinese=False)
        for pattern in english_patterns
    )


def _requests_broader_evidence_source(text: str) -> bool:
    if _mentions_known_broader_evidence_source(text):
        return True
    chinese_patterns = (
        r"(?:扩大|扩展|补充|增加)[^，。；;!?！？\n]{0,12}"
        r"(?:证据|资料|上下文|来源|范围)",
        r"(?:调用|读取|查看|查询|检索|结合|参考)"
        r"[^，。；;!?！？\n]{0,16}"
        r"(?:人物列表|角色列表|世界设定列表|故事健康度|写作统计|"
        r"故事背景|全局大纲|总纲|章节目录|其他资料|外部资料)",
        r"(?:全局大纲|总纲)",
    )
    if any(
        _has_non_negated_match(text, pattern, chinese=True)
        for pattern in chinese_patterns
    ):
        return True
    lowered = text.casefold()
    english_patterns = (
        r"\b(?:broaden|expand|supplement|add)\b[^.!?\n]{0,24}"
        r"\b(?:evidence|context|sources?|scope)\b",
        r"\b(?:read|check|query|search|consult|use)\b[^.!?\n]{0,24}"
        r"\b(?:character\s+list|setting\s+list|story\s+health|writing\s+stats|"
        r"story\s+background|global\s+outline|chapter\s+catalog|external\s+sources?)\b",
        r"\b(?:global|book-wide)\s+outline\b",
    )
    return any(
        _has_non_negated_match(lowered, pattern, chinese=False)
        for pattern in english_patterns
    )


def _mentions_known_broader_evidence_source(text: str) -> bool:
    if re.search(
        r"(?:人物列表|角色列表|人物设定|角色设定|世界设定列表|"
        r"故事健康度|写作统计|故事背景|全局大纲|总纲|章节目录|"
        r"其他资料|外部资料|"
        r"getStoryHealthDashboard|listBookCharacters|getBookCharacters|"
        r"listSettingEntities|getSettingEntities|getWritingStatsDashboard|"
        r"getStoryBackground|getGlobalOutline|listWritingChapters)",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return bool(re.search(
        r"\b(?:character\s+(?:list|profiles?)|setting\s+list|"
        r"story\s+health|writing\s+stats|story\s+background|"
        r"global\s+outline|chapter\s+catalog|external\s+sources?)\b",
        text.casefold(),
    ))


def _all_present_selected_evidence_is_complete(
    host_facts: Mapping[str, object],
    context: WritingDomainContext,
    *,
    complete_selected_memory: bool,
    complete_associated_chapters: bool,
) -> bool:
    checks: list[bool] = []
    if "selectedMemories" in host_facts:
        checks.append(complete_selected_memory)
    if "associatedChapters" in host_facts:
        checks.append(complete_associated_chapters)
    if "associatedOutlines" in host_facts:
        checks.append(_all_associated_outlines_are_complete(
            host_facts,
            context,
        ))
    return bool(checks) and all(checks)


def _all_associated_outlines_are_complete(
    host_facts: Mapping[str, object],
    context: WritingDomainContext,
) -> bool:
    raw = host_facts.get("associatedOutlines")
    if not isinstance(raw, Mapping):
        return False
    selected_count = _strict_nonnegative_int(raw.get("selectedCount"))
    complete_count = _strict_nonnegative_int(raw.get("completeCount"))
    if selected_count is None or complete_count is None:
        return False
    associated_count = len({
        str(value).strip()
        for value in context.associated_outline_ids
        if str(value).strip()
    })
    items = raw.get("items")
    if (
        selected_count <= 0
        or associated_count != selected_count
        or complete_count != selected_count
        or not isinstance(items, Sequence)
        or isinstance(items, (str, bytes))
        or len(items) != selected_count
    ):
        return False
    return all(
        isinstance(item, Mapping)
        and _strict_nonnegative_int(item.get("ordinal")) == index
        and str(item.get("status") or "") == "complete"
        and str(item.get("readTool") or "") == "queryOutline"
        for index, item in enumerate(items, start=1)
    )


def _request_uses_selected_outlines(
    text: str,
    context: WritingDomainContext,
) -> bool:
    lowered = text.casefold()
    if re.search(
        r"(?:关联|已选|选中|所选|勾选)(?:的)?"
        r"[^。；;!?！？\n]{0,30}大纲",
        text,
    ):
        return True
    if re.search(r"\b(?:associated|selected)\s+outlines?\b", lowered):
        return True
    associated_ids = {
        str(value).strip()
        for value in context.associated_outline_ids
        if str(value).strip()
    }
    for item in context.available_outlines:
        if str(item.get("id") or "").strip() not in associated_ids:
            continue
        title = str(item.get("title") or "").strip()
        if _contains_selected_title_reference(text, title, kind="outline"):
            return True
    return False


def _requests_fresh_outline_read(text: str) -> bool:
    if _explicit_tool_use_requested(text, frozenset({"queryOutline"})):
        return True
    chinese_patterns = (
        r"(?:重新|再次|再|重读|重查)\s*(?:读取|读|查询|加载)?\s*(?:最新的?)?\s*"
        r"(?:关联|已选|选中|所选)?(?:的)?大纲",
        r"(?:刷新|重新加载|同步)\s*(?:并)?\s*(?:读取|查询|加载)?\s*"
        r"(?:最新的?)?\s*(?:关联|已选|选中|所选)?(?:的)?大纲",
        r"(?:读取|查询|加载)\s*(?:当前)?最新(?:的|版本)?\s*"
        r"(?:关联|已选|选中|所选)?(?:的)?大纲",
        r"(?:关联|已选|选中|所选)?(?:的)?大纲.{0,8}"
        r"(?:最新版本|最新内容|最新副本)",
        r"最新(?:的|版本)?\s*(?:关联|已选|选中|所选)?(?:的)?大纲",
    )
    for pattern in chinese_patterns:
        if _has_non_negated_match(text, pattern, chinese=True):
            return True

    lowered = text.casefold()
    english_patterns = (
        r"(?:re-?read|read\s+again|refresh|reload)\b.{0,32}\boutlines?\b",
        r"\b(?:fetch|read|load)\b.{0,12}\b(?:latest|fresh)\b.{0,20}"
        r"\boutlines?\b",
        r"\b(?:latest|fresh)\s+(?:(?:associated|selected)\s+)?outlines?\b",
        r"\boutlines?\b.{0,20}\b(?:latest|fresh)\s+"
        r"(?:version|content|copy)\b",
    )
    return any(
        _has_non_negated_match(lowered, pattern, chinese=False)
        for pattern in english_patterns
    )


def _has_non_negated_match(
    text: str,
    pattern: str,
    *,
    chinese: bool,
) -> bool:
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        prefix = text[max(0, match.start() - 16):match.start()]
        if chinese:
            if re.search(
                r"(?:不要|无需|无须|不用|不必|不能|不应|不得|别|禁止|"
                r"并非|绝非|不是|并不|不)\s*(?:再次|再|重新)?\s*$",
                prefix,
            ):
                continue
        elif re.search(
            r"(?:do\s+not|don't|no\s+need\s+to|without|not)\s*$",
            prefix,
            flags=re.IGNORECASE,
        ):
            continue
        return True
    return False


def _requests_other_outline_scope(
    text: str,
    context: WritingDomainContext,
) -> bool:
    lowered = text.casefold()
    if re.search(
        r"(?:其他|其它|另一个|另外|非关联|未关联|全部|所有|任意)"
        r"(?:的)?大纲|大纲(?:列表|目录)",
        text,
    ):
        return True
    if re.search(
        r"\b(?:another|other|different|all|unselected)\s+outlines?\b"
        r"|\boutline\s+(?:list|catalog)\b",
        lowered,
    ):
        return True

    associated_ids = {
        str(value).strip()
        for value in context.associated_outline_ids
        if str(value).strip()
    }
    for item in context.available_outlines:
        outline_id = str(item.get("id") or "").strip()
        if not outline_id or outline_id in associated_ids:
            continue
        title = str(item.get("title") or "").strip()
        if title and title.casefold() in lowered:
            return True
        if len(outline_id) >= 3 and outline_id.casefold() in lowered:
            return True
    return False

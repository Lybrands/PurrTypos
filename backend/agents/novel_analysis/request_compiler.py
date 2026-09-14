"""Compile persisted source revisions into the canonical replacement scope."""

from __future__ import annotations

from agents.novel_analysis.domain import NovelAnalysisRequestScope
from agents.novel_analysis.recipe import AnalysisSegment
from purra.context_budget import estimate_json_tokens


class NovelAnalysisRequestCompilationError(ValueError):
    code = "novel_analysis_request_compilation_failed"


async def compile_novel_analysis_request_scope(
    db,
    *,
    source_revision_id: str,
    command_id: str,
    segment_token_budget: int,
) -> NovelAnalysisRequestScope:
    revision_id = str(source_revision_id or "").strip()
    revision = await db.fetch_one(
        "SELECT id FROM novel_source_revisions WHERE id = ?",
        [revision_id],
    )
    if revision is None:
        raise NovelAnalysisRequestCompilationError(
            "novel analysis source revision does not exist"
        )
    sections = await db.fetch_all(
        "SELECT id, ordinal, text_content, content_digest "
        "FROM novel_source_sections WHERE revision_id = ? ORDER BY ordinal",
        [revision_id],
    )
    if not sections:
        raise NovelAnalysisRequestCompilationError(
            "novel analysis source revision has no sections"
        )
    segments = []
    for section in sections:
        section_id = str(section["id"])
        content = str(section["text_content"])
        digest = str(section["content_digest"] or "").strip()
        if not digest:
            raise NovelAnalysisRequestCompilationError(
                "novel analysis source section has no content digest"
            )
        for start, end in split_analysis_source_text(
            content,
            segment_token_budget,
        ):
            segments.append(AnalysisSegment(
                id=f"{section_id}:{start}:{end}",
                section_id=section_id,
                section_digest=digest,
                section_ordinal=int(section["ordinal"]),
                start_character=start,
                end_character=end,
            ))
    if not segments:
        raise NovelAnalysisRequestCompilationError(
            "novel analysis source revision contains no analyzable text"
        )
    return NovelAnalysisRequestScope(
        source_revision_id=revision_id,
        command_id=command_id,
        segments=tuple(segments),
    )


def split_analysis_source_text(
    text: str,
    token_budget: int,
) -> tuple[tuple[int, int], ...]:
    content = str(text)
    if not content:
        return ()
    if type(token_budget) is not int or token_budget < 256:
        raise NovelAnalysisRequestCompilationError(
            "novel analysis segment token budget must be at least 256"
        )
    result = []
    start = 0
    while start < len(content):
        if estimate_json_tokens(content[start:]) <= token_budget:
            result.append((start, len(content)))
            break
        low = start + 1
        high = len(content)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_json_tokens(content[start:middle]) <= token_budget:
                low = middle
            else:
                high = middle - 1
        end = low
        minimum = start + max(1, (end - start) // 2)
        for marker in ("\n\n", "\n", "。", "！", "？", ". "):
            boundary = content.rfind(marker, minimum, end)
            if boundary >= minimum:
                end = boundary + len(marker)
                break
        if end <= start:
            raise NovelAnalysisRequestCompilationError(
                "novel analysis segment compiler made no progress"
            )
        result.append((start, end))
        start = end
    return tuple(result)


__all__ = [
    "NovelAnalysisRequestCompilationError",
    "compile_novel_analysis_request_scope",
    "split_analysis_source_text",
]

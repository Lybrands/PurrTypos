"""Single source of truth for replacement analysis read capabilities."""

from __future__ import annotations

from collections.abc import Mapping

from agents.novel_analysis.recipe import AnalysisUnitKind
from agents.novel_analysis.submission_tool import SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT


_READ_TOOLS_BY_UNIT: Mapping[AnalysisUnitKind, tuple[str, ...]] = {
    AnalysisUnitKind.EXTRACT: (
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
    ),
    AnalysisUnitKind.NORMALIZE: (
        "listAnalysisObservations",
        "readAnalysisObservations",
    ),
    AnalysisUnitKind.VALIDATE_EVIDENCE: (),
    AnalysisUnitKind.OVERVIEW: (
        "listAnalysisObservations",
        "readAnalysisObservations",
    ),
    AnalysisUnitKind.DISTILL_TECHNIQUE: (
        "listAnalysisObservations",
        "readAnalysisObservations",
    ),
    AnalysisUnitKind.COVERAGE: (),
    AnalysisUnitKind.REVIEW: (),
}


def analysis_read_tool_names(kind: AnalysisUnitKind | str) -> tuple[str, ...]:
    return _READ_TOOLS_BY_UNIT[AnalysisUnitKind(kind)]


def analysis_model_tool_names(kind: AnalysisUnitKind | str) -> tuple[str, ...]:
    normalized = AnalysisUnitKind(kind)
    if normalized not in _MODEL_UNIT_KINDS:
        return ()
    return (*analysis_read_tool_names(normalized), SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT)


def analysis_unit_read_guidance(kind: AnalysisUnitKind | str) -> str:
    normalized = AnalysisUnitKind(kind)
    tools = analysis_read_tool_names(normalized)
    if not tools:
        return "This host-only unit has no model-readable material tools."
    return (
        f"For {normalized.value}, read bounded material only through: "
        + ", ".join(tools)
        + ". Do not infer omitted material or invent source handles."
    )


def validate_analysis_capabilities(catalog) -> None:
    installed = frozenset(catalog.names)
    required = frozenset(
        tool
        for tools in _READ_TOOLS_BY_UNIT.values()
        for tool in tools
    ) | {SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT}
    missing = required - installed
    if missing:
        raise ValueError(
            "replacement analysis capability is not installed: "
            + ", ".join(sorted(missing))
        )


__all__ = [
    "analysis_model_tool_names",
    "analysis_read_tool_names",
    "analysis_unit_read_guidance",
    "validate_analysis_capabilities",
]


_MODEL_UNIT_KINDS = frozenset({
    AnalysisUnitKind.EXTRACT,
    AnalysisUnitKind.NORMALIZE,
    AnalysisUnitKind.OVERVIEW,
    AnalysisUnitKind.DISTILL_TECHNIQUE,
})

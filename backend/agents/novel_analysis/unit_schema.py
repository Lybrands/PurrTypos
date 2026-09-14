"""Strict v1 output schemas for replacement Novel Analysis model Units."""

from __future__ import annotations

from collections.abc import Mapping

from agents.novel_analysis.recipe import AnalysisUnitKind
from agents.novel_analysis.domain import NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS
from purra.errors import ModelGatewayError
from purra.json_values import canonical_json_digest


class NovelAnalysisModelOutputError(ModelGatewayError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="novel_analysis_model_output_invalid",
            retryable=True,
        )


def validate_model_unit_output(
    kind: AnalysisUnitKind | str,
    payload,
    *,
    allowed_observation_ids: frozenset[str] = frozenset(),
) -> dict[str, object]:
    normalized_kind = AnalysisUnitKind(kind)
    if not isinstance(payload, Mapping):
        raise NovelAnalysisModelOutputError("analysis output must be an object")
    raw = dict(payload)
    if normalized_kind in {AnalysisUnitKind.EXTRACT, AnalysisUnitKind.NORMALIZE}:
        _require_keys(raw, {"facts", "observations"})
        facts = _require_list(raw["facts"], "facts")
        observations = _require_list(raw["observations"], "observations")
        normalized_facts = tuple(_fact(item) for item in facts)
        normalized_observations = tuple(
            _observation(
                item,
                require_merged=normalized_kind is AnalysisUnitKind.NORMALIZE,
                allowed_observation_ids=allowed_observation_ids,
            )
            for item in observations
        )
        _require_unique(
            (item["factId"] for item in normalized_facts),
            "facts",
        )
        _require_unique(
            (item["observationId"] for item in normalized_observations),
            "observations",
        )
        if (
            normalized_kind is AnalysisUnitKind.NORMALIZE
            and allowed_observation_ids
        ):
            merged = {
                item_id
                for item in normalized_observations
                for item_id in item["mergedObservationIds"]
            }
            if merged != set(allowed_observation_ids):
                raise NovelAnalysisModelOutputError(
                    "normalize output must account for every input observation"
                )
        return {
            "schemaVersion": 1,
            "kind": normalized_kind.value,
            "facts": list(normalized_facts),
            "observations": list(normalized_observations),
        }
    if normalized_kind is AnalysisUnitKind.OVERVIEW:
        _require_keys(raw, {"storyOverview"})
        overview = raw["storyOverview"]
        if not isinstance(overview, Mapping):
            raise NovelAnalysisModelOutputError("storyOverview must be an object")
        overview = dict(overview)
        _require_keys(overview, {"summaryMarkdown", "evidenceRefs"})
        return {
            "schemaVersion": 1,
            "kind": normalized_kind.value,
            "storyOverview": {
                "summaryMarkdown": _text(
                    overview["summaryMarkdown"], "summaryMarkdown", 20_000
                ),
                "evidenceRefs": _evidence_refs(overview["evidenceRefs"]),
            },
        }
    if normalized_kind is AnalysisUnitKind.DISTILL_TECHNIQUE:
        _require_keys(raw, {"techniqueResult"})
        return {
            "schemaVersion": 1,
            "kind": normalized_kind.value,
            "techniqueResult": _technique_result(
                raw["techniqueResult"],
                allowed_observation_ids,
            ),
        }
    raise ValueError(f"{normalized_kind.value} is a host-only analysis Unit")


def _fact(value) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NovelAnalysisModelOutputError("fact must be an object")
    raw = dict(value)
    _require_keys(raw, {
        "factKind", "subjectKey", "predicate", "value", "evidenceRefs",
    })
    fact_kind = _text(raw["factKind"], "factKind", 100)
    if fact_kind not in NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS:
        raise NovelAnalysisModelOutputError("factKind is not publishable canon")
    fact = {
        "factKind": fact_kind,
        "subjectKey": _text(raw["subjectKey"], "subjectKey", 500),
        "predicate": _text(raw["predicate"], "predicate", 500),
        "value": raw["value"],
        "evidenceRefs": _evidence_refs(raw["evidenceRefs"]),
    }
    fact["factId"] = "F" + canonical_json_digest(fact).split(":", 1)[-1][:16]
    return fact


def _observation(
    value,
    *,
    require_merged: bool,
    allowed_observation_ids: frozenset[str],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NovelAnalysisModelOutputError("observation must be an object")
    raw = dict(value)
    expected = {"cardKind", "title", "bodyMarkdown", "evidenceRefs"}
    if require_merged:
        expected.add("mergedObservationIds")
    _require_keys(raw, expected)
    merged = ()
    if require_merged:
        merged = tuple(
            _text(item, "mergedObservationId", 500)
            for item in _require_list(
                raw["mergedObservationIds"], "mergedObservationIds"
            )
        )
        if not merged or len(merged) != len(set(merged)):
            raise NovelAnalysisModelOutputError(
                "mergedObservationIds must be non-empty and unique"
            )
        if not set(merged).issubset(allowed_observation_ids):
            raise NovelAnalysisModelOutputError(
                "normalize output references an unknown observation"
            )
    observation = {
        "cardKind": _text(raw["cardKind"], "cardKind", 100),
        "title": _text(raw["title"], "title", 500),
        "bodyMarkdown": _text(raw["bodyMarkdown"], "bodyMarkdown", 8_000),
        "evidenceRefs": _evidence_refs(raw["evidenceRefs"]),
        **({"mergedObservationIds": list(merged)} if require_merged else {}),
    }
    identity = {
        key: value for key, value in observation.items()
        if key != "mergedObservationIds"
    }
    observation["observationId"] = (
        "O" + canonical_json_digest(identity).split(":", 1)[-1][:16]
    )
    return observation


def _technique_result(value, allowed_ids: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NovelAnalysisModelOutputError("techniqueResult must be an object")
    raw = dict(value)
    status = raw.get("status")
    if status == "empty":
        _require_keys(raw, {"status", "reason"})
        return {
            "status": "empty",
            "reason": _text(raw["reason"], "reason", 2_000),
            "techniques": [],
        }
    if status != "generated":
        raise NovelAnalysisModelOutputError("techniqueResult status is invalid")
    _require_keys(raw, {"status", "techniques"})
    techniques = []
    for value in _require_list(raw["techniques"], "techniques"):
        if not isinstance(value, Mapping):
            raise NovelAnalysisModelOutputError("technique must be an object")
        item = dict(value)
        _require_keys(item, {"title", "bodyMarkdown", "observationIds"})
        ids = tuple(
            _text(identifier, "observationId", 500)
            for identifier in _require_list(item["observationIds"], "observationIds")
        )
        if not ids or not set(ids).issubset(allowed_ids):
            raise NovelAnalysisModelOutputError(
                "technique references an unknown observation"
            )
        techniques.append({
            "title": _text(item["title"], "title", 500),
            "bodyMarkdown": _text(item["bodyMarkdown"], "bodyMarkdown", 20_000),
            "observationIds": list(ids),
        })
    if not techniques:
        raise NovelAnalysisModelOutputError(
            "generated techniqueResult requires techniques"
        )
    return {"status": "generated", "techniques": techniques}


def _evidence_refs(value) -> list[dict[str, str]]:
    values = _require_list(value, "evidenceRefs")
    if not values:
        raise NovelAnalysisModelOutputError("evidenceRefs must be non-empty")
    result = []
    for reference in values:
        if not isinstance(reference, Mapping):
            raise NovelAnalysisModelOutputError("evidence ref must be an object")
        raw = dict(reference)
        _require_keys(raw, {"segmentId", "sourceSpanId"})
        result.append({
            "segmentId": _text(raw["segmentId"], "segmentId", 500),
            "sourceSpanId": _text(raw["sourceSpanId"], "sourceSpanId", 100),
        })
    unique = {(item["segmentId"], item["sourceSpanId"]) for item in result}
    if len(unique) != len(result):
        raise NovelAnalysisModelOutputError("evidenceRefs must be unique")
    return result


def _require_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise NovelAnalysisModelOutputError("analysis output shape is invalid")


def _require_list(value, name: str) -> list:
    if not isinstance(value, list):
        raise NovelAnalysisModelOutputError(f"{name} must be a list")
    return value


def _require_unique(values, name: str) -> None:
    values = tuple(values)
    if len(values) != len(set(values)):
        raise NovelAnalysisModelOutputError(f"{name} must be unique")


def _text(value, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise NovelAnalysisModelOutputError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise NovelAnalysisModelOutputError(f"{name} is invalid")
    return normalized


__all__ = [
    "NovelAnalysisModelOutputError",
    "validate_model_unit_output",
]

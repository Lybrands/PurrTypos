"""Canonical Novel Analysis material contract consumed by continuation creation."""

from __future__ import annotations

from collections.abc import Mapping

from agents.novel_analysis.domain import NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS


class CanonicalAnalysisMaterialError(ValueError):
    pass


CANONICAL_CLAIM_NATURES = frozenset({"fact", "summary", "inference"})
CANONICAL_SETTING_FACT_KINDS = frozenset({
    "setting", "location", "faction", "item", "world_rule"
})


def validate_canonical_materials(raw: Mapping[str, object]) -> dict[str, object]:
    required = {"summaryMarkdown", "facts", "craftCards"}
    if not required.issubset(raw):
        raise CanonicalAnalysisMaterialError("canonical material output shape is invalid")
    summary = _text(raw.get("summaryMarkdown"), "summaryMarkdown", 20_000)
    facts = [_fact(item) for item in _list(raw.get("facts"), "facts")]
    cards = [_card(item) for item in _list(raw.get("craftCards"), "craftCards")]
    if not facts:
        raise CanonicalAnalysisMaterialError("canonical facts are empty")
    backgrounds = [item for item in facts if item["factKind"] == "background"]
    if not backgrounds:
        raise CanonicalAnalysisMaterialError("canonical story background is missing")
    if len(backgrounds) != 1:
        raise CanonicalAnalysisMaterialError("canonical story background must be one document")
    character_summaries = [
        item for item in facts if item["factKind"] == "character_summary"
    ]
    _unique((item["subjectKey"] for item in character_summaries), "character material names")
    setting_materials = [
        item for item in facts
        if item["factKind"] in CANONICAL_SETTING_FACT_KINDS
    ]
    _unique((item["subjectKey"] for item in setting_materials), "setting material names")
    _unique((item["id"] for item in facts), "fact ids")
    _unique((item["id"] for item in cards), "craft card ids")
    return {
        "summaryMarkdown": summary,
        "facts": facts,
        "craftCards": cards,
    }


def _fact(value: object) -> dict[str, object]:
    item = _mapping(value, "fact")
    required = {"id", "claimNature", "factKind", "subjectKey", "predicate", "value"}
    if not required.issubset(item):
        raise CanonicalAnalysisMaterialError("canonical fact shape is invalid")
    kind = _text(item.get("factKind"), "factKind", 100)
    if kind not in NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS:
        raise CanonicalAnalysisMaterialError("factKind is not publishable canon")
    raw_nature = _text(item.get("claimNature"), "claimNature", 100)
    # Models often confuse the epistemic claim nature with factKind (for
    # example "unresolved" or "timeline"). Canonicalize that harmlessly;
    # factKind remains the authoritative semantic category.
    nature = raw_nature if raw_nature in CANONICAL_CLAIM_NATURES else "fact"
    fact_value = _material_value(
        kind,
        _without_model_lifecycle(item.get("value")),
        item.get("subjectKey"),
    )
    return {"id": _text(item.get("id"), "fact id", 200), "claimNature": nature,
            "factKind": kind, "subjectKey": _text(item.get("subjectKey"), "subjectKey", 300),
            "predicate": _text(item.get("predicate"), "predicate", 300),
            "value": fact_value, "lifecycleStatus": "active"}


def _without_model_lifecycle(value: object) -> object:
    """Lifecycle belongs to the Host, including when a model nests it in value."""
    if not isinstance(value, Mapping) or "lifecycleStatus" not in value:
        return value
    normalized = dict(value)
    normalized.pop("lifecycleStatus", None)
    return normalized


def _material_value(kind: str, value: object, subject_key: object) -> object:
    """Validate creation-material drafts without inventing an analysis-only form."""
    if kind == "background":
        item = _mapping(value, "story background")
        if "content" not in item:
            raise CanonicalAnalysisMaterialError("story background must use the creation material shape")
        return {"content": _text(item.get("content"), "story background content", 20_000)}
    if kind == "character_summary":
        item = _mapping(value, "character material")
        if not {"name", "tags", "profile_md"}.issubset(item):
            raise CanonicalAnalysisMaterialError("character material must use the creation material shape")
        name = _text(item.get("name"), "character name", 50)
        if name != _text(subject_key, "subjectKey", 300):
            raise CanonicalAnalysisMaterialError("character name must match subjectKey")
        return {
            "name": name,
            "tags": _optional_text(item.get("tags"), "character tags", 500),
            "profile_md": _text(item.get("profile_md"), "character profile", 20_000),
        }
    if kind in CANONICAL_SETTING_FACT_KINDS:
        item = _mapping(value, "setting material")
        if not {"entity_type", "name", "tags", "profile_md"}.issubset(item):
            raise CanonicalAnalysisMaterialError("setting material must use the creation material shape")
        entity_type = _text(item.get("entity_type"), "setting entity type", 20)
        expected_type = kind if kind in {"location", "faction", "item"} else "other"
        if entity_type != expected_type:
            raise CanonicalAnalysisMaterialError("setting entity type does not match factKind")
        name = _text(item.get("name"), "setting name", 50)
        if name != _text(subject_key, "subjectKey", 300):
            raise CanonicalAnalysisMaterialError("setting name must match subjectKey")
        return {
            "entity_type": entity_type,
            "name": name,
            "tags": _optional_text(item.get("tags"), "setting tags", 500),
            "profile_md": _text(item.get("profile_md"), "setting profile", 20_000),
        }
    return value


def _card(value: object) -> dict[str, object]:
    item = _mapping(value, "craft card")
    # Map observations can carry staging provenance that is not part of the
    # canonical craft-card contract.
    required = {"id", "cardKind", "title", "bodyMarkdown"}
    if not required.issubset(item):
        raise CanonicalAnalysisMaterialError("canonical craft card shape is invalid")
    return {"id": _text(item.get("id"), "craft card id", 200),
            "cardKind": _text(item.get("cardKind"), "cardKind", 100),
            "title": _text(item.get("title"), "title", 300),
            "bodyMarkdown": _text(item.get("bodyMarkdown"), "bodyMarkdown", 100_000)}


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise CanonicalAnalysisMaterialError(f"{label} must be an object")
    return dict(value)


def _list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise CanonicalAnalysisMaterialError(f"{label} must be a list")
    return value


def _text(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise CanonicalAnalysisMaterialError(f"{label} is invalid")
    return text


def _optional_text(value: object, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise CanonicalAnalysisMaterialError(f"{label} is invalid")
    return text


def _unique(values, label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise CanonicalAnalysisMaterialError(f"{label} must be unique")


__all__ = [
    "CANONICAL_CLAIM_NATURES",
    "CANONICAL_SETTING_FACT_KINDS",
    "CanonicalAnalysisMaterialError",
    "validate_canonical_materials",
]

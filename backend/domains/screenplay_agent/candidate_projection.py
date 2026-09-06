"""Opaque Run-binding contract for screenplay candidate completion.

Core persists this mapping without interpreting it. The screenplay host uses
it to make candidate finalization part of the Run's terminal transaction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from purra.json_values import FrozenList


SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE = "candidateCompletionProjection"
SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL = (
    "purrtypos.screenplay.candidate-completion.v1"
)
SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL = (
    "purrtypos.screenplay.candidate-validation/v1"
)
_MAX_ID_LENGTH = 256
_MAX_EPISODE_NUMBER = 10_000
_MAX_SCENE_IDS = 512
_REVIEW_DIMENSIONS = frozenset({
    "continuity",
    "character_arc",
    "structure_rhythm",
    "dialogue",
    "format",
})
_DIGEST_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


def parse_candidate_validation_contract(
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and canonicalize the persisted Candidate trust contract."""

    if not isinstance(contract, Mapping):
        raise ValueError("candidate validation contract must be an object")
    value = dict(contract)
    if value.get("protocol") != SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL:
        raise ValueError("candidate validation protocol is unsupported")
    kind = value.get("kind")
    if not isinstance(kind, str):
        raise ValueError("candidate validation kind must be a string")
    expected_keys = {
        "generic": {"protocol", "kind"},
        "scene": {"protocol", "kind", "expectedSceneId"},
        "episode_metadata": {"protocol", "kind", "episodeNumber"},
        "review_dimension": {
            "protocol",
            "kind",
            "episodeNumber",
            "dimension",
            "allowedSceneIds",
            "reviewedDraftId",
            "reviewedContentDigest",
        },
        "document_section": {"protocol", "kind", "sectionKey"},
        "creative_brief_section": {"protocol", "kind", "sectionKey"},
        "source_chapter_digest": {"protocol", "kind", "chapterId"},
        "source_digest_reduction": {"protocol", "kind", "digestId"},
        "source_analysis_section": {"protocol", "kind", "sectionKey"},
        "scene_list_fragment": {"protocol", "kind", "episodeNumber"},
        "structure_episode_plan_index": {"protocol", "kind"},
        "structure_series_arc_index": {"protocol", "kind"},
        "structure_series_arc_phase": {
            "protocol",
            "kind",
            "phaseKey",
            "phaseTitle",
            "phaseObjective",
        },
        "structure_episode_plan_fragment": {
            "protocol",
            "kind",
            "episodeNumber",
            "episodeId",
            "episodeTitle",
        },
        "structure_character_arcs_index": {"protocol", "kind"},
        "structure_character_arc_fragment": {
            "protocol",
            "kind",
            "characterKey",
            "characterName",
        },
    }.get(kind)
    if expected_keys is None:
        raise ValueError("candidate validation kind is unsupported")
    if set(value) != expected_keys:
        raise ValueError("candidate validation contract fields are invalid")

    result: dict[str, Any] = {
        "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
        "kind": kind,
    }
    if kind == "scene":
        result["expectedSceneId"] = _contract_id(
            value["expectedSceneId"],
            "expected scene id",
        )
    elif kind in {"episode_metadata", "scene_list_fragment"}:
        result["episodeNumber"] = _episode_number(value["episodeNumber"])
    elif kind == "structure_episode_plan_fragment":
        result.update({
            "episodeNumber": _episode_number(value["episodeNumber"]),
            "episodeId": _contract_id(value["episodeId"], "structure episode id"),
            "episodeTitle": _contract_id(
                value["episodeTitle"],
                "structure episode title",
            ),
        })
    elif kind == "structure_series_arc_phase":
        result.update({
            "phaseKey": _contract_id(value["phaseKey"], "structure phase key"),
            "phaseTitle": _contract_id(
                value["phaseTitle"],
                "structure phase title",
            ),
            "phaseObjective": _contract_id(
                value["phaseObjective"],
                "structure phase objective",
            ),
        })
    elif kind == "structure_character_arc_fragment":
        result.update({
            "characterKey": _contract_id(
                value["characterKey"],
                "structure character key",
            ),
            "characterName": _contract_id(
                value["characterName"],
                "structure character name",
            ),
        })
    elif kind == "review_dimension":
        result["episodeNumber"] = _episode_number(value["episodeNumber"])
        dimension = value["dimension"]
        if not isinstance(dimension, str) or dimension not in _REVIEW_DIMENSIONS:
            raise ValueError("review dimension is invalid")
        raw_scene_ids = value["allowedSceneIds"]
        if (
            not isinstance(raw_scene_ids, (list, FrozenList))
            or not raw_scene_ids
            or len(raw_scene_ids) > _MAX_SCENE_IDS
        ):
            raise ValueError("review scene ids are invalid")
        scene_ids = [
            _contract_id(item, "review scene id")
            for item in raw_scene_ids
        ]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("review scene ids must be unique")
        digest = value["reviewedContentDigest"]
        if not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None:
            raise ValueError("review content digest is invalid")
        result.update({
            "dimension": dimension,
            "allowedSceneIds": scene_ids,
            "reviewedDraftId": _contract_id(
                value["reviewedDraftId"],
                "reviewed draft id",
            ),
            "reviewedContentDigest": digest.lower(),
        })
    elif kind in {
        "document_section",
        "creative_brief_section",
        "source_analysis_section",
    }:
        result["sectionKey"] = _contract_id(
            value["sectionKey"],
            "document section key",
        )
    elif kind == "source_chapter_digest":
        result["chapterId"] = _contract_id(
            value["chapterId"],
            "source chapter id",
        )
    elif kind == "source_digest_reduction":
        result["digestId"] = _contract_id(
            value["digestId"],
            "source digest id",
        )
    return result


def _contract_id(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_ID_LENGTH:
        raise ValueError(f"{label} is invalid")
    return normalized


def _episode_number(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > _MAX_EPISODE_NUMBER
    ):
        raise ValueError("episode number is invalid")
    return value


def candidate_completion_projection(
    *,
    scope: Mapping[str, Any],
    turn_id: str,
    validation_contract: Mapping[str, Any],
    host_candidate_template: Mapping[str, Any] | None = None,
    text_field: str = "sceneText",
) -> dict[str, Any]:
    normalized_validation = parse_candidate_validation_contract(
        validation_contract
    )
    scope_validation = scope.get("candidateValidation")
    if (
        not isinstance(scope_validation, Mapping)
        or parse_candidate_validation_contract(scope_validation)
        != normalized_validation
    ):
        raise ValueError("candidate projection validation scope conflicts")
    projection: dict[str, Any] = {
        "protocol": SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL,
        "scope": dict(scope),
        "turnId": str(turn_id or "").strip(),
        "validationContract": normalized_validation,
    }
    if not projection["turnId"]:
        raise ValueError("candidate projection turn id is required")
    if normalized_validation["kind"] == "episode_metadata":
        if host_candidate_template is not None:
            raise ValueError("episode metadata requires a JSON candidate")
        projection["hostCapture"] = {"format": "json"}
    elif host_candidate_template is not None:
        normalized_field = str(text_field or "").strip()
        if not normalized_field:
            raise ValueError("host candidate text field is required")
        projection["hostCapture"] = {
            "candidateTemplate": dict(host_candidate_template),
            "textField": normalized_field,
        }
    return {SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE: projection}


def normalize_episode_metadata_payload(raw, episode_number):
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"episodeNumber", "title", "continuitySummary"}
        or isinstance(raw.get("episodeNumber"), bool)
        or not isinstance(raw.get("episodeNumber"), int)
        or raw.get("episodeNumber") != episode_number
        or not isinstance(raw.get("title"), str)
        or not isinstance(raw.get("continuitySummary"), str)
    ):
        raise ValueError("episode metadata number does not match")
    title = raw["title"].strip()
    continuity = raw["continuitySummary"].strip()
    if not title or not continuity:
        raise ValueError("episode metadata title and continuity are required")
    return {"episodeNumber": episode_number, "title": title, "continuitySummary": continuity}


__all__ = [
    "SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE",
    "SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL",
    "SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL",
    "candidate_completion_projection",
    "parse_candidate_validation_contract",
    "normalize_episode_metadata_payload",
]

"""Explicit execution contracts for screenplay AI Parts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from purra.contracts import ExecutionRecipe, ReasoningMode
from purra.json_values import thaw_json_mapping
from purra.long_tasks import LongTaskBudgetLimits


_MAX_INVOCATIONS_PER_PART = 8
_MAX_STRUCTURE_EPISODES = 100
_MAX_STRUCTURE_PHASES = 12
_MAX_STRUCTURE_CHARACTERS = 12
_INPUT_TOKENS_WITH_REASONING = 300_000
_INPUT_TOKENS_WITHOUT_REASONING = 100_000


@dataclass(frozen=True, slots=True)
class ScreenplayPartContract:
    key: str
    validation_kind: str | None
    tool_profile: str
    output_token_cap: int


def _contract(
    key: str,
    *,
    validation_kind: str | None,
    tool_profile: str,
    output_token_cap: int,
) -> ScreenplayPartContract:
    return ScreenplayPartContract(
        key=key,
        validation_kind=validation_kind,
        tool_profile=tool_profile,
        output_token_cap=output_token_cap,
    )


_CONTRACTS = {
    "draft_scene": _contract(
        "draft_scene",
        validation_kind="scene",
        tool_profile="draft_scene",
        output_token_cap=16_384,
    ),
    "episode_metadata": _contract(
        "episode_metadata",
        validation_kind="episode_metadata",
        tool_profile="episode_metadata",
        output_token_cap=4_096,
    ),
    "review_dimension": _contract(
        "review_dimension",
        validation_kind="review_dimension",
        tool_profile="review_dimension",
        output_token_cap=8_192,
    ),
    "structure.episode_plan_index": _contract(
        "structure.episode_plan_index",
        validation_kind="structure_episode_plan_index",
        tool_profile="episode_plan_index",
        output_token_cap=4_096,
    ),
    "structure.episode_plan_fragment": _contract(
        "structure.episode_plan_fragment",
        validation_kind="structure_episode_plan_fragment",
        tool_profile="episode_plan_fragment",
        output_token_cap=16_384,
    ),
    "structure.series_arc_index": _contract(
        "structure.series_arc_index",
        validation_kind="structure_series_arc_index",
        tool_profile="series_arc_index",
        output_token_cap=8_192,
    ),
    "structure.series_arc_phase": _contract(
        "structure.series_arc_phase",
        validation_kind="structure_series_arc_phase",
        tool_profile="series_arc_phase",
        output_token_cap=8_192,
    ),
    "structure.character_arcs_index": _contract(
        "structure.character_arcs_index",
        validation_kind="structure_character_arcs_index",
        tool_profile="character_arcs_index",
        output_token_cap=4_096,
    ),
    "structure.character_arc_fragment": _contract(
        "structure.character_arc_fragment",
        validation_kind="structure_character_arc_fragment",
        tool_profile="character_arc_fragment",
        output_token_cap=8_192,
    ),
    "scene_list_episode": _contract(
        "scene_list_episode",
        validation_kind="scene_list_fragment",
        tool_profile="scene_list_episode",
        output_token_cap=16_384,
    ),
    "final_response": _contract(
        "final_response",
        validation_kind=None,
        tool_profile="final_response",
        output_token_cap=1_024,
    ),
    "source_analysis.chapter_digest": _contract(
        "source_analysis.chapter_digest",
        validation_kind="source_chapter_digest",
        tool_profile="source_chapter_digest",
        output_token_cap=8_192,
    ),
    "source_analysis.digest_reduction": _contract(
        "source_analysis.digest_reduction",
        validation_kind="source_digest_reduction",
        tool_profile="source_digest_reduction",
        output_token_cap=8_192,
    ),
}

for _section in (
    "characters",
    "story",
    "world",
    "themes",
    "adaptation_risks",
):
    _key = f"source_analysis.{_section}"
    _CONTRACTS[_key] = _contract(
        _key,
        validation_kind="source_analysis_section",
        tool_profile="source_analysis_section",
        output_token_cap=16_384,
    )

for _section in (
    "positioning",
    "premise",
    "characters",
    "world",
    "adaptation_rules",
):
    _key = f"creative_brief.{_section}"
    _CONTRACTS[_key] = _contract(
        _key,
        validation_kind="creative_brief_section",
        tool_profile="creative_brief_section",
        output_token_cap=8_192,
    )

PART_CONTRACTS: Mapping[str, ScreenplayPartContract] = MappingProxyType(
    _CONTRACTS
)


def resolve_screenplay_part_contract(
    target_role: str,
    unit_kind: str,
    unit_input: Mapping[str, Any],
    *,
    persisted_key: str | None = None,
) -> ScreenplayPartContract:
    role = str(target_role or "").strip()
    kind = str(unit_kind or "").strip()
    values = dict(unit_input or {})
    key: str | None = None
    if kind == "generate_draft_scene" and role == "screenplayDraft":
        key = "draft_scene"
    elif kind == "generate_episode_metadata" and role == "screenplayDraft":
        key = "episode_metadata"
    elif kind == "generate_review_dimension" and role == "review":
        key = "review_dimension"
    elif kind == "compose_final_response":
        key = "final_response"
    elif kind == "generate_document_section":
        section = str(values.get("sectionKey") or "").strip()
        if role == "sourceAnalysis":
            if values.get("sourceChapterDigest") is True:
                key = "source_analysis.chapter_digest"
            elif values.get("sourceDigestReduction") is True:
                key = "source_analysis.digest_reduction"
            else:
                key = f"source_analysis.{section}"
        elif role == "creativeBrief":
            key = f"creative_brief.{section}"
        elif role == "structure":
            if values.get("episodePlanIndex") is True:
                key = "structure.episode_plan_index"
            elif values.get("seriesArcIndex") is True:
                key = "structure.series_arc_index"
            elif (
                str(values.get("documentSectionKey") or "") == "series_arc"
                and str(values.get("phaseKey") or "").strip()
            ):
                key = "structure.series_arc_phase"
            elif (
                str(values.get("documentSectionKey") or "") == "episode_plan"
                and int(values.get("episodeNumber") or 0) > 0
            ):
                key = "structure.episode_plan_fragment"
            elif values.get("characterArcsIndex") is True:
                key = "structure.character_arcs_index"
            elif (
                str(values.get("documentSectionKey") or "") == "character_arcs"
                and str(values.get("characterKey") or "").strip()
            ):
                key = "structure.character_arc_fragment"
        elif role == "sceneList" and _is_episode_section(section):
            key = "scene_list_episode"
    contract = PART_CONTRACTS.get(str(key or ""))
    if contract is None or (
        persisted_key is not None
        and str(persisted_key or "").strip() != contract.key
    ):
        raise ValueError("screenplay_part_contract_unknown")
    return contract


def screenplay_task_budget_limits(
    recipe: ExecutionRecipe | Mapping[str, Any],
    reasoning_mode: ReasoningMode,
) -> LongTaskBudgetLimits:
    contracts = [
        PART_CONTRACTS[key]
        for key in _recipe_contract_keys(recipe)
    ]
    return LongTaskBudgetLimits(
        max_invocation_attempts=len(contracts) * _MAX_INVOCATIONS_PER_PART,
        max_input_tokens=sum(
            (
                _INPUT_TOKENS_WITHOUT_REASONING
                if reasoning_mode is ReasoningMode.DISABLED
                else _INPUT_TOKENS_WITH_REASONING
            )
            for contract in contracts
        ),
        max_run_output_tokens=sum(
            contract.output_token_cap * _MAX_INVOCATIONS_PER_PART
            for contract in contracts
        ),
        max_reasoning_tokens=max(1, sum(
            contract.output_token_cap * _MAX_INVOCATIONS_PER_PART
            for contract in contracts
            if reasoning_mode is not ReasoningMode.DISABLED
        )),
    )


def screenplay_max_generated_units(
    recipe: ExecutionRecipe | Mapping[str, Any],
) -> int:
    kind, steps = _recipe_kind_and_steps(recipe)
    if kind == "screenplay.structure" and {
        str(step.get("kind") or "") for step in steps
    } >= {
        "expand_structure_series_arc",
        "expand_structure_episode_plan",
        "expand_structure_character_arcs",
    }:
        return (
            len(steps)
            + _MAX_STRUCTURE_PHASES
            + _MAX_STRUCTURE_EPISODES
            + _MAX_STRUCTURE_CHARACTERS
        )
    return len(steps)


def _recipe_contract_keys(
    recipe: ExecutionRecipe | Mapping[str, Any],
) -> tuple[str, ...]:
    _kind, steps = _recipe_kind_and_steps(recipe)
    keys = [
        str((step.get("metadata") or {}).get("partContractKey") or "")
        for step in steps
        if str((step.get("metadata") or {}).get("partContractKey") or "")
    ]
    unknown = [key for key in keys if key not in PART_CONTRACTS]
    if unknown:
        raise ValueError("screenplay_part_contract_unknown")
    if any(
        str(step.get("kind") or "") == "expand_structure_series_arc"
        for step in steps
    ):
        keys.extend(
            ["structure.series_arc_phase"] * _MAX_STRUCTURE_PHASES
        )
    if any(
        str(step.get("kind") or "") == "expand_structure_episode_plan"
        for step in steps
    ):
        keys.extend(
            ["structure.episode_plan_fragment"] * _MAX_STRUCTURE_EPISODES
        )
    if any(
        str(step.get("kind") or "") == "expand_structure_character_arcs"
        for step in steps
    ):
        keys.extend(
            ["structure.character_arc_fragment"]
            * _MAX_STRUCTURE_CHARACTERS
        )
    return tuple(keys)


def _recipe_kind_and_steps(
    recipe: ExecutionRecipe | Mapping[str, Any],
) -> tuple[str, tuple[Mapping[str, Any], ...]]:
    if isinstance(recipe, ExecutionRecipe):
        return recipe.kind, tuple(
            {
                "kind": step.kind,
                "metadata": thaw_json_mapping(step.metadata),
            }
            for step in recipe.steps
        )
    value = dict(recipe or {})
    return str(value.get("kind") or ""), tuple(
        dict(step)
        for step in value.get("steps") or ()
        if isinstance(step, Mapping)
    )


def _is_episode_section(section: str) -> bool:
    prefix = "episode-"
    if not section.startswith(prefix):
        return False
    suffix = section.removeprefix(prefix)
    return suffix.isdigit() and int(suffix) > 0


__all__ = [
    "PART_CONTRACTS",
    "ScreenplayPartContract",
    "resolve_screenplay_part_contract",
    "screenplay_max_generated_units",
    "screenplay_task_budget_limits",
]

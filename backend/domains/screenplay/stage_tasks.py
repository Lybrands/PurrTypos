"""Authoritative stage goals exposed to the screenplay Agent planner."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay.adaptation_brief import SERIES_FORMATS
from domains.screenplay.scene_order import ordered_scene_mappings


def build_screenplay_stage_planning_facts(
    *,
    stage: str,
    source_kind: str,
    screenplay_format: str,
    source_book_bound: bool,
    source_scope_restricted: bool,
    documents: Sequence[Mapping[str, Any]],
    require_deliverable: bool = False,
    draft_scene_count: int = 1,
    draft_scope: str = "planner",
    bound_draft_scene_ids: Sequence[str] = (),
    draft_scenes: Sequence[Mapping[str, Any]] = (),
    completed_scene_ids: Sequence[str] = (),
) -> dict[str, object]:
    """Describe the current outcome without prescribing a tool sequence."""

    facts: dict[str, object] = {
        "screenplayStage": stage,
        "planningMode": "dynamic-within-stage",
        "planningRules": [
            "Plan only the current screenplay stage and do not create a later-stage deliverable.",
            "Choose the smallest useful sequence from the available capabilities; the host owns tool prerequisites and validation.",
            "Treat accepted project documents as immutable inputs and submit changes as a reviewable new version.",
            "Ask the user only when a required creative decision cannot be inferred safely from accepted project material.",
        ],
    }
    task = _stage_task(
        stage=stage,
        source_kind=source_kind,
        screenplay_format=screenplay_format,
        source_book_bound=source_book_bound,
        source_scope_restricted=source_scope_restricted,
        documents=documents,
        draft_scene_count=draft_scene_count,
        draft_scope=draft_scope,
        bound_draft_scene_ids=bound_draft_scene_ids,
        draft_scenes=draft_scenes,
        completed_scene_ids=completed_scene_ids,
    )
    facts.update(task)
    episode_scoped_draft = (
        draft_scope == "next_episode"
        or re.fullmatch(r"next_\d+_episodes", draft_scope) is not None
    )
    if stage == "draft" and episode_scoped_draft:
        planning_rules = facts.get("planningRules")
        if isinstance(planning_rules, list):
            planning_rules.append(
                "For an episode-scoped request, identify the requested episode "
                "range in user-visible plan copy and do not restate scene totals "
                "in the plan title, goal, or step titles. Author independent "
                "episode-creation steps that can execute in parallel, followed "
                "by a continuity review or synthesis step; do not collapse the "
                "AI-visible plan into one generic submission step."
            )
    if require_deliverable and stage != "completed":
        facts["requestedOutcome"] = "complete_current_stage_deliverable"
        completion_capability = _completion_capability(
            stage=stage,
            source_kind=source_kind,
            documents=documents,
        )
        if completion_capability:
            # Consumed only by the screenplay PlanningPolicy. Agent Core treats
            # host planning facts as opaque domain data.
            facts["requestedCompletionCapability"] = completion_capability
    return facts


def _completion_capability(
    *,
    stage: str,
    source_kind: str,
    documents: Sequence[Mapping[str, Any]],
) -> str | None:
    if stage == "orientation":
        return (
            "analyzeSourceMaterial"
            if source_kind == "book"
            else "generateCreativeBrief"
        )
    if stage == "review":
        mode, _ = _review_mode(documents)
        return (
            "reviseCurrentDraft"
            if mode == "revision"
            else "reviewCurrentDraft"
        )
    return {
        "brief": "generateCreativeBrief",
        "structure": "generateScreenplayStructure",
        "scenes": "generateSceneList",
        "draft": "continueScreenplayDraft",
    }.get(stage)


def _stage_task(
    *,
    stage: str,
    source_kind: str,
    screenplay_format: str,
    source_book_bound: bool,
    source_scope_restricted: bool,
    documents: Sequence[Mapping[str, Any]],
    draft_scene_count: int,
    draft_scope: str,
    bound_draft_scene_ids: Sequence[str],
    draft_scenes: Sequence[Mapping[str, Any]],
    completed_scene_ids: Sequence[str],
) -> dict[str, object]:
    if stage == "completed":
        return {
            "objective": "Explain or export the completed screenplay project.",
            "deliverable": "A project summary or export requested by the user.",
            "completionCriteria": [
                "Use only the accepted version chain and delivery manifest.",
                "Do not create another formal screenplay proposal.",
            ],
            "boundaries": ["The creative workflow is complete and read-only."],
        }

    if stage == "orientation" and source_kind == "book":
        coverage = (
            "the project-selected, restricted source range"
            if source_scope_restricted
            else "the complete source work"
        )
        return {
            "objective": f"Establish a reliable factual adaptation base from {coverage}.",
            "deliverable": "A reviewable source-range analysis.",
            "completionCriteria": [
                "Represent the full selected range and state any unread or sampled limitations.",
                "Identify principal characters, events, conflicts, adaptable assets, and continuity risks.",
                "Keep every source-derived conclusion traceable to evidence returned by read-only source capabilities.",
                "Commit analysis entries in bounded batches and finalize only after every declared category count is complete.",
            ],
            "boundaries": [
                "Do not use material outside the selected source range.",
                "Do not create the adaptation creative brief in this stage.",
            ],
            "sourceAvailable": source_book_bound,
        }

    if stage in {"orientation", "brief"}:
        adaptation = source_kind == "book"
        return {
            "objective": (
                "Turn the accepted source analysis into an executable adaptation direction."
                if adaptation
                else "Turn the current premise and user decisions into an executable original screenplay direction."
            ),
            "deliverable": "A reviewable creative brief.",
            "completionCriteria": [
                "Fix the target format, scale, narrative endpoint, central conflict, and character direction.",
                *(
                    [
                        "Express each adaptation choice explicitly and trace source-derived choices to the accepted analysis.",
                        "Carry forward all reading and evidence limitations from the accepted source analysis.",
                    ]
                    if adaptation
                    else ["Separate confirmed choices from creative decisions that still require the user."]
                ),
            ],
            "boundaries": ["Do not create the structure, scene list, or screenplay draft in this stage."],
        }

    if stage == "structure":
        is_series = screenplay_format in SERIES_FORMATS
        unit = "episodes" if is_series else "story beats"
        return {
            "objective": f"Design the complete {unit} from the accepted creative brief.",
            "deliverable": "A reviewable episode outline." if is_series else "A reviewable beat sheet.",
            "completionCriteria": [
                f"Use stable identifiers and a continuous order for all {unit}.",
                "Account for every accepted adaptation decision exactly once, including intentional omissions.",
                "Keep the planned scale consistent with the accepted creative brief.",
                "Commit structure units and decision coverage in bounded batches, then finalize the complete artifact.",
            ],
            "boundaries": ["Do not write the scene list or full screenplay in this stage."],
        }

    if stage == "scenes":
        is_series = screenplay_format in SERIES_FORMATS
        return {
            "objective": "Translate the accepted structure into a complete, executable scene list.",
            "deliverable": "A reviewable scene list.",
            "completionCriteria": [
                "Give every scene a stable identifier, continuous order, goal, conflict, and turn.",
                "Ensure every accepted structure unit is carried by at least one scene.",
                "Commit the scene list in bounded batches (multiple same-tool calls may share a model round) and finalize it only after the declared scene count is complete.",
                *(
                    ["Assign every scene to exactly one episode and keep episode numbering consistent."]
                    if is_series
                    else []
                ),
            ],
            "boundaries": ["Do not write full scene dialogue or screenplay prose in this stage."],
        }

    if stage == "draft":
        remaining_scenes = _next_scenes(
            draft_scenes,
            completed_scene_ids,
            limit=1_000_000,
        )
        next_scenes = (
            _select_bound_draft_scenes(
                remaining_scenes,
                bound_draft_scene_ids,
            )
            if bound_draft_scene_ids
            else list(select_draft_scenes(
                remaining_scenes,
                scope=draft_scope,
                fallback_count=draft_scene_count,
            ))
        )
        if not next_scenes:
            return {
                "objective": "Verify and finalize the complete rolling screenplay draft.",
                "deliverable": "A reviewable final complete draft.",
                "completionCriteria": [
                    "Every accepted scene appears once and the rolling draft is marked complete.",
                    "Preserve the accepted execution history and continuity state.",
                ],
                "boundaries": ["Do not silently rewrite an already accepted scene."],
            }
        return {
            "objective": "Write the next incomplete scene batch while preserving the accepted rolling draft.",
            "deliverable": (
                "A reviewable rolling draft with exactly one newly completed scene."
                if len(next_scenes) == 1
                else (
                    "A reviewable rolling draft with exactly "
                    f"{len(next_scenes)} newly completed scenes."
                )
            ),
            "currentScene": next_scenes[0],
            "currentScenes": next_scenes,
            "draftPosition": _draft_position_facts(
                draft_scenes,
                completed_scene_ids,
                next_scenes,
            ),
            "requestedSceneCount": len(next_scenes),
            "requestedDraftScope": draft_scope,
            "hostBoundSceneIds": list(bound_draft_scene_ids),
            "remainingSceneCount": len(remaining_scenes),
            "draftIntentSchema": {
                "capability": "continueScreenplayDraft",
                "targetFields": {
                    "action": "write_scene_drafts",
                },
                "scopes": [
                    "next_scene",
                    "current_episode_remaining",
                    "next_episodes",
                    "count",
                    "all_remaining",
                    "explicit_scene_ids",
                ],
                "parameters": {
                    "next_episodes": {
                        "count": "positive_episode_count",
                    },
                    "count": {
                        "count": "positive_scene_count",
                    },
                },
            },
            "completionCriteria": [
                "Fulfil the scene goal, advance its conflict, deliver its turn, and establish the outgoing continuity state.",
                "Return only the host-selected scene texts and record unresolved items honestly; the host appends accepted history.",
            ],
            "boundaries": ["Write only the host-selected contiguous scene batch; do not resend or rewrite accepted prior scenes."],
        }

    if stage == "review":
        mode, issue_count = _review_mode(documents)
        if mode == "revision":
            return {
                "objective": "Revise the complete screenplay against the accepted structured review.",
                "deliverable": "A reviewable complete revision.",
                "reviewIssueCount": issue_count,
                "completionCriteria": [
                    "Resolve every accepted review issue with a status and textual evidence.",
                    "Re-evaluate execution records for every affected scene.",
                    "Submit revised affected scenes with their execution records in bounded scene batches.",
                    "Submit issue resolutions separately in bounded resolution batches; preserve unaffected accepted scene text through host assembly.",
                ],
                "boundaries": ["Preserve unaffected accepted content and do not merely claim an issue is fixed."],
            }
        return {
            "objective": (
                "Re-review the current revision against the previous acceptance criteria."
                if mode == "re_review"
                else "Review the complete screenplay for story and screenplay quality."
            ),
            "deliverable": "A reviewable structured screenplay review.",
            "previousReviewIssueCount": issue_count,
            "completionCriteria": [
                "Bind each issue to concrete scenes, evidence, and a reproducible acceptance criterion.",
                "Check continuity, character arcs, structure, pacing, dialogue, format, and scene execution.",
                "Commit the summary, issues, and required rereview verifications in bounded batches before finalizing the report.",
                *(
                    ["Verify every previous issue and keep failed or regressed issues open."]
                    if mode == "re_review"
                    else []
                ),
            ],
            "boundaries": ["Declare the draft ready only when no unresolved or regressed issue remains."],
        }

    return {
        "objective": "Advance the current screenplay stage from accepted project material.",
        "deliverable": "A reviewable proposal for the current stage.",
        "completionCriteria": ["Satisfy the current stage contract."],
        "boundaries": ["Do not create a later-stage deliverable."],
    }


def _accepted_document(
    documents: Sequence[Mapping[str, Any]], kind: str
) -> Mapping[str, Any] | None:
    matches = [
        item
        for item in documents
        if str(item.get("kind") or "") == kind
        and str(item.get("status") or "") == "accepted"
    ]
    return max(matches, key=lambda item: int(item.get("version") or 0), default=None)


def _content(document: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if document is None:
        return {}
    value = document.get("content_json")
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _next_scenes(
    scenes: Sequence[Mapping[str, Any]],
    completed_scene_ids: Sequence[str],
    *,
    limit: int,
) -> list[dict[str, object]]:
    ordered_scenes = ordered_scene_mappings({"scenes": list(scenes)})
    completed = {str(item) for item in completed_scene_ids}
    if not ordered_scenes:
        return []
    pending: list[dict[str, object]] = []
    for scene in ordered_scenes:
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("id") or "").strip()
        if scene_id and scene_id not in completed:
            next_scene: dict[str, object] = {
                "id": scene_id,
                "heading": str(scene.get("heading") or "").strip(),
            }
            if scene.get("episodeNumber") is not None:
                next_scene["episodeNumber"] = scene.get("episodeNumber")
            for field in ("objective", "conflict", "turn", "synopsis"):
                value = str(scene.get(field) or "").strip()
                if value:
                    next_scene[field] = value
            structure_unit_ids = scene.get("structureUnitIds")
            if isinstance(structure_unit_ids, Sequence) and not isinstance(
                structure_unit_ids,
                (str, bytes, bytearray),
            ):
                next_scene["structureUnitIds"] = [
                    str(item) for item in structure_unit_ids
                    if str(item).strip()
                ]
            pending.append(next_scene)
            if len(pending) >= max(1, int(limit)):
                break
    return pending


def _draft_position_facts(
    scenes: Sequence[Mapping[str, Any]],
    completed_scene_ids: Sequence[str],
    selected_scenes: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    ordered_scenes = ordered_scene_mappings({"scenes": list(scenes)})
    completed = {
        str(item) for item in completed_scene_ids if str(item).strip()
    }
    episode_scenes: dict[int, list[str]] = {}
    for scene in ordered_scenes:
        episode = scene.get("episodeNumber")
        scene_id = str(scene.get("id") or "").strip()
        if (
            isinstance(episode, int)
            and not isinstance(episode, bool)
            and episode > 0
            and scene_id
        ):
            episode_scenes.setdefault(episode, []).append(scene_id)
    return {
        "completedSceneCount": len(completed),
        "totalSceneCount": len(ordered_scenes),
        "completedEpisodeNumbers": [
            episode
            for episode, scene_ids in episode_scenes.items()
            if scene_ids and all(scene_id in completed for scene_id in scene_ids)
        ],
        "selectedEpisodeNumbers": list(dict.fromkeys(
            int(scene["episodeNumber"])
            for scene in selected_scenes
            if isinstance(scene.get("episodeNumber"), int)
            and not isinstance(scene.get("episodeNumber"), bool)
        )),
        "selectedSceneIds": [
            str(scene.get("id") or "") for scene in selected_scenes
        ],
    }


def _select_bound_draft_scenes(
    remaining_scenes: Sequence[Mapping[str, Any]],
    bound_scene_ids: Sequence[str],
) -> list[Mapping[str, Any]]:
    requested = [str(item).strip() for item in bound_scene_ids]
    by_id = {
        str(scene.get("id") or "").strip(): scene
        for scene in remaining_scenes
    }
    selected = [by_id.get(scene_id) for scene_id in requested]
    if any(scene is None for scene in selected):
        return []
    canonical = [
        str(scene.get("id") or "").strip()
        for scene in remaining_scenes
        if str(scene.get("id") or "").strip() in set(requested)
    ]
    if canonical != requested:
        return []
    return [scene for scene in selected if scene is not None]


def select_draft_scenes(
    pending: Sequence[Mapping[str, Any]],
    *,
    scope: str,
    fallback_count: int,
    episode_count: int | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Resolve a stable user range against the current accepted checkpoint."""

    available = tuple(pending)
    if not available:
        return ()
    if scope == "all_remaining":
        return available
    if scope == "next_scene":
        return available[:1]
    episode_limit = (
        1
        if scope in {"next_episode", "current_episode_remaining"}
        else None
    )
    stable_episode_scope = re.fullmatch(r"next_(\d+)_episodes", scope)
    if stable_episode_scope is not None:
        episode_limit = int(stable_episode_scope.group(1))
    if scope == "next_episodes":
        try:
            episode_limit = int(episode_count or 0)
        except (TypeError, ValueError):
            episode_limit = 0
        if episode_limit <= 0:
            return ()
    if episode_limit is not None:
        if available[0].get("episodeNumber") is None:
            return available[:max(1, int(fallback_count))]
        selected: list[Mapping[str, Any]] = []
        episode_blocks = 0
        previous_episode: object = object()
        for scene in available:
            episode = scene.get("episodeNumber")
            if not selected or episode != previous_episode:
                episode_blocks += 1
                if episode_blocks > episode_limit:
                    break
                previous_episode = episode
            selected.append(scene)
        return tuple(selected)
    return available[:max(1, int(fallback_count))]


def _review_mode(
    documents: Sequence[Mapping[str, Any]],
) -> tuple[str, int]:
    draft_document = _accepted_document(documents, "scene_draft")
    review_document = _accepted_document(documents, "review")
    draft = _content(draft_document)
    review = _content(review_document)
    draft_id = str(draft_document.get("id") or "") if draft_document else ""
    if review_document and str(review.get("reviewedDraftId") or "") == draft_id:
        return "revision", int(review.get("issueCount") or 0)
    previous_review_id = str(draft.get("reviewId") or "")
    previous_review = next(
        (
            item
            for item in documents
            if str(item.get("kind") or "") == "review"
            and str(item.get("id") or "") == previous_review_id
        ),
        None,
    )
    if previous_review_id and previous_review:
        return "re_review", int(
            _content(previous_review).get("issueCount") or 0
        )
    return "initial_review", 0

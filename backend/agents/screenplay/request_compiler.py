"""Compile product Screenplay commands into frozen replacement recipes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from agents.screenplay.contracts import (
    ScreenplaySourceItemRef,
    ScreenplayStageCommand,
)
from agents.screenplay.recipe import ScreenplayHostRecipeSpec, ScreenplayRecipePart
from domains.screenplay.source_scope import scoped_chapters
from utils.text import extract_text_from_lexical


class ScreenplayRequestCompilationError(ValueError):
    code = "screenplay_request_compilation_failed"


_INPUT_ROLES = {
    "sourceAnalysis": (),
    "creativeBrief": ("sourceAnalysis",),
    "structure": ("creativeBrief",),
    "sceneList": ("structure",),
    "screenplayDraft": ("sceneList",),
    "review": ("sceneList", "screenplayDraft"),
}


async def compile_screenplay_host_recipe(
    db,
    *,
    project_id: str,
    stage_command: ScreenplayStageCommand,
) -> ScreenplayHostRecipeSpec:
    project = str(project_id or "").strip()
    if not project or not isinstance(stage_command, ScreenplayStageCommand):
        raise ScreenplayRequestCompilationError(
            "Screenplay replacement command scope is invalid"
        )
    row = await db.fetch_one(
        "SELECT id, source_kind, source_book_id, source_scope_json FROM screenplay_projects "
        "WHERE id = ? AND status = 'active'",
        [project],
    )
    if row is None:
        raise ScreenplayRequestCompilationError(
            "Screenplay replacement project does not exist or is inactive"
        )
    target_role = stage_command.target_role
    required_roles = _INPUT_ROLES.get(target_role)
    if required_roles is None:
        raise ScreenplayRequestCompilationError(
            f"Screenplay replacement target {target_role} is not admitted yet"
        )
    if target_role == "creativeBrief" and row.get("source_kind") == "original":
        required_roles = ()
    heads = await _head_revisions(db, project)
    revision_scope: dict[str, str] = {}
    for role in required_roles:
        revision_id = heads.get(role)
        if revision_id is None:
            raise ScreenplayRequestCompilationError(
                f"Screenplay replacement requires accepted {role}"
            )
        revision_scope[role] = revision_id
    if stage_command.action.value == "revise" or (
        target_role in {"sceneList", "screenplayDraft"}
        and heads.get(target_role) is not None
    ):
        current = heads.get(target_role)
        if current is None and stage_command.action.value == "revise":
            raise ScreenplayRequestCompilationError(
                "Screenplay replacement revision requires an accepted baseline"
            )
        if current is not None:
            revision_scope[target_role] = current
    source_items = (
        await _source_items(db, row)
        if target_role == "sourceAnalysis"
        else ()
    )
    common = {
        "source_revision_refs": tuple(revision_scope.values()),
        "deliverable_revision_scope": revision_scope,
        "source_book_id": (
            str(row.get("source_book_id") or "").strip() or None
            if source_items else None
        ),
        "source_items": source_items,
    }
    if target_role in {"sceneList", "screenplayDraft"}:
        return await _compile_episode_recipe(
            db,
            stage_command=stage_command,
            revision_scope=revision_scope,
            common=common,
        )
    model_id = f"{target_role}:main"
    model_kind = "review_dimension" if target_role == "review" else "document_section"
    model = ScreenplayRecipePart(
        id=model_id,
        kind=model_kind,
        semantic_key=model_id,
        **common,
    )
    projection = ScreenplayRecipePart(
        id=f"projection:{target_role}",
        kind="host_projection",
        semantic_key=f"projection:{target_role}",
        depends_on=(model.id,),
        **common,
    )
    validation = ScreenplayRecipePart(
        id=f"validation:{target_role}",
        kind="validation",
        semantic_key=f"validation:{target_role}",
        depends_on=(projection.id,),
        **common,
    )
    final = ScreenplayRecipePart(
        id=f"final:{target_role}",
        kind="final_response",
        semantic_key=f"final:{target_role}",
        depends_on=(validation.id,),
        **common,
    )
    return ScreenplayHostRecipeSpec(
        operation=stage_command.action.value,
        target_role=target_role,
        max_parallelism=1,
        parts=(model, projection, validation, final),
    )


async def _compile_episode_recipe(
    db,
    *,
    stage_command: ScreenplayStageCommand,
    revision_scope: Mapping[str, str],
    common: Mapping[str, object],
) -> ScreenplayHostRecipeSpec:
    target_role = stage_command.target_role
    input_role = "structure" if target_role == "sceneList" else "sceneList"
    available = await _revision_episodes(
        db,
        revision_scope[input_role],
        single_document_fallback=input_role == "structure",
    )
    current_id = revision_scope.get(target_role)
    current = await _revision_episodes(db, current_id) if current_id else {}
    selected = _select_episode_numbers(
        stage_command,
        available=tuple(sorted(available)),
        existing=tuple(sorted(current)),
    )
    model_parts: list[ScreenplayRecipePart] = []
    if target_role == "sceneList":
        for number in selected:
            part_id = f"scene-list:episode:{number}"
            model_parts.append(ScreenplayRecipePart(
                id=part_id,
                kind="document_section",
                semantic_key=part_id,
                episode_number=number,
                **common,
            ))
    else:
        occupied_scene_ids: set[str] = set()
        for number in selected:
            scenes = available[number].get("scenes")
            if not isinstance(scenes, list) or not scenes:
                raise ScreenplayRequestCompilationError(
                    f"Screenplay sceneList episode {number} has no scenes"
                )
            scene_part_ids = []
            for raw_scene in scenes:
                if not isinstance(raw_scene, Mapping):
                    raise ScreenplayRequestCompilationError(
                        f"Screenplay sceneList episode {number} is invalid"
                    )
                scene_id = str(raw_scene.get("id") or "").strip()
                if not scene_id or scene_id in occupied_scene_ids:
                    raise ScreenplayRequestCompilationError(
                        f"Screenplay sceneList episode {number} scene ids are invalid"
                    )
                occupied_scene_ids.add(scene_id)
                scene_part_ids.append(scene_id)
                model_parts.append(ScreenplayRecipePart(
                    id=f"draft-scene:{scene_id}",
                    kind="draft_scene",
                    semantic_key=scene_id,
                    episode_number=number,
                    scene_id=scene_id,
                    **common,
                ))
            metadata_id = f"episode-metadata:{number}"
            model_parts.append(ScreenplayRecipePart(
                id=metadata_id,
                kind="episode_metadata",
                semantic_key=metadata_id,
                depends_on=tuple(f"draft-scene:{value}" for value in scene_part_ids),
                episode_number=number,
                **common,
            ))
    if not model_parts:
        raise ScreenplayRequestCompilationError(
            "Screenplay replacement episode scope contains no work"
        )
    projection = ScreenplayRecipePart(
        id=f"projection:{target_role}",
        kind="host_projection",
        semantic_key=f"projection:{target_role}",
        depends_on=tuple(part.id for part in model_parts),
        **common,
    )
    validation = ScreenplayRecipePart(
        id=f"validation:{target_role}",
        kind="validation",
        semantic_key=f"validation:{target_role}",
        depends_on=(projection.id,),
        **common,
    )
    final = ScreenplayRecipePart(
        id=f"final:{target_role}",
        kind="final_response",
        semantic_key=f"final:{target_role}",
        depends_on=(validation.id,),
        **common,
    )
    return ScreenplayHostRecipeSpec(
        operation=stage_command.action.value,
        target_role=target_role,
        # Every Part records events and receipts in the same owning Run. Keep
        # those SQLite-backed Operation effects serialized until the shared
        # Run journal exposes a cross-Operation write scheduler.
        max_parallelism=1,
        parts=(*model_parts, projection, validation, final),
    )


def _select_episode_numbers(stage_command, *, available, existing):
    scope = stage_command.scope
    available_set = set(available)
    remaining = tuple(value for value in available if value not in set(existing))
    if scope.kind.value == "episodes":
        selected = scope.episode_numbers
        if not set(selected).issubset(available_set):
            raise ScreenplayRequestCompilationError(
                "Screenplay requested episode is outside the accepted upstream revision"
            )
    elif scope.kind.value == "next_episodes":
        selected = remaining[: scope.count]
    elif scope.kind.value == "all_remaining":
        selected = remaining
    else:
        selected = available
    if not selected:
        raise ScreenplayRequestCompilationError(
            "Screenplay replacement episode scope contains no work"
        )
    return tuple(selected)


async def _revision_episodes(
    db,
    revision_id: str,
    *,
    single_document_fallback: bool = False,
) -> dict[int, dict[str, object]]:
    rows = await db.fetch_all(
        "SELECT part_type, part_key, payload_json FROM screenplay_revision_parts "
        "WHERE revision_id = ? ORDER BY position",
        [revision_id],
    )
    result: dict[int, dict[str, object]] = {}
    document: Mapping[str, object] = {}
    for row in rows:
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, Mapping):
            continue
        if row.get("part_type") == "episode":
            number = _positive_episode(payload.get("episodeNumber") or row.get("part_key"))
            if number is not None:
                result[number] = dict(payload)
        elif row.get("part_type") == "document" and row.get("part_key") == "main":
            document = payload
    if result:
        return result
    for candidate in _document_payload_candidates(document):
        raw_episodes = candidate.get("episodes") or candidate.get("episodeDrafts")
        if isinstance(raw_episodes, list):
            for raw in raw_episodes:
                if not isinstance(raw, Mapping):
                    continue
                number = _positive_episode(
                    raw.get("episodeNumber") or raw.get("number")
                )
                if number is not None:
                    result[number] = dict(raw)
        if result:
            break
        if isinstance(candidate.get("scenes"), list):
            for raw in candidate["scenes"]:
                if not isinstance(raw, Mapping):
                    continue
                number = _positive_episode(raw.get("episodeNumber")) or 1
                episode = result.setdefault(
                    number,
                    {"episodeNumber": number, "scenes": []},
                )
                episode["scenes"].append(dict(raw))
        if result:
            break
    if not result and single_document_fallback and document:
        result[1] = {"episodeNumber": 1}
    if not result:
        raise ScreenplayRequestCompilationError(
            "Screenplay accepted upstream revision has no episode-shaped content"
        )
    return result


def _document_payload_candidates(
    document: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    candidates: list[Mapping[str, object]] = [document]
    parts = document.get("parts")
    if not isinstance(parts, list):
        return tuple(candidates)
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        payload = part.get("payload")
        if not isinstance(payload, Mapping):
            continue
        candidates.append(payload)
        content = payload.get("content")
        if isinstance(content, Mapping):
            candidates.append(content)
    return tuple(candidates)


def _positive_episode(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def _head_revisions(db, project_id: str) -> dict[str, str]:
    rows = await db.fetch_all(
        "SELECT d.role, h.revision_id FROM screenplay_project_heads AS h "
        "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
        "WHERE h.project_id = ? ORDER BY d.role",
        [project_id],
    )
    return {str(row["role"]): str(row["revision_id"]) for row in rows}


async def _source_items(db, project) -> tuple[ScreenplaySourceItemRef, ...]:
    book_id = str(project.get("source_book_id") or "").strip()
    if not book_id:
        return ()
    chapters = await scoped_chapters(db, project)
    result = []
    for chapter in chapters:
        chapter_id = str(chapter.get("id") or "").strip()
        article = await db.fetch_one(
            "SELECT content FROM articles WHERE chapter_id = ?",
            [chapter_id],
        )
        content = extract_text_from_lexical(str((article or {}).get("content") or ""))
        if not chapter_id or not content.strip():
            continue
        result.append(ScreenplaySourceItemRef(
            source_type="chapter",
            source_id=chapter_id,
            content_digest=(
                "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
            ),
        ))
    if not result:
        raise ScreenplayRequestCompilationError(
            "Screenplay replacement source scope has no readable chapters"
        )
    return tuple(result)


__all__ = [
    "ScreenplayRequestCompilationError",
    "compile_screenplay_host_recipe",
]

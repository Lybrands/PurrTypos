"""Project-scoped read and proposal tools for the screenplay Agent."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from agent_core.contracts import (
    AgentRunRequest,
    DomainEffect,
    ExecutionState,
    ToolEffectState,
    ToolHandlerResult,
    ToolStepDisposition,
)
from agent_core.artifacts.errors import ArtifactError
from agent_core.ports import CancellationSignal, ToolRegistration
from agent_core.tools import InMemoryToolCatalog
from database.crud.screenplay_source_receipts import record_source_receipts
from database.crud.screenplay_drafts import (
    latest_accepted_draft_manifest,
    list_episode_rows,
)
from database.crud.screenplay_episode_documents import (
    assemble_episode_document,
    get_episode as get_structured_episode,
    list_episode_rows as list_structured_episode_rows,
)
from database.crud.screenplay_head_projection import (
    get_current_document,
    get_document as get_project_document,
    list_current_documents,
)
from domains.screenplay.source_scope import (
    is_restricted_source_scope,
    parse_source_scope,
    scoped_chapters,
    scoped_outline_ids,
    source_scope_summary,
)
from domains.screenplay.scene_trace import normalize_scene_trace
from domains.screenplay.stage_tasks import select_draft_scenes
from domains.screenplay.scene_order import ordered_scene_mappings
from domains.screenplay.scene_execution import (
    normalize_scene_execution,
    validate_scene_execution_history,
)
from domains.screenplay.draft_episodes import build_episode_draft_batches
from domains.screenplay.proposal_rendering import render_screenplay_proposal
from domains.screenplay.source_coverage import (
    build_source_coverage_plan,
    read_source_coverage_batch,
)
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)
from domains.screenplay.payload_limits import SCENE_DRAFT_PAYLOAD_LIMITS
from domains.screenplay.tool_contracts import (
    SCREENPLAY_PLANNING_CAPABILITY_SCHEMA_BY_NAME,
    SCREENPLAY_PRIVATE_TOOL_TO_PLANNING_CAPABILITY,
    SCREENPLAY_TOOL_CONTEXT_CONTRACTS,
    SCREENPLAY_TOOL_DATA_CONTRACTS,
    SCREENPLAY_TOOL_POLICIES,
    SCREENPLAY_TOOL_SCHEMAS,
)
from infrastructure.screenplay.artifact_tools import (
    ScreenplayArtifactInputError,
    ScreenplayArtifactToolService,
)
from utils.text import extract_text_from_lexical


logger = logging.getLogger(__name__)


_HOST_PLANNED_FINALIZE_TOOLS = frozenset({
    "finalizeSourceAnalysisProposal",
    "finalizeCreativeBriefProposal",
    "finalizeScreenplayStructureProposal",
    "finalizeSceneListProposal",
    "finalizeScreenplayReviewProposal",
    "finalizeScreenplayRevisionProposal",
})


_SOURCE_TOOL_NAMES = frozenset({
    "getSourceBookOverview",
    "getSourceCoveragePlan",
    "readSourceCoverageBatch",
    "searchSourceMaterial",
    "readSourcePassages",
    "getSourceCharacters",
    "getSourceWorldSettings",
})
_VALID_SOURCE_TYPES = frozenset({
    "chapter",
    "outline",
    "character",
    "setting",
    "background",
})
_PROPOSAL_STAGES = {
    "beginSourceAnalysisArtifact": frozenset({"orientation", "brief"}),
    "appendSourceAnalysisBatch": frozenset({"orientation", "brief"}),
    "finalizeSourceAnalysisProposal": frozenset({"orientation", "brief"}),
    "beginCreativeBriefArtifact": frozenset({"orientation", "brief"}),
    "appendCreativeBriefBatch": frozenset({"orientation", "brief"}),
    "finalizeCreativeBriefProposal": frozenset({"orientation", "brief"}),
    "beginScreenplayStructureArtifact": frozenset({"structure"}),
    "appendScreenplayStructureBatch": frozenset({"structure"}),
    "finalizeScreenplayStructureProposal": frozenset({"structure"}),
    "beginSceneListArtifact": frozenset({"scenes"}),
    "appendSceneListBatch": frozenset({"scenes"}),
    "finalizeSceneListProposal": frozenset({"scenes"}),
    "proposeSceneDraft": frozenset({"draft"}),
    "beginScreenplayReviewArtifact": frozenset({"review"}),
    "appendScreenplayReviewBatch": frozenset({"review"}),
    "finalizeScreenplayReviewProposal": frozenset({"review"}),
    "beginScreenplayRevisionArtifact": frozenset({"review"}),
    "appendScreenplayRevisionBatch": frozenset({"review"}),
    "appendScreenplayRevisionResolutionBatch": frozenset({"review"}),
    "finalizeScreenplayRevisionProposal": frozenset({"review"}),
}
_ARTIFACT_TOOL_NAMES = frozenset({
    "beginSourceAnalysisArtifact",
    "appendSourceAnalysisBatch",
    "finalizeSourceAnalysisProposal",
    "beginCreativeBriefArtifact",
    "appendCreativeBriefBatch",
    "finalizeCreativeBriefProposal",
    "beginScreenplayStructureArtifact",
    "appendScreenplayStructureBatch",
    "finalizeScreenplayStructureProposal",
    "beginSceneListArtifact",
    "appendSceneListBatch",
    "finalizeSceneListProposal",
    "beginScreenplayReviewArtifact",
    "appendScreenplayReviewBatch",
    "finalizeScreenplayReviewProposal",
    "beginScreenplayRevisionArtifact",
    "appendScreenplayRevisionBatch",
    "appendScreenplayRevisionResolutionBatch",
    "finalizeScreenplayRevisionProposal",
})
_ARTIFACT_APPEND_TOOL_NAMES = frozenset({
    name for name in _ARTIFACT_TOOL_NAMES if name.startswith("append")
})
_STATEFUL_TOOL_NAMES = frozenset({
    "getScreenplayDraftContext",
    "proposeSceneDraft",
})
_SERIES_FORMATS = frozenset({"连续剧", "竖屏短剧"})
_GLOBAL_SOURCE_TOOL_NAMES = frozenset({
    "getSourceCharacters",
    "getSourceWorldSettings",
})


def build_screenplay_tool_catalog(db) -> InMemoryToolCatalog:
    artifact_tools = ScreenplayArtifactToolService(db)
    handlers = {
        "getScreenplayProject": _get_screenplay_project,
        "getScreenplayDocument": _get_screenplay_document,
        "getScreenplayEpisodeContext": _get_screenplay_episode_context,
        "getScreenplayDraftContext": _get_screenplay_draft_context,
        "getSourceBookOverview": _get_source_book_overview,
        "getSourceCoveragePlan": _get_source_coverage_plan,
        "readSourceCoverageBatch": _read_source_coverage_batch,
        "searchSourceMaterial": _search_source_material,
        "readSourcePassages": _read_source_passages,
        "getSourceCharacters": _get_source_characters,
        "getSourceWorldSettings": _get_source_world_settings,
        "beginSourceAnalysisArtifact": artifact_tools.begin_source_analysis,
        "appendSourceAnalysisBatch": artifact_tools.append_source_analysis,
        "finalizeSourceAnalysisProposal": (
            artifact_tools.finalize_source_analysis
        ),
        "beginCreativeBriefArtifact": artifact_tools.begin_creative_brief,
        "appendCreativeBriefBatch": artifact_tools.append_creative_brief,
        "finalizeCreativeBriefProposal": (
            artifact_tools.finalize_creative_brief
        ),
        "beginScreenplayStructureArtifact": artifact_tools.begin_structure,
        "appendScreenplayStructureBatch": artifact_tools.append_structure,
        "finalizeScreenplayStructureProposal": (
            artifact_tools.finalize_structure
        ),
        "beginSceneListArtifact": artifact_tools.begin_scene_list,
        "appendSceneListBatch": artifact_tools.append_scene_list,
        "finalizeSceneListProposal": artifact_tools.finalize_scene_list,
        "proposeSceneDraft": _propose_scene_draft,
        "beginScreenplayReviewArtifact": artifact_tools.begin_review,
        "appendScreenplayReviewBatch": artifact_tools.append_review,
        "finalizeScreenplayReviewProposal": artifact_tools.finalize_review,
        "beginScreenplayRevisionArtifact": artifact_tools.begin_revision,
        "appendScreenplayRevisionBatch": artifact_tools.append_revision,
        "appendScreenplayRevisionResolutionBatch": (
            artifact_tools.append_revision_resolutions
        ),
        "finalizeScreenplayRevisionProposal": artifact_tools.finalize_revision,
    }
    registrations = tuple(
        ToolRegistration(
            schema=schema,
            handler=_bind_handler(db, schema.name, handlers[schema.name]),
            policy=SCREENPLAY_TOOL_POLICIES[schema.name],
            scope_validator=_scope_validator(db, schema.name),
            context_contract=SCREENPLAY_TOOL_CONTEXT_CONTRACTS[schema.name],
            data_contract=SCREENPLAY_TOOL_DATA_CONTRACTS[schema.name],
            cancellation_linearizable=(schema.name in _ARTIFACT_TOOL_NAMES),
            host_managed_durability=(schema.name in _ARTIFACT_TOOL_NAMES),
            host_planned_arguments=(
                {}
                if schema.name in _HOST_PLANNED_FINALIZE_TOOLS
                else None
            ),
            planning_capability=(
                SCREENPLAY_PLANNING_CAPABILITY_SCHEMA_BY_NAME[
                    SCREENPLAY_PRIVATE_TOOL_TO_PLANNING_CAPABILITY[schema.name]
                ]
                if schema.name
                in SCREENPLAY_PRIVATE_TOOL_TO_PLANNING_CAPABILITY
                else None
            ),
        )
        for schema in SCREENPLAY_TOOL_SCHEMAS
    )
    return InMemoryToolCatalog(registrations, _enabled_tools)


def _enabled_tools(request: AgentRunRequest) -> frozenset[str]:
    if (
        request.domain_context.namespace != SCREENPLAY_DOMAIN_NAMESPACE
        or not request.tools_enabled
    ):
        return frozenset()
    context = ScreenplayDomainContext.from_core_context(
        request.domain_context
    )
    project_tools = frozenset({
        "getScreenplayProject",
        "getScreenplayDocument",
    })
    enabled = set(project_tools)
    if context.requested_stage in {"scenes", "draft", "review", "completed"}:
        enabled.add("getScreenplayEpisodeContext")
    if context.requested_stage in {"draft", "review", "completed"}:
        enabled.add("getScreenplayDraftContext")
    if context.requested_source_book_id:
        enabled.update(_SOURCE_TOOL_NAMES)
        if context.source_scope_restricted:
            # Do not advertise capabilities that the authoritative execution
            # scope must reject. The scope validator remains defense in depth.
            enabled.difference_update(_GLOBAL_SOURCE_TOOL_NAMES)
    if (
        context.requested_stage == "orientation"
        and context.requested_source_book_id
    ):
        enabled.update({
            "beginSourceAnalysisArtifact",
            "appendSourceAnalysisBatch",
            "finalizeSourceAnalysisProposal",
        })
    elif context.requested_stage == "brief":
        enabled.update({
            "beginCreativeBriefArtifact",
            "appendCreativeBriefBatch",
            "finalizeCreativeBriefProposal",
        })
        if context.requested_source_book_id:
            enabled.update({
                "beginSourceAnalysisArtifact",
                "appendSourceAnalysisBatch",
                "finalizeSourceAnalysisProposal",
            })
    elif context.requested_stage == "orientation":
        enabled.update({
            "beginCreativeBriefArtifact",
            "appendCreativeBriefBatch",
            "finalizeCreativeBriefProposal",
        })
    elif context.requested_stage == "structure":
        enabled.update({
            "beginScreenplayStructureArtifact",
            "appendScreenplayStructureBatch",
            "finalizeScreenplayStructureProposal",
        })
    elif context.requested_stage == "scenes":
        enabled.update({
            "beginSceneListArtifact",
            "appendSceneListBatch",
            "finalizeSceneListProposal",
        })
    elif context.requested_stage == "draft":
        enabled.add("proposeSceneDraft")
    elif context.requested_stage == "review":
        enabled.update({
            "beginScreenplayReviewArtifact",
            "appendScreenplayReviewBatch",
            "finalizeScreenplayReviewProposal",
            "beginScreenplayRevisionArtifact",
            "appendScreenplayRevisionBatch",
            "appendScreenplayRevisionResolutionBatch",
            "finalizeScreenplayRevisionProposal",
        })
    return frozenset(enabled)


def _bind_handler(db, tool_name: str, operation):
    async def _handler(
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        if signal is not None and signal.is_set():
            return _error("工具执行已取消")
        try:
            project = await _load_project_scope(db, state)
            operation_result = (
                await operation(project, dict(arguments), state)
                if tool_name in _ARTIFACT_TOOL_NAMES
                else await operation(db, project, dict(arguments), state)
                if tool_name in _STATEFUL_TOOL_NAMES
                else await operation(db, project, dict(arguments))
            )
            payload, refs = operation_result[:2]
            effects = (
                tuple(operation_result[2])
                if len(operation_result) > 2
                else ()
            )
            if refs and state.run_id:
                await record_source_receipts(
                    db,
                    project_id=str(project["id"]),
                    agent_run_id=str(state.run_id),
                    tool_name=tool_name,
                    refs=refs,
                )
            return ToolHandlerResult(
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                effects=effects,
                step_disposition=(
                    ToolStepDisposition.CONTINUE
                    if tool_name in _ARTIFACT_APPEND_TOOL_NAMES
                    and str(payload.get("nextAction") or "") == "append_batch"
                    else ToolStepDisposition.COMPLETE
                ),
            )
        except (
            _ToolInputError,
            ScreenplayArtifactInputError,
        ) as error:
            return _error(
                str(error),
                error_code="tool_input_invalid",
                effect_state=ToolEffectState.NOT_STARTED,
            )
        except ArtifactError as error:
            return _error(str(error), error_code="tool_input_invalid")
        except Exception:
            logger.exception("Screenplay tool %s failed internally", tool_name)
            return _error(
                "The screenplay tool failed internally.",
                error_code="tool_internal_error",
            )

    return _handler


def _scope_validator(db, tool_name: str):
    async def _validate(
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> str | None:
        del signal
        if any(
            key in arguments
            for key in ("bookId", "sourceBookId", "projectId")
        ):
            return (
                "Book and project scope are host-bound and cannot be supplied "
                "by the model."
            )
        try:
            project = await _load_project_scope(db, state)
        except _ToolInputError as error:
            return str(error)
        if tool_name in _SOURCE_TOOL_NAMES and not project.get("source_book_id"):
            return "The current screenplay project has no source book."
        if (
            tool_name in _GLOBAL_SOURCE_TOOL_NAMES
            and is_restricted_source_scope(project)
        ):
            return (
                f"{tool_name} is unavailable because character dossiers, world "
                "settings, and background are book-global and cannot be proven "
                "to belong to the project's restricted adaptation range."
            )
        if tool_name in _PROPOSAL_STAGES:
            if project.get("status") != "active":
                return "Archived screenplay projects cannot receive proposals."
            if (
                tool_name in {
                    "beginSourceAnalysisArtifact",
                    "appendSourceAnalysisBatch",
                    "finalizeSourceAnalysisProposal",
                }
                and not project.get("source_book_id")
            ):
                return (
                    f"{tool_name} requires an available source book."
                )
            stage = str(project.get("active_stage") or "")
            if stage not in _PROPOSAL_STAGES[tool_name]:
                return (
                    f"{tool_name} is unavailable during screenplay stage "
                    f"{stage or 'unknown'}."
                )
        return None

    return _validate


async def _load_project_scope(db, state: ExecutionState) -> dict[str, Any]:
    project_id = str(
        state.domain.get("screenplayProjectId") or ""
    ).strip()
    if not project_id:
        raise _ToolInputError("Missing host-bound screenplay project.")
    project = await db.fetch_one(
        "SELECT * FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    if project is None:
        raise _ToolInputError("The bound screenplay project no longer exists.")
    source_book_id = str(project.get("source_book_id") or "").strip() or None
    if source_book_id:
        book = await db.fetch_one(
            "SELECT id FROM books WHERE id = ?",
            [source_book_id],
        )
        if book is None:
            source_book_id = None
    project["source_book_id"] = source_book_id
    project["source_scope"] = parse_source_scope(
        project.get("source_scope_json")
    )
    return project


async def _get_screenplay_project(db, project, arguments):
    del arguments
    documents = await list_current_documents(db, str(project["id"]))
    return {
        "project": {
            "id": project["id"],
            "title": project["title"],
            "sourceKind": project["source_kind"],
            "sourceBookId": project.get("source_book_id"),
            "sourceScope": source_scope_summary(
                project.get("source_scope")
            ),
            "format": project["format"],
            "approach": project["approach"],
            "premise": project["premise"],
            "activeStage": project["active_stage"],
            "status": project["status"],
        },
        "documents": [
            {
                key: row.get(key)
                for key in (
                    "id",
                    "kind",
                    "title",
                    "version",
                    "status",
                    "create_time",
                    "update_time",
                )
            } | {
                "derived_from_ids": _json_value(
                    row.get("derived_from_ids"),
                    [],
                ),
            }
            for row in documents
        ],
    }, []


async def _get_screenplay_document(db, project, arguments):
    document_id = str(arguments.get("documentId") or "").strip()
    if not document_id:
        raise _ToolInputError("documentId is required.")
    row = await get_project_document(
        db,
        str(project["id"]),
        document_id,
    )
    if row is None:
        raise _ToolInputError(
            "The requested document is outside the current screenplay project."
        )
    row["content_json"] = _json_value(row.get("content_json"), {})
    row["derived_from_ids"] = _json_value(
        row.get("derived_from_ids"),
        [],
    )
    return {"document": row}, []


async def _get_screenplay_episode_context(db, project, arguments):
    document_id = str(arguments.get("documentId") or "").strip()
    if not document_id:
        raise _ToolInputError("documentId is required.")
    document = await get_project_document(
        db,
        str(project["id"]),
        document_id,
    )
    if document is None:
        raise _ToolInputError(
            "The requested document is outside the current screenplay project."
        )
    index = await list_structured_episode_rows(
        db,
        document_id=document_id,
        include_content=False,
    )
    if not index:
        raise _ToolInputError(
            "The requested document has no episode-native payloads."
        )
    raw_numbers = arguments.get("episodeNumbers")
    if raw_numbers is None:
        requested = [int(index[0]["episode_number"])]
    elif isinstance(raw_numbers, list):
        requested = list(dict.fromkeys(
            int(item)
            for item in raw_numbers
            if isinstance(item, int)
            and not isinstance(item, bool)
            and item > 0
        ))[:8]
    else:
        raise _ToolInputError("episodeNumbers must be an array.")
    available = {int(item["episode_number"]) for item in index}
    unknown = set(requested) - available
    if unknown:
        raise _ToolInputError(
            "Requested episodes are outside this document: "
            + ", ".join(str(item) for item in sorted(unknown))
        )
    selected = []
    for number in requested:
        row = await get_structured_episode(
            db,
            document_id=document_id,
            episode_number=number,
        )
        if row is not None:
            selected.append(row)
    return {
        "document": {
            "id": str(document["id"]),
            "kind": str(document["kind"]),
            "title": str(document["title"]),
            "version": int(document["version"]),
            "status": str(document["status"]),
            "manifest": _json_value(document.get("content_json"), {}),
        },
        "episodeIndex": index,
        "selectedEpisodes": selected,
    }, []


async def _get_screenplay_draft_context(db, project, arguments, state):
    scene_list = await get_current_document(
        db,
        str(project["id"]),
        kind="scene_list",
    )
    if scene_list is None:
        raise _ToolInputError(
            "An accepted scene list is required before reading draft context."
        )
    scene_episode_rows = await list_structured_episode_rows(
        db,
        document_id=str(scene_list.get("id") or ""),
        include_content=True,
    )
    if scene_episode_rows:
        scene_list_json = {
            "scenes": [
                dict(scene)
                for episode in scene_episode_rows
                for scene in episode.get("content_json", {}).get("scenes", [])
                if isinstance(scene, Mapping)
            ],
        }
    else:
        scene_list_json = _json_value(scene_list.get("content_json"), {})
    ordered_scenes = list(ordered_scene_mappings(
        scene_list_json if isinstance(scene_list_json, Mapping) else {}
    ))
    draft = await latest_accepted_draft_manifest(db, str(project["id"]))
    draft_json = _json_value((draft or {}).get("content_json"), {})
    completed_ids = [
        str(item).strip()
        for item in (
            draft_json.get("completedSceneIds", [])
            if isinstance(draft_json, Mapping)
            else []
        )
        if str(item).strip()
    ]
    completed_set = set(completed_ids)
    episode_rows = await list_episode_rows(
        db,
        project_id=str(project["id"]),
        status="accepted",
        include_content=True,
    )
    episode_document_by_number = {
        int(row["episode_number"]): row
        for row in episode_rows
    }

    episode_scenes: dict[int, list[Mapping[str, Any]]] = {}
    for scene in ordered_scenes:
        raw_episode = scene.get("episodeNumber")
        if (
            isinstance(raw_episode, bool)
            or not isinstance(raw_episode, int)
            or raw_episode <= 0
        ):
            raw_episode = 1
        episode_scenes.setdefault(raw_episode, []).append(scene)
    episode_index: list[dict[str, Any]] = []
    next_episode_number: int | None = None
    for episode_number, scenes in episode_scenes.items():
        scene_ids = [str(scene.get("id") or "") for scene in scenes]
        completed_scene_ids = [
            scene_id for scene_id in scene_ids if scene_id in completed_set
        ]
        if not completed_scene_ids:
            status = "pending"
        elif len(completed_scene_ids) == len(scene_ids):
            status = "completed"
        else:
            status = "in_progress"
        if next_episode_number is None and status != "completed":
            next_episode_number = episode_number
        episode_document = episode_document_by_number.get(episode_number)
        episode_index.append({
            "episodeNumber": episode_number,
            "status": status,
            "sceneCount": len(scene_ids),
            "completedSceneCount": len(completed_scene_ids),
            "sceneIds": scene_ids,
            "documentId": (
                str(episode_document["id"])
                if episode_document is not None
                else None
            ),
        })

    raw_requested = arguments.get("episodeNumbers")
    if raw_requested is None:
        bound_scene_ids = {
            str(item).strip()
            for item in state.domain.get(
                "screenplayBoundDraftSceneIds",
                [],
            )
            if str(item).strip()
        }
        requested_numbers = list(dict.fromkeys(
            episode_number
            for episode_number, scenes in episode_scenes.items()
            if any(
                str(scene.get("id") or "").strip() in bound_scene_ids
                for scene in scenes
            )
        ))
        if not requested_numbers and next_episode_number is not None:
            requested_numbers = [next_episode_number]
    elif isinstance(raw_requested, list):
        requested_numbers = list(dict.fromkeys(
            int(item)
            for item in raw_requested
            if isinstance(item, int)
            and not isinstance(item, bool)
            and item > 0
        ))[:3]
    else:
        raise _ToolInputError("episodeNumbers must be an array.")
    unknown_episodes = set(requested_numbers) - set(episode_scenes)
    if unknown_episodes:
        raise _ToolInputError(
            "Requested episodes are outside the accepted scene list: "
            + ", ".join(str(item) for item in sorted(unknown_episodes))
        )

    selected_episodes: list[dict[str, Any]] = []
    for episode_number in requested_numbers:
        document = episode_document_by_number.get(episode_number)
        selected_episodes.append({
            "episodeNumber": episode_number,
            "scenes": [
                {
                    key: scene.get(key)
                    for key in (
                        "id",
                        "order",
                        "heading",
                        "location",
                        "timeOfDay",
                        "characters",
                        "objective",
                        "conflict",
                        "turn",
                        "synopsis",
                    )
                    if scene.get(key) is not None
                }
                for scene in episode_scenes[episode_number]
            ],
            "acceptedDocument": (
                {
                    "id": str(document["id"]),
                    "version": int(document["version"]),
                    "sceneIds": list(document.get("scene_ids", [])),
                    "contentText": str(document.get("content_text") or ""),
                    "continuitySummary": str(
                        document.get("continuity_summary") or ""
                    ),
                }
                if document is not None
                else None
            ),
        })

    executions = (
        draft_json.get("sceneExecutions", [])
        if isinstance(draft_json, Mapping)
        else []
    )
    latest_execution = next(
        (
            dict(item)
            for item in reversed(executions)
            if isinstance(item, Mapping)
        ),
        None,
    )
    previous_episode_document = next(
        (
            episode_document_by_number[number]
            for number in sorted(episode_document_by_number, reverse=True)
            if not requested_numbers or number < min(requested_numbers)
        ),
        None,
    )
    return {
        "sceneListDocumentId": str(scene_list["id"]),
        "acceptedDraftDocumentId": (
            str(draft["id"]) if draft is not None else None
        ),
        "completedSceneCount": len(completed_ids),
        "totalSceneCount": len(ordered_scenes),
        "nextEpisodeNumber": next_episode_number,
        "episodeIndex": episode_index,
        "selectedEpisodes": selected_episodes,
        "previousEpisodeBoundary": {
            "episodeNumber": (
                int(previous_episode_document["episode_number"])
                if previous_episode_document is not None
                else None
            ),
            "continuitySummary": (
                str(previous_episode_document.get("continuity_summary") or "")
                if previous_episode_document is not None
                else str((latest_execution or {}).get("continuityState") or "")
            ),
            "contentTail": (
                str(previous_episode_document.get("content_text") or "")[-6_000:]
                if previous_episode_document is not None
                else ""
            ),
        },
    }, []


async def _propose_scene_list(db, project, arguments):
    required_kind = (
        "episode_outline"
        if str(project.get("format") or "") in _SERIES_FORMATS
        else "beat_sheet"
    )
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        required_accepted_kind=required_kind,
    )
    structure = await get_current_document(
        db,
        str(project["id"]),
        kind=required_kind,
    )
    if structure is None:
        raise _ToolInputError(
            "An accepted structure is required before this proposal."
        )
    structure = (
        await assemble_episode_document(db, structure, include_text=False)
        or structure
    )
    parsed_structure = _json_value(structure.get("content_json"), {})
    try:
        scenes = normalize_scene_trace(
            scenes=arguments.get("scenes"),
            structure_kind=str(structure["kind"]),
            structure_content=(
                parsed_structure
                if isinstance(parsed_structure, Mapping)
                else {}
            ),
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "scene_list",
        "structureId": str(structure["id"]),
        "scenes": scenes,
    }
    return _proposal_result(
        kind="scene_list",
        title=_proposal_title(arguments, "Agent 场景表提案"),
        content_json=content_json,
        content_text=None,
        derived_from_ids=derived_from_ids,
    )


async def _propose_scene_draft(db, project, arguments, state):
    accepted_scene_list = await get_current_document(
        db,
        str(project["id"]),
        kind="scene_list",
    )
    if accepted_scene_list is None:
        raise _ToolInputError(
            "An accepted scene list is required before drafting scenes."
        )
    scene_list_json = _json_value(
        accepted_scene_list.get("content_json"),
        {},
    )
    scenes = (
        scene_list_json.get("scenes")
        if isinstance(scene_list_json, Mapping)
        else None
    )
    if not isinstance(scenes, list) or not scenes:
        raise _ToolInputError(
            "The accepted scene list has no structured scenes."
        )
    ordered_scenes = list(ordered_scene_mappings(scene_list_json))
    ordered_scene_ids = [
        str(scene.get("id") or "").strip()
        for scene in ordered_scenes
        if str(scene.get("id") or "").strip()
    ]
    latest_draft = await latest_accepted_draft_manifest(
        db,
        str(project["id"]),
    )
    previous_completed: list[str] = []
    previous_executions: list[dict[str, Any]] = []
    if latest_draft is not None:
        previous_json = _json_value(latest_draft.get("content_json"), {})
        if (
            not isinstance(previous_json, Mapping)
            or str(previous_json.get("sceneListId") or "")
            != str(accepted_scene_list["id"])
        ):
            raise _ToolInputError(
                "The latest accepted draft is not bound to the current "
                "scene list. Accept a compatible draft before continuing."
            )
        raw_previous = (
            previous_json.get("completedSceneIds")
            if isinstance(previous_json, Mapping)
            else []
        )
        if isinstance(raw_previous, list):
            previous_completed = [
                str(item).strip()
                for item in raw_previous
                if str(item).strip()
            ]
        previous_episode_rows = await list_episode_rows(
            db,
            project_id=str(project["id"]),
            draft_document_id=str(latest_draft["id"]),
            status=None,
            include_content=True,
        )
        previous_executions = [
            dict(execution)
            for episode in previous_episode_rows
            for execution in episode.get("scene_executions", [])
            if isinstance(execution, Mapping)
        ]
    expected_previous = ordered_scene_ids[:len(previous_completed)]
    if previous_completed != expected_previous:
        raise _ToolInputError(
            "The accepted draft does not preserve scene-list order."
        )
    if len(previous_completed) >= len(ordered_scene_ids):
        raise _ToolInputError(
            "Every scene in the accepted scene list is already complete."
        )
    requested_count = _bounded_int(
        state.domain.get("screenplayDraftSceneCount"),
        1,
        1,
        20,
    )
    remaining_scenes = ordered_scenes[len(previous_completed):]
    draft_scope = str(
        state.domain.get("screenplayDraftScope") or "planner"
    ).strip()
    selected_scene_ids = [
        str(scene.get("id") or "").strip()
        for scene in select_draft_scenes(
            remaining_scenes,
            scope=draft_scope,
            fallback_count=requested_count,
        )
        if str(scene.get("id") or "").strip()
    ]
    additional_scenes = arguments.get("additionalScenes", [])
    if additional_scenes is None:
        additional_scenes = []
    if not isinstance(additional_scenes, list) or any(
        not isinstance(item, Mapping) for item in additional_scenes
    ):
        raise _ToolInputError("additionalScenes must be an array of scene drafts.")
    draft_inputs = [arguments, *additional_scenes]
    if len(draft_inputs) != len(selected_scene_ids):
        raise _ToolInputError(
            "The scene draft batch must contain exactly "
            f"{len(selected_scene_ids)} scene(s) selected by the host."
        )
    completed_ids = [*previous_completed, *selected_scene_ids]
    all_scenes_complete = len(completed_ids) == len(ordered_scene_ids)
    scenes_by_id = {
        str(scene.get("id") or "").strip(): scene
        for scene in ordered_scenes
        if str(scene.get("id") or "").strip()
    }
    selected_scenes = [scenes_by_id[scene_id] for scene_id in selected_scene_ids]
    scene_headings = [
        str(scene.get("heading") or "").strip()
        for scene in selected_scenes
    ]
    try:
        current_executions = [
            normalize_scene_execution(
                scene=scene,
                execution=draft_input.get("execution"),
            )
            for scene, draft_input in zip(
                selected_scenes,
                draft_inputs,
                strict=True,
            )
        ]
        scene_executions = validate_scene_execution_history(
            scene_list_content=scene_list_json,
            completed_scene_ids=completed_ids,
            scene_executions=[
                *previous_executions,
                *current_executions,
            ],
            previous_executions=previous_executions,
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error

    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        required_accepted_kind="scene_list",
    )
    if latest_draft is not None:
        latest_draft_id = str(latest_draft["id"])
        if latest_draft_id not in derived_from_ids:
            derived_from_ids.append(latest_draft_id)

    scene_texts = [
        _required_model_text(
            draft_input,
            "sceneText",
            SCENE_DRAFT_PAYLOAD_LIMITS.scene_text_chars,
        )
        for draft_input in draft_inputs
    ]
    notes = str(arguments.get("notes") or "").strip()
    if len(notes) > SCENE_DRAFT_PAYLOAD_LIMITS.draft_notes_chars:
        raise _ToolInputError(
            "notes exceeds "
            f"{SCENE_DRAFT_PAYLOAD_LIMITS.draft_notes_chars} characters."
        )
    content_text = "\n\n".join(item for item in scene_texts if item)
    episode_drafts = build_episode_draft_batches(
        scenes_by_id=scenes_by_id,
        generated_scenes=[
            {
                "sceneId": scene_id,
                "sceneText": scene_text,
                "execution": execution,
            }
            for scene_id, scene_text, execution in zip(
                selected_scene_ids,
                scene_texts,
                current_executions,
                strict=True,
            )
        ],
    )
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "scene_draft",
        "sceneListId": str(accepted_scene_list["id"]),
        "sceneId": selected_scene_ids[0],
        "sceneHeading": scene_headings[0],
        "newSceneIds": selected_scene_ids,
        "newSceneHeadings": scene_headings,
        "completedSceneIds": completed_ids,
        "isComplete": all_scenes_complete,
        "notes": notes,
    }
    if episode_drafts:
        content_json["episodeDrafts"] = episode_drafts
    return _proposal_result(
        kind="scene_draft",
        title=_proposal_title(
            arguments,
            (
                f"Agent 场景正文 · {scene_headings[0] or selected_scene_ids[0]}"
                if len(selected_scene_ids) == 1
                else (
                    "Agent 场景正文 · "
                    f"{selected_scene_ids[0]}–{selected_scene_ids[-1]}"
                )
            ),
        ),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


def _proposal_result(
    *,
    kind: str,
    title: str,
    content_json: Mapping[str, Any],
    content_text: str | None,
    derived_from_ids: list[str],
):
    rendered_text = (
        str(content_text).strip()
        if content_text is not None
        else render_screenplay_proposal(
            kind=kind,
            title=title,
            content=content_json,
        )
    )
    proposal = {
        "kind": kind,
        "title": title,
        "contentJson": dict(content_json),
        "contentText": rendered_text,
        "derivedFromIds": derived_from_ids,
    }
    return (
        {
            "success": True,
            "status": "awaiting_user_review",
            "message": (
                "Proposal delivered for user review. It has not been saved "
                "or accepted."
            ),
        },
        [],
        (DomainEffect(
            type="screenplay.document_proposal",
            payload=proposal,
        ),),
    )


async def _proposal_parent_ids(
    db,
    project: Mapping[str, Any],
    *,
    required_accepted_kind: str | None,
) -> list[str]:
    # Document provenance is host-owned state. Model-facing tools produce
    # creative content, but they must never select persistence identities or
    # mix book/chapter/coverage-plan ids into the screenplay document graph.
    parent_ids: list[str] = []
    if required_accepted_kind:
        accepted = await get_current_document(
            db,
            str(project["id"]),
            kind=required_accepted_kind,
        )
        if accepted is None:
            label = (
                "source analysis"
                if required_accepted_kind == "source_analysis"
                else "creative brief"
                if required_accepted_kind == "creative_brief"
                else required_accepted_kind.replace("_", " ")
            )
            raise _ToolInputError(
                f"An accepted {label} is required before this proposal."
            )
        accepted_id = str(accepted["id"])
        parent_ids = [accepted_id]
    else:
        latest = await get_current_document(
            db,
            str(project["id"]),
            kind="creative_brief",
        )
        if latest is not None:
            parent_ids = [str(latest["id"])]
    return parent_ids


def _proposal_text(arguments: Mapping[str, Any], maximum: int) -> str:
    content_text = str(arguments.get("contentText") or "").strip()
    if not content_text:
        raise _ToolInputError("contentText is required.")
    if len(content_text) > maximum:
        raise _ToolInputError(f"contentText exceeds {maximum} characters.")
    return content_text


def _required_model_text(
    arguments: Mapping[str, Any],
    field: str,
    maximum: int,
) -> str:
    text = str(arguments.get(field) or "").strip()
    if not text:
        raise _ToolInputError(f"{field} is required.")
    if len(text) > maximum:
        raise _ToolInputError(f"{field} exceeds {maximum} characters.")
    return text


def _proposal_title(arguments: Mapping[str, Any], fallback: str) -> str:
    title = str(arguments.get("title") or "").strip() or fallback
    if len(title) > 160:
        raise _ToolInputError("title exceeds 160 characters.")
    return title


async def _get_source_book_overview(db, project, arguments):
    del arguments
    book_id = _require_source_book(project)
    book = await db.fetch_one(
        "SELECT * FROM books WHERE id = ?",
        [book_id],
    )
    outlines = await db.fetch_all(
        "SELECT id, title, type, parent_outline_id, writing_chapter_id "
        "FROM outlines WHERE book_id = ? ORDER BY type ASC, sort ASC",
        [book_id],
    )
    chapters = await scoped_chapters(db, project)
    restricted = is_restricted_source_scope(project)
    if restricted:
        allowed_outline_ids = await scoped_outline_ids(db, project)
        outlines = [
            row for row in outlines
            if str(row["id"]) in allowed_outline_ids
        ]
    counts = {
        "characters": (
            None
            if restricted
            else int((await db.fetch_one(
                "SELECT COUNT(*) AS count FROM characters WHERE book_id = ?",
                [book_id],
            ) or {}).get("count") or 0)
        ),
        "settings": (
            None
            if restricted
            else int((await db.fetch_one(
                "SELECT COUNT(*) AS count FROM setting_entities WHERE book_id = ?",
                [book_id],
            ) or {}).get("count") or 0)
        ),
        "outlines": len(outlines),
        "writingChapters": len(chapters),
    }
    payload = {
        "book": {
            "id": book_id,
            "title": str((book or {}).get("title") or ""),
        },
        "counts": counts,
        "sourceScope": source_scope_summary(project.get("source_scope")),
        "outlines": outlines[:100],
        "writingChapters": chapters[:200],
    }
    refs = [_source_ref(
        "book",
        book_id,
        {"title": payload["book"]["title"], "counts": counts},
        payload["book"]["title"],
    )]
    refs.extend(
        _source_ref(
            "outline",
            str(row["id"]),
            row,
            str(row.get("title") or ""),
        )
        for row in outlines[:100]
    )
    refs.extend(
        _source_ref(
            "chapter",
            str(row["id"]),
            row,
            str(row.get("title") or ""),
            coverage_mode="catalog",
        )
        for row in chapters[:200]
    )
    return payload, refs


async def _get_source_coverage_plan(db, project, arguments):
    del arguments
    _require_source_book(project)
    return await build_source_coverage_plan(db, project), []


async def _read_source_coverage_batch(db, project, arguments):
    _require_source_book(project)
    plan_id = str(arguments.get("planId") or "").strip()
    if not plan_id:
        raise _ToolInputError("planId is required.")
    try:
        batch_number = int(arguments.get("batchNumber"))
    except (TypeError, ValueError):
        raise _ToolInputError(
            "batchNumber must be a positive integer."
        ) from None
    if batch_number < 1:
        raise _ToolInputError("batchNumber must be a positive integer.")
    try:
        payload, coverage_refs = await read_source_coverage_batch(
            db,
            project,
            plan_id=plan_id,
            batch_number=batch_number,
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    refs = [
        _source_ref(
            str(item["sourceType"]),
            str(item["sourceId"]),
            item["revisionText"],
            str(item.get("excerpt") or ""),
            coverage_mode=str(item.get("coverageMode") or "sampled"),
        )
        for item in coverage_refs
    ]
    return payload, refs


async def _search_source_material(db, project, arguments):
    book_id = _require_source_book(project)
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise _ToolInputError("query is required.")
    requested_types = {
        str(item).strip()
        for item in (arguments.get("sourceTypes") or [])
        if str(item).strip() in _VALID_SOURCE_TYPES
    }
    restricted = is_restricted_source_scope(project)
    global_types = {"character", "setting", "background"}
    if restricted and requested_types.intersection(global_types):
        raise _ToolInputError(
            "Character dossiers, world settings, and background are outside "
            "this project's restricted adaptation range."
        )
    allowed_chapter_ids: set[str] | None = None
    allowed_outline_ids: set[str] | None = None
    if restricted:
        allowed_chapter_ids = {
            str(item["id"]) for item in await scoped_chapters(db, project)
        }
        allowed_outline_ids = await scoped_outline_ids(db, project)
    limit = _bounded_int(arguments.get("limit"), 8, 1, 20)
    records = await _matching_source_records(
        db,
        book_id,
        query,
        requested_types,
        per_type_limit=limit,
        allowed_chapter_ids=allowed_chapter_ids,
        allowed_outline_ids=allowed_outline_ids,
        allow_global_sources=not restricted,
    )
    needle = query.casefold()
    hits = []
    for record in records:
        if requested_types and record["sourceType"] not in requested_types:
            continue
        haystack = (
            f"{record['title']}\n{record['content']}"
        ).casefold()
        index = haystack.find(needle)
        if index < 0:
            continue
        hits.append({
            "sourceType": record["sourceType"],
            "sourceId": record["sourceId"],
            "title": record["title"],
            "excerpt": _excerpt_around(
                f"{record['title']}\n{record['content']}",
                query,
            ),
        })
        if len(hits) >= limit:
            break
    refs = [
        _source_ref(
            hit["sourceType"],
            hit["sourceId"],
            next(
                record["content"]
                for record in records
                if record["sourceType"] == hit["sourceType"]
                and record["sourceId"] == hit["sourceId"]
            ),
            hit["excerpt"],
            coverage_mode="search",
        )
        for hit in hits
    ]
    return {
        "query": query,
        "total": len(hits),
        "hits": hits,
    }, refs


async def _read_source_passages(db, project, arguments):
    book_id = _require_source_book(project)
    sources = arguments.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
        raise _ToolInputError("sources must contain between 1 and 10 items.")
    max_characters = _bounded_int(
        arguments.get("maxCharactersPerSource"),
        8000,
        500,
        16000,
    )
    passages = []
    refs = []
    restricted = is_restricted_source_scope(project)
    allowed_chapter_ids = (
        {str(item["id"]) for item in await scoped_chapters(db, project)}
        if restricted
        else None
    )
    allowed_outline_ids = (
        await scoped_outline_ids(db, project)
        if restricted
        else None
    )
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        source_type = str(source.get("sourceType") or "").strip()
        source_id = str(source.get("sourceId") or "").strip()
        if source_type == "chapter":
            if allowed_chapter_ids is not None and source_id not in allowed_chapter_ids:
                raise _ToolInputError(
                    f"chapter:{source_id} is outside the project's adaptation range."
                )
            row = await db.fetch_one(
                "SELECT c.id, c.title, a.content, a.update_time "
                "FROM outline_chapters AS c "
                "JOIN outlines AS o ON o.id = c.outline_id "
                "LEFT JOIN articles AS a ON a.chapter_id = c.id "
                "WHERE c.id = ? AND o.book_id = ? AND o.type = 'writing'",
                [source_id, book_id],
            )
            content = (
                extract_text_from_lexical(row.get("content") or "")
                if row is not None
                else ""
            )
        elif source_type == "outline":
            if allowed_outline_ids is not None and source_id not in allowed_outline_ids:
                raise _ToolInputError(
                    f"outline:{source_id} is outside the project's adaptation range."
                )
            row = await db.fetch_one(
                "SELECT id, title, type, markdown_content, xmind_data, "
                "create_time FROM outlines WHERE id = ? AND book_id = ?",
                [source_id, book_id],
            )
            content = (
                str(
                    row.get("markdown_content")
                    or row.get("xmind_data")
                    or ""
                )
                if row is not None
                else ""
            )
        else:
            raise _ToolInputError(
                "readSourcePassages only accepts chapter or outline sources."
            )
        if row is None:
            raise _ToolInputError(
                f"{source_type}:{source_id} is outside the bound source book."
            )
        visible = _truncate(content, max_characters)
        passage = {
            "sourceType": source_type,
            "sourceId": source_id,
            "title": str(row.get("title") or ""),
            "content": visible,
            "truncated": len(content) > len(visible),
        }
        passages.append(passage)
        refs.append(_source_ref(
            source_type,
            source_id,
            content,
            visible[:1000],
            coverage_mode=(
                "full"
                if source_type == "chapter" and len(visible) == len(content)
                else "passage"
            ),
        ))
    return {"passages": passages}, refs


async def _get_source_characters(db, project, arguments):
    if is_restricted_source_scope(project):
        raise _ToolInputError(
            "Character dossiers are book-global and unavailable inside a "
            "restricted adaptation range."
        )
    book_id = _require_source_book(project)
    rows = await db.fetch_all(
        "SELECT id, name, tags, profile_md, create_time "
        "FROM characters WHERE book_id = ? ORDER BY create_time ASC, id ASC",
        [book_id],
    )
    ids = {
        int(item)
        for item in (arguments.get("characterIds") or [])
        if str(item).isdigit()
    }
    names = {
        str(item).strip().casefold()
        for item in (arguments.get("names") or [])
        if str(item).strip()
    }
    if ids:
        rows = [row for row in rows if int(row["id"]) in ids]
    if names:
        rows = [
            row
            for row in rows
            if str(row.get("name") or "").strip().casefold() in names
        ]
    limit = _bounded_int(arguments.get("limit"), 20, 1, 30)
    rows = rows[:limit]
    characters = [
        {
            "id": row["id"],
            "name": row.get("name") or "",
            "tags": row.get("tags") or "",
            "profile": _truncate(str(row.get("profile_md") or ""), 12000),
        }
        for row in rows
    ]
    refs = [
        _source_ref(
            "character",
            str(row["id"]),
            {
                "name": row.get("name"),
                "tags": row.get("tags"),
                "profile": row.get("profile_md"),
            },
            f"{row.get('name') or ''}: "
            f"{_truncate(str(row.get('profile_md') or ''), 800)}",
        )
        for row in rows
    ]
    return {"characters": characters}, refs


async def _get_source_world_settings(db, project, arguments):
    if is_restricted_source_scope(project):
        raise _ToolInputError(
            "World settings and background are book-global and unavailable "
            "inside a restricted adaptation range."
        )
    book_id = _require_source_book(project)
    rows = await db.fetch_all(
        "SELECT id, entity_type, name, tags, profile_md, create_time "
        "FROM setting_entities WHERE book_id = ? "
        "ORDER BY create_time ASC, id ASC",
        [book_id],
    )
    ids = {
        int(item)
        for item in (arguments.get("entityIds") or [])
        if str(item).isdigit()
    }
    names = {
        str(item).strip().casefold()
        for item in (arguments.get("names") or [])
        if str(item).strip()
    }
    entity_type = str(arguments.get("entityType") or "").strip()
    if ids:
        rows = [row for row in rows if int(row["id"]) in ids]
    if names:
        rows = [
            row
            for row in rows
            if str(row.get("name") or "").strip().casefold() in names
        ]
    if entity_type:
        rows = [
            row
            for row in rows
            if str(row.get("entity_type") or "") == entity_type
        ]
    rows = rows[:_bounded_int(arguments.get("limit"), 20, 1, 30)]
    include_background = arguments.get("includeBackground") is not False
    background = (
        await db.fetch_one(
            "SELECT content, update_time FROM story_background WHERE book_id = ?",
            [book_id],
        )
        if include_background
        else None
    )
    settings = [
        {
            "id": row["id"],
            "entityType": row.get("entity_type"),
            "name": row.get("name") or "",
            "tags": row.get("tags") or "",
            "profile": _truncate(str(row.get("profile_md") or ""), 12000),
        }
        for row in rows
    ]
    refs = [
        _source_ref(
            "setting",
            str(row["id"]),
            row,
            f"{row.get('name') or ''}: "
            f"{_truncate(str(row.get('profile_md') or ''), 800)}",
        )
        for row in rows
    ]
    if background is not None:
        refs.append(_source_ref(
            "background",
            book_id,
            background,
            _truncate(str(background.get("content") or ""), 1000),
        ))
    return {
        "background": (
            _truncate(str(background.get("content") or ""), 16000)
            if background is not None
            else None
        ),
        "settings": settings,
    }, refs


async def _matching_source_records(
    db,
    book_id: str,
    query: str,
    requested_types: set[str],
    *,
    per_type_limit: int,
    allowed_chapter_ids: set[str] | None = None,
    allowed_outline_ids: set[str] | None = None,
    allow_global_sources: bool = True,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    include_all = not requested_types
    if include_all or "outline" in requested_types:
        outlines = await db.fetch_all(
            "SELECT id, title, markdown_content, xmind_data FROM outlines "
            "WHERE book_id = ? AND ("
            "instr(lower(COALESCE(title, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(markdown_content, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(xmind_data, '')), lower(?)) > 0) "
            "ORDER BY sort ASC LIMIT ?",
            [
                book_id,
                query,
                query,
                query,
                (
                    per_type_limit
                    if allowed_outline_ids is None
                    else 100_000
                ),
            ],
        )
        records.extend({
            "sourceType": "outline",
            "sourceId": str(row["id"]),
            "title": str(row.get("title") or ""),
            "content": str(
                row.get("markdown_content") or row.get("xmind_data") or ""
            ),
        } for row in outlines if (
            allowed_outline_ids is None
            or str(row["id"]) in allowed_outline_ids
        ))
    if include_all or "chapter" in requested_types:
        chapters = await db.fetch_all(
            "SELECT c.id, c.title, a.content FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "LEFT JOIN articles AS a ON a.chapter_id = c.id "
            "WHERE o.book_id = ? AND o.type = 'writing' AND ("
            "instr(lower(COALESCE(c.title, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(a.content, '')), lower(?)) > 0) "
            "ORDER BY c.sort ASC LIMIT ?",
            [
                book_id,
                query,
                query,
                (
                    per_type_limit
                    if allowed_chapter_ids is None
                    else 100_000
                ),
            ],
        )
        records.extend({
            "sourceType": "chapter",
            "sourceId": str(row["id"]),
            "title": str(row.get("title") or ""),
            "content": extract_text_from_lexical(row.get("content") or ""),
        } for row in chapters if (
            allowed_chapter_ids is None
            or str(row["id"]) in allowed_chapter_ids
        ))
    if allow_global_sources and (include_all or "character" in requested_types):
        characters = await db.fetch_all(
            "SELECT id, name, tags, profile_md FROM characters "
            "WHERE book_id = ? AND ("
            "instr(lower(COALESCE(name, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(tags, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(profile_md, '')), lower(?)) > 0) "
            "ORDER BY id ASC LIMIT ?",
            [book_id, query, query, query, per_type_limit],
        )
        records.extend({
            "sourceType": "character",
            "sourceId": str(row["id"]),
            "title": str(row.get("name") or ""),
            "content": (
                f"{row.get('tags') or ''}\n{row.get('profile_md') or ''}"
            ),
        } for row in characters)
    if allow_global_sources and (include_all or "setting" in requested_types):
        settings = await db.fetch_all(
            "SELECT id, name, tags, profile_md FROM setting_entities "
            "WHERE book_id = ? AND ("
            "instr(lower(COALESCE(name, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(tags, '')), lower(?)) > 0 OR "
            "instr(lower(COALESCE(profile_md, '')), lower(?)) > 0) "
            "ORDER BY id ASC LIMIT ?",
            [book_id, query, query, query, per_type_limit],
        )
        records.extend({
            "sourceType": "setting",
            "sourceId": str(row["id"]),
            "title": str(row.get("name") or ""),
            "content": (
                f"{row.get('tags') or ''}\n{row.get('profile_md') or ''}"
            ),
        } for row in settings)
    if allow_global_sources and (include_all or "background" in requested_types):
        background = await db.fetch_one(
            "SELECT content FROM story_background WHERE book_id = ? AND "
            "instr(lower(COALESCE(content, '')), lower(?)) > 0",
            [book_id, query],
        )
        if background is not None:
            records.append({
                "sourceType": "background",
                "sourceId": book_id,
                "title": "故事背景",
                "content": str(background.get("content") or ""),
            })
    return records


def _source_ref(
    source_type: str,
    source_id: str,
    revision_value: Any,
    excerpt: str,
    *,
    coverage_mode: str = "referenced",
) -> dict[str, str]:
    encoded = json.dumps(
        revision_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return {
        "sourceType": source_type,
        "sourceId": str(source_id),
        "sourceRevision": sha256(encoded).hexdigest(),
        "coverageMode": str(coverage_mode or "referenced"),
        "excerpt": str(excerpt or "")[:1000],
    }


def _require_source_book(project: Mapping[str, Any]) -> str:
    book_id = str(project.get("source_book_id") or "").strip()
    if not book_id:
        raise _ToolInputError(
            "The current screenplay project has no available source book."
        )
    return book_id


def _error(
    message: str,
    *,
    error_code: str = "tool_execution_failed",
    effect_state: ToolEffectState = ToolEffectState.UNKNOWN,
) -> ToolHandlerResult:
    return ToolHandlerResult(
        content=json.dumps(
            {"success": False, "error": str(message)},
            ensure_ascii=False,
        ),
        error_code=error_code,
        effect_state=effect_state,
    )


def _json_value(value: object, fallback: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, normalized))


def _truncate(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 8)] + "\n…（已截断）"


def _excerpt_around(text: str, query: str, radius: int = 240) -> str:
    value = str(text or "")
    index = value.casefold().find(str(query or "").casefold())
    if index < 0:
        return _truncate(value, radius * 2)
    start = max(0, index - radius)
    end = min(len(value), index + len(query) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(value) else ""
    return prefix + value[start:end].strip() + suffix


class _ToolInputError(ValueError):
    pass

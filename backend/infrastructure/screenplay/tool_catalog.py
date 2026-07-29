"""Project-scoped read and proposal tools for the screenplay Agent."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from agent_core.contracts import (
    AgentRunRequest,
    DomainEffect,
    ExecutionState,
    ToolHandlerResult,
)
from agent_core.ports import CancellationSignal, ToolRegistration
from agent_core.tools import InMemoryToolCatalog
from database.crud.screenplay_source_refs import record_source_refs
from domains.screenplay.adaptation_brief import normalize_creative_brief
from domains.screenplay.source_scope import (
    is_restricted_source_scope,
    parse_source_scope,
    scoped_chapters,
    scoped_outline_ids,
    source_scope_summary,
)
from domains.screenplay.scene_trace import normalize_scene_trace
from domains.screenplay.scene_execution import (
    normalize_scene_execution,
    validate_scene_execution_history,
)
from domains.screenplay.review_trace import (
    build_revision_trace,
    normalize_review_issues,
    normalize_review_verifications,
)
from domains.screenplay.source_coverage import (
    build_source_coverage_plan,
    read_source_coverage_batch,
)
from domains.screenplay.structure_trace import normalize_structure_trace
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)
from domains.screenplay.tool_contracts import (
    SCREENPLAY_TOOL_CONTEXT_CONTRACTS,
    SCREENPLAY_TOOL_POLICIES,
    SCREENPLAY_TOOL_SCHEMAS,
)
from utils.text import extract_text_from_lexical


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
    "proposeSourceAnalysis": frozenset({"orientation", "brief"}),
    "proposeCreativeBrief": frozenset({"orientation", "brief"}),
    "proposeBeatSheet": frozenset({"structure"}),
    "proposeEpisodeOutline": frozenset({"structure"}),
    "proposeSceneList": frozenset({"scenes"}),
    "proposeSceneDraft": frozenset({"draft"}),
    "proposeScreenplayReview": frozenset({"review"}),
    "proposeScreenplayRevision": frozenset({"review"}),
}
_SERIES_FORMATS = frozenset({"连续剧", "竖屏短剧"})
_GLOBAL_SOURCE_TOOL_NAMES = frozenset({
    "getSourceCharacters",
    "getSourceWorldSettings",
})


def build_screenplay_tool_catalog(db) -> InMemoryToolCatalog:
    handlers = {
        "getScreenplayProject": _get_screenplay_project,
        "getScreenplayDocument": _get_screenplay_document,
        "getSourceBookOverview": _get_source_book_overview,
        "getSourceCoveragePlan": _get_source_coverage_plan,
        "readSourceCoverageBatch": _read_source_coverage_batch,
        "searchSourceMaterial": _search_source_material,
        "readSourcePassages": _read_source_passages,
        "getSourceCharacters": _get_source_characters,
        "getSourceWorldSettings": _get_source_world_settings,
        "proposeSourceAnalysis": _propose_source_analysis,
        "proposeCreativeBrief": _propose_creative_brief,
        "proposeBeatSheet": _propose_beat_sheet,
        "proposeEpisodeOutline": _propose_episode_outline,
        "proposeSceneList": _propose_scene_list,
        "proposeSceneDraft": _propose_scene_draft,
        "proposeScreenplayReview": _propose_screenplay_review,
        "proposeScreenplayRevision": _propose_screenplay_revision,
    }
    registrations = tuple(
        ToolRegistration(
            schema=schema,
            handler=_bind_handler(db, schema.name, handlers[schema.name]),
            policy=SCREENPLAY_TOOL_POLICIES[schema.name],
            scope_validator=_scope_validator(db, schema.name),
            context_contract=SCREENPLAY_TOOL_CONTEXT_CONTRACTS[schema.name],
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
    if context.requested_source_book_id:
        enabled.update(_SOURCE_TOOL_NAMES)
    if (
        context.requested_stage == "orientation"
        and context.requested_source_book_id
    ):
        enabled.add("proposeSourceAnalysis")
    elif context.requested_stage == "brief":
        enabled.add("proposeCreativeBrief")
        if context.requested_source_book_id:
            enabled.add("proposeSourceAnalysis")
    elif context.requested_stage == "orientation":
        enabled.add("proposeCreativeBrief")
    elif context.requested_stage == "structure":
        enabled.update({"proposeBeatSheet", "proposeEpisodeOutline"})
    elif context.requested_stage == "scenes":
        enabled.add("proposeSceneList")
    elif context.requested_stage == "draft":
        enabled.add("proposeSceneDraft")
    elif context.requested_stage == "review":
        enabled.update({
            "proposeScreenplayReview",
            "proposeScreenplayRevision",
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
            operation_result = await operation(
                db,
                project,
                dict(arguments),
            )
            payload, refs = operation_result[:2]
            effects = (
                tuple(operation_result[2])
                if len(operation_result) > 2
                else ()
            )
            if refs and state.run_id:
                await record_source_refs(
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
            )
        except _ToolInputError as error:
            return _error(str(error))
        except Exception as error:
            return _error(str(error))

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
                tool_name == "proposeSourceAnalysis"
                and not project.get("source_book_id")
            ):
                return (
                    "proposeSourceAnalysis requires an available source book."
                )
            stage = str(project.get("active_stage") or "")
            if stage not in _PROPOSAL_STAGES[tool_name]:
                return (
                    f"{tool_name} is unavailable during screenplay stage "
                    f"{stage or 'unknown'}."
                )
            screenplay_format = str(project.get("format") or "")
            if (
                tool_name == "proposeBeatSheet"
                and screenplay_format in _SERIES_FORMATS
            ):
                return (
                    "Series projects require proposeEpisodeOutline during "
                    "the structure stage."
                )
            if (
                tool_name == "proposeEpisodeOutline"
                and screenplay_format not in _SERIES_FORMATS
            ):
                return (
                    "Non-series projects require proposeBeatSheet during "
                    "the structure stage."
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
    documents = await db.fetch_all(
        "SELECT id, kind, title, version, status, derived_from_ids, "
        "create_time, update_time FROM screenplay_documents "
        "WHERE project_id = ? ORDER BY kind ASC, version DESC",
        [project["id"]],
    )
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
                **row,
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
    row = await db.fetch_one(
        "SELECT * FROM screenplay_documents WHERE id = ? AND project_id = ?",
        [document_id, project["id"]],
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


async def _propose_source_analysis(db, project, arguments):
    analysis = arguments.get("analysis")
    if not isinstance(analysis, Mapping):
        raise _ToolInputError("analysis must be an object.")
    if not str(analysis.get("rangeSummary") or "").strip():
        raise _ToolInputError("analysis requires rangeSummary.")
    if not str(analysis.get("narrativeSummary") or "").strip():
        raise _ToolInputError("analysis requires narrativeSummary.")
    coverage = analysis.get("coverage")
    if not isinstance(coverage, Mapping):
        raise _ToolInputError("analysis requires coverage.")
    try:
        selected_chapter_count = int(coverage.get("selectedChapterCount"))
    except (TypeError, ValueError):
        raise _ToolInputError(
            "coverage requires a non-negative selectedChapterCount."
        ) from None
    if selected_chapter_count < 0:
        raise _ToolInputError(
            "coverage requires a non-negative selectedChapterCount."
        )
    raw_read_chapter_ids = coverage.get("readChapterIds")
    raw_sampled_chapter_ids = coverage.get("sampledChapterIds")
    raw_limitations = coverage.get("limitations")
    if not isinstance(raw_read_chapter_ids, list):
        raise _ToolInputError("coverage readChapterIds must be an array.")
    if not isinstance(raw_sampled_chapter_ids, list):
        raise _ToolInputError("coverage sampledChapterIds must be an array.")
    if not isinstance(raw_limitations, list):
        raise _ToolInputError("coverage limitations must be an array.")
    read_chapter_ids = list(dict.fromkeys(
        str(item).strip()
        for item in raw_read_chapter_ids
        if str(item).strip()
    ))
    sampled_chapter_ids = [
        chapter_id
        for chapter_id in dict.fromkeys(
            str(item).strip()
            for item in raw_sampled_chapter_ids
            if str(item).strip()
        )
        if chapter_id not in set(read_chapter_ids)
    ]
    limitations = [
        str(item).strip()
        for item in raw_limitations
        if str(item).strip()
    ]
    covered_chapter_count = len(read_chapter_ids) + len(sampled_chapter_ids)
    if covered_chapter_count > selected_chapter_count:
        raise _ToolInputError(
            "coverage cannot include more chapters than the selected range."
        )
    if (
        (sampled_chapter_ids or selected_chapter_count > covered_chapter_count)
        and not limitations
    ):
        raise _ToolInputError(
            "Sampled or partial coverage must disclose at least one limitation."
        )
    plot_events = analysis.get("plotEvents")
    if not isinstance(plot_events, list) or not plot_events:
        raise _ToolInputError(
            "analysis requires at least one plot event."
        )
    event_orders: list[int] = []
    for event in plot_events:
        if not isinstance(event, Mapping):
            raise _ToolInputError("Every plot event must be an object.")
        try:
            order = int(event.get("order"))
        except (TypeError, ValueError):
            raise _ToolInputError(
                "Every plot event requires a positive integer order."
            ) from None
        if order < 1:
            raise _ToolInputError(
                "Every plot event requires a positive integer order."
            )
        if not str(event.get("event") or "").strip():
            raise _ToolInputError("Every plot event requires event text.")
        event_orders.append(order)
    if event_orders != list(range(1, len(event_orders) + 1)):
        raise _ToolInputError(
            "Plot event order must start at 1 and increase continuously."
        )
    evidence = analysis.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise _ToolInputError(
            "analysis requires at least one source evidence item."
        )
    normalized_evidence: list[dict[str, str]] = []
    seen_evidence: set[tuple[str, str]] = set()
    for item in evidence:
        if not isinstance(item, Mapping):
            raise _ToolInputError("Every evidence item must be an object.")
        source_type = str(item.get("sourceType") or "").strip()
        source_id = str(item.get("sourceId") or "").strip()
        claim = str(item.get("claim") or "").strip()
        if (
            source_type not in {*_VALID_SOURCE_TYPES, "book"}
            or not source_id
            or not claim
        ):
            raise _ToolInputError(
                "Every evidence item requires a valid sourceType, "
                "sourceId, and claim."
            )
        key = (source_type, source_id)
        if key in seen_evidence:
            continue
        seen_evidence.add(key)
        normalized_evidence.append({
            "sourceType": source_type,
            "sourceId": source_id,
            "claim": claim,
        })
    normalized_analysis = dict(analysis)
    normalized_analysis["coverage"] = {
        "selectedChapterCount": selected_chapter_count,
        "readChapterIds": read_chapter_ids,
        "sampledChapterIds": sampled_chapter_ids,
        "limitations": limitations,
    }
    normalized_analysis["evidence"] = normalized_evidence
    content_text = _proposal_text(arguments, 300_000)
    accepted_analysis = await db.fetch_one(
        "SELECT id FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'source_analysis' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project["id"]],
    )
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind=(
            "source_analysis" if accepted_analysis is not None else None
        ),
    )
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "source_analysis",
        "analysis": normalized_analysis,
    }
    return _proposal_result(
        kind="source_analysis",
        title=_proposal_title(arguments, "Agent 原作范围分析"),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_creative_brief(db, project, arguments):
    brief = arguments.get("brief")
    if not isinstance(brief, Mapping):
        raise _ToolInputError("brief must be an object.")
    content_text = _proposal_text(arguments, 100_000)
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind=(
            "source_analysis"
            if project.get("source_book_id")
            else None
        ),
    )
    source_analysis_id = None
    source_analysis_content = None
    if project.get("source_book_id"):
        source_analysis = await db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'source_analysis' "
            "AND status = 'accepted' "
            "ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        if source_analysis is None:
            raise _ToolInputError(
                "An accepted source analysis is required before this proposal."
            )
        source_analysis_id = str(source_analysis["id"])
        parsed = _json_value(source_analysis.get("content_json"), {})
        source_analysis_content = (
            parsed if isinstance(parsed, Mapping) else {}
        )
    try:
        normalized_brief = normalize_creative_brief(
            brief,
            project_format=str(project.get("format") or ""),
            source_analysis=source_analysis_content,
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "creative_brief",
        "brief": normalized_brief,
    }
    if source_analysis_id is not None:
        content_json["sourceAnalysisId"] = source_analysis_id
    return _proposal_result(
        kind="creative_brief",
        title=_proposal_title(arguments, "Agent 创作简报提案"),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_beat_sheet(db, project, arguments):
    content_text = _proposal_text(arguments, 200_000)
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind="creative_brief",
    )
    creative_brief = await _current_accepted_creative_brief(
        db,
        str(project["id"]),
    )
    try:
        beats, decision_coverage = normalize_structure_trace(
            kind="beat_sheet",
            units=arguments.get("beats"),
            decision_coverage=arguments.get("decisionCoverage"),
            creative_brief=creative_brief["content"],
            require_adaptation_decisions=bool(project.get("source_book_id")),
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "beat_sheet",
        "creativeBriefId": creative_brief["id"],
        "beats": beats,
        "decisionCoverage": decision_coverage,
    }
    return _proposal_result(
        kind="beat_sheet",
        title=_proposal_title(arguments, "Agent 节拍表提案"),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_episode_outline(db, project, arguments):
    content_text = _proposal_text(arguments, 300_000)
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind="creative_brief",
    )
    creative_brief = await _current_accepted_creative_brief(
        db,
        str(project["id"]),
    )
    try:
        episodes, decision_coverage = normalize_structure_trace(
            kind="episode_outline",
            units=arguments.get("episodes"),
            decision_coverage=arguments.get("decisionCoverage"),
            creative_brief=creative_brief["content"],
            require_adaptation_decisions=bool(project.get("source_book_id")),
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "episode_outline",
        "creativeBriefId": creative_brief["id"],
        "episodes": episodes,
        "decisionCoverage": decision_coverage,
    }
    return _proposal_result(
        kind="episode_outline",
        title=_proposal_title(arguments, "Agent 分集结构提案"),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_scene_list(db, project, arguments):
    required_kind = (
        "episode_outline"
        if str(project.get("format") or "") in _SERIES_FORMATS
        else "beat_sheet"
    )
    content_text = _proposal_text(arguments, 500_000)
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind=required_kind,
    )
    structure = await db.fetch_one(
        "SELECT id, kind, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = ? AND status = 'accepted' "
        "ORDER BY version DESC LIMIT 1",
        [project["id"], required_kind],
    )
    if structure is None:
        raise _ToolInputError(
            "An accepted structure is required before this proposal."
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
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_scene_draft(db, project, arguments):
    accepted_scene_list = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'scene_list' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project["id"]],
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
    ordered_scenes = sorted(
        (
            scene for scene in scenes
            if isinstance(scene, Mapping)
        ),
        key=lambda scene: _bounded_int(
            scene.get("order"),
            1_000_000,
            1,
            1_000_000,
        ),
    )
    ordered_scene_ids = [
        str(scene.get("id") or "").strip()
        for scene in ordered_scenes
        if str(scene.get("id") or "").strip()
    ]
    scenes_by_id = {
        str(scene.get("id") or "").strip(): scene
        for scene in ordered_scenes
        if str(scene.get("id") or "").strip()
    }
    scene_id = str(arguments.get("sceneId") or "").strip()
    if scene_id not in ordered_scene_ids:
        raise _ToolInputError(
            "sceneId must reference the accepted scene list."
        )
    completed = arguments.get("completedSceneIds")
    if not isinstance(completed, list) or not completed:
        raise _ToolInputError(
            "completedSceneIds must contain at least the current scene."
        )
    completed_ids = list(dict.fromkeys(
        str(item).strip()
        for item in completed
        if str(item).strip()
    ))
    if scene_id not in completed_ids:
        raise _ToolInputError(
            "completedSceneIds must include the current sceneId."
        )
    if not set(completed_ids).issubset(set(ordered_scene_ids)):
        raise _ToolInputError(
            "completedSceneIds must belong to the accepted scene list."
        )
    expected_prefix = ordered_scene_ids[:len(completed_ids)]
    if completed_ids != expected_prefix:
        raise _ToolInputError(
            "Scenes must be completed in the accepted scene-list order."
        )
    if completed_ids[-1] != scene_id:
        raise _ToolInputError(
            "sceneId must be the newly completed final scene."
        )

    latest_draft = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'scene_draft' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project["id"]],
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
        raw_executions = previous_json.get("sceneExecutions")
        if isinstance(raw_executions, list):
            previous_executions = [
                dict(item) for item in raw_executions
                if isinstance(item, Mapping)
            ]
    if completed_ids != [*previous_completed, scene_id]:
        raise _ToolInputError(
            "Each rolling draft must preserve prior scenes and append exactly "
            "the next scene."
        )

    all_scenes_complete = (
        set(completed_ids) == set(ordered_scene_ids)
    )
    requested_complete = arguments.get("isComplete") is True
    if requested_complete != all_scenes_complete:
        raise _ToolInputError(
            "isComplete must match whether every accepted scene-list id "
            "appears in completedSceneIds."
        )
    planned_scene = scenes_by_id[scene_id]
    scene_heading = str(arguments.get("sceneHeading") or "").strip()
    if scene_heading != str(planned_scene.get("heading") or "").strip():
        raise _ToolInputError(
            "sceneHeading must match the accepted scene-list heading."
        )
    try:
        current_execution = normalize_scene_execution(
            scene=planned_scene,
            execution=arguments.get("execution"),
        )
        scene_executions = validate_scene_execution_history(
            scene_list_content=scene_list_json,
            completed_scene_ids=completed_ids,
            scene_executions=[
                *previous_executions,
                current_execution,
            ],
            previous_executions=previous_executions,
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error

    supplied = arguments.get("derivedFromIds") or []
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind="scene_list",
    )
    if latest_draft is not None:
        latest_draft_id = str(latest_draft["id"])
        if supplied and latest_draft_id not in derived_from_ids:
            raise _ToolInputError(
                "The next rolling draft must derive from the latest "
                "accepted scene draft."
            )
        if latest_draft_id not in derived_from_ids:
            derived_from_ids.append(latest_draft_id)

    content_text = _proposal_text(arguments, 2_000_000)
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "scene_draft",
        "sceneListId": str(accepted_scene_list["id"]),
        "sceneId": scene_id,
        "sceneHeading": scene_heading,
        "completedSceneIds": completed_ids,
        "sceneExecutions": scene_executions,
        "isComplete": all_scenes_complete,
        "notes": str(arguments.get("notes") or "").strip(),
    }
    return _proposal_result(
        kind="scene_draft",
        title=_proposal_title(
            arguments,
            f"Agent 场景正文 · {content_json['sceneHeading'] or scene_id}",
        ),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_screenplay_review(db, project, arguments):
    accepted_draft = await _accepted_complete_draft(db, project)
    draft_json = accepted_draft["content_json"]
    try:
        issues = normalize_review_issues(
            issues=arguments.get("issues"),
            completed_scene_ids=draft_json.get("completedSceneIds"),
            scene_executions=draft_json.get("sceneExecutions"),
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    strengths = arguments.get("strengths")
    if not isinstance(strengths, list):
        raise _ToolInputError("strengths must be an array.")
    verification_results: list[dict[str, Any]] = []
    verification_review_id = ""
    previous_review_id = str(draft_json.get("reviewId") or "").strip()
    revision_of = str(draft_json.get("revisionOf") or "").strip()
    if previous_review_id or revision_of:
        if not previous_review_id or not revision_of:
            raise _ToolInputError(
                "The current revision has incomplete review provenance."
            )
        previous_review = await db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE id = ? AND project_id = ? AND kind = 'review'",
            [previous_review_id, project["id"]],
        )
        previous_review_json = _json_value(
            (previous_review or {}).get("content_json"),
            {},
        )
        if (
            previous_review is None
            or not isinstance(previous_review_json, Mapping)
            or str(previous_review_json.get("reviewedDraftId") or "")
            != revision_of
        ):
            raise _ToolInputError(
                "The current revision cannot be traced to its prior review."
            )
        try:
            verification_results = normalize_review_verifications(
                previous_review_issues=previous_review_json.get("issues"),
                issue_resolutions=draft_json.get("issueResolutions"),
                verification_results=arguments.get("verificationResults"),
            )
        except ValueError as error:
            raise _ToolInputError(str(error)) from error
        verification_review_id = previous_review_id
        previous_issue_ids = {
            item["issueId"] for item in verification_results
        }
        current_issue_ids = {str(item["id"]) for item in issues}
        failed_verification_ids = {
            item["issueId"]
            for item in verification_results
            if item["status"] != "verified"
        }
        verified_issue_ids = previous_issue_ids - failed_verification_ids
        if not failed_verification_ids.issubset(current_issue_ids):
            raise _ToolInputError(
                "Every failed verification must remain in the current issues."
            )
        if verified_issue_ids & current_issue_ids:
            raise _ToolInputError(
                "Verified prior issues cannot remain open in the current review."
            )
    elif arguments.get("verificationResults"):
        raise _ToolInputError(
            "verificationResults are only valid when reviewing a revision."
        )
    verdict = str(arguments.get("verdict") or "").strip()
    if verdict == "ready" and issues:
        raise _ToolInputError("A ready review cannot contain open issues.")
    if verdict in {"revise", "major_rework"} and not issues:
        raise _ToolInputError("A revision verdict requires at least one issue.")
    if (
        verdict == "ready"
        and any(item["status"] != "verified" for item in verification_results)
    ):
        raise _ToolInputError(
            "A ready rereview requires every prior issue to be verified."
        )
    content_text = _proposal_text(arguments, 300_000)
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind="scene_draft",
    )
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "review",
        "reviewedDraftId": str(accepted_draft["id"]),
        "summary": str(arguments.get("summary") or "").strip(),
        "strengths": strengths,
        "issues": issues,
        "verdict": verdict,
    }
    if verification_review_id:
        content_json.update({
            "verificationOfReviewId": verification_review_id,
            "verificationResults": verification_results,
            "verifiedIssueIds": [
                item["issueId"]
                for item in verification_results
                if item["status"] == "verified"
            ],
            "failedVerificationIssueIds": [
                item["issueId"]
                for item in verification_results
                if item["status"] != "verified"
            ],
        })
    return _proposal_result(
        kind="review",
        title=_proposal_title(arguments, "Agent 剧本审阅报告"),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _propose_screenplay_revision(db, project, arguments):
    accepted_draft = await _accepted_complete_draft(db, project)
    accepted_review = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'review' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project["id"]],
    )
    if accepted_review is None:
        raise _ToolInputError(
            "An accepted screenplay review is required before revision."
        )
    review_json = _json_value(accepted_review.get("content_json"), {})
    if (
        not isinstance(review_json, Mapping)
        or str(review_json.get("reviewedDraftId") or "")
        != str(accepted_draft["id"])
        or str(review_json.get("verdict") or "") == "ready"
    ):
        raise _ToolInputError(
            "The accepted screenplay review does not apply to the current draft."
        )
    raw_issues = review_json.get("issues")
    supplied = arguments.get("derivedFromIds") or []
    derived_from_ids = await _proposal_parent_ids(
        db,
        project,
        arguments,
        required_accepted_kind="scene_draft",
    )
    review_id = str(accepted_review["id"])
    if supplied and review_id not in derived_from_ids:
        raise _ToolInputError(
            "The revision must derive from the accepted review."
        )
    if review_id not in derived_from_ids:
        derived_from_ids.append(review_id)
    draft_json = accepted_draft.get("content_json")
    completed_scene_ids = (
        draft_json.get("completedSceneIds")
        if isinstance(draft_json, Mapping)
        else []
    )
    accepted_scene_list = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'scene_list' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project["id"]],
    )
    scene_list_json = _json_value(
        (accepted_scene_list or {}).get("content_json"),
        {},
    )
    if (
        accepted_scene_list is None
        or not isinstance(scene_list_json, Mapping)
        or str(draft_json.get("sceneListId") or "")
        != str(accepted_scene_list["id"])
    ):
        raise _ToolInputError(
            "The current draft is not bound to the accepted scene list."
        )
    try:
        (
            issue_resolutions,
            scene_executions,
            reassessed_scene_ids,
        ) = build_revision_trace(
            scene_list_content=scene_list_json,
            completed_scene_ids=completed_scene_ids,
            previous_executions=draft_json.get("sceneExecutions"),
            review_issues=raw_issues,
            issue_resolutions=arguments.get("issueResolutions"),
            execution_updates=arguments.get("executionUpdates"),
        )
    except ValueError as error:
        raise _ToolInputError(str(error)) from error
    resolved_issue_ids = [
        item["issueId"]
        for item in issue_resolutions
        if item["status"] == "resolved"
    ]
    partially_resolved_issue_ids = [
        item["issueId"]
        for item in issue_resolutions
        if item["status"] == "partially_resolved"
    ]
    content_text = _proposal_text(arguments, 2_000_000)
    content_json = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "scene_draft",
        "isComplete": True,
        "sceneListId": str(draft_json.get("sceneListId") or ""),
        "completedSceneIds": (
            completed_scene_ids
            if isinstance(completed_scene_ids, list)
            else []
        ),
        "sceneExecutions": scene_executions,
        "revisionOf": str(accepted_draft["id"]),
        "reviewId": review_id,
        "issueResolutions": issue_resolutions,
        "reassessedSceneIds": reassessed_scene_ids,
        "resolvedIssueIds": resolved_issue_ids,
        "partiallyResolvedIssueIds": partially_resolved_issue_ids,
        "revisionSummary": str(
            arguments.get("revisionSummary") or ""
        ).strip(),
    }
    return _proposal_result(
        kind="scene_draft",
        title=_proposal_title(arguments, "Agent 完整剧本修订稿"),
        content_json=content_json,
        content_text=content_text,
        derived_from_ids=derived_from_ids,
    )


async def _accepted_complete_draft(db, project) -> dict[str, Any]:
    accepted_draft = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'scene_draft' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project["id"]],
    )
    if accepted_draft is None:
        raise _ToolInputError(
            "An accepted complete scene draft is required for review."
        )
    content_json = _json_value(accepted_draft.get("content_json"), {})
    if (
        not isinstance(content_json, Mapping)
        or content_json.get("isComplete") is not True
    ):
        raise _ToolInputError(
            "The accepted scene draft is not marked complete."
        )
    accepted_draft["content_json"] = content_json
    return accepted_draft


def _proposal_result(
    *,
    kind: str,
    title: str,
    content_json: Mapping[str, Any],
    content_text: str,
    derived_from_ids: list[str],
):
    proposal = {
        "kind": kind,
        "title": title,
        "contentJson": dict(content_json),
        "contentText": content_text,
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
    arguments: Mapping[str, Any],
    *,
    required_accepted_kind: str | None,
) -> list[str]:
    supplied = arguments.get("derivedFromIds") or []
    if not isinstance(supplied, list):
        raise _ToolInputError("derivedFromIds must be an array.")
    parent_ids = list(dict.fromkeys(
        str(item).strip()
        for item in supplied
        if str(item).strip()
    ))
    rows = []
    if parent_ids:
        placeholders = ",".join("?" for _ in parent_ids)
        rows = await db.fetch_all(
            "SELECT id, kind, status FROM screenplay_documents "
            f"WHERE project_id = ? AND id IN ({placeholders})",
            [project["id"], *parent_ids],
        )
        if len(rows) != len(parent_ids):
            raise _ToolInputError(
                "Every derivedFromId must belong to the bound screenplay "
                "project."
            )
    if required_accepted_kind:
        accepted = await db.fetch_one(
            "SELECT id FROM screenplay_documents "
            "WHERE project_id = ? AND kind = ? AND status = 'accepted' "
            "ORDER BY version DESC LIMIT 1",
            [project["id"], required_accepted_kind],
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
        if parent_ids and accepted_id not in parent_ids:
            label = (
                "source analysis"
                if required_accepted_kind == "source_analysis"
                else "creative brief"
                if required_accepted_kind == "creative_brief"
                else required_accepted_kind.replace("_", " ")
            )
            raise _ToolInputError(
                f"The proposal must derive from the accepted {label}."
            )
        if not parent_ids:
            parent_ids = [accepted_id]
    elif not parent_ids:
        latest = await db.fetch_one(
            "SELECT id FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'creative_brief' "
            "ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        if latest is not None:
            parent_ids = [str(latest["id"])]
    return parent_ids


async def _current_accepted_creative_brief(
    db,
    project_id: str,
) -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'creative_brief' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project_id],
    )
    if row is None:
        raise _ToolInputError(
            "An accepted creative brief is required before this proposal."
        )
    parsed = _json_value(row.get("content_json"), {})
    return {
        "id": str(row["id"]),
        "content": parsed if isinstance(parsed, Mapping) else {},
    }


def _proposal_text(arguments: Mapping[str, Any], maximum: int) -> str:
    content_text = str(arguments.get("contentText") or "").strip()
    if not content_text:
        raise _ToolInputError("contentText is required.")
    if len(content_text) > maximum:
        raise _ToolInputError(f"contentText exceeds {maximum} characters.")
    return content_text


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
        "excerpt": str(excerpt or "")[:1000],
    }


def _require_source_book(project: Mapping[str, Any]) -> str:
    book_id = str(project.get("source_book_id") or "").strip()
    if not book_id:
        raise _ToolInputError(
            "The current screenplay project has no available source book."
        )
    return book_id


def _error(message: str) -> ToolHandlerResult:
    return ToolHandlerResult(
        content=json.dumps(
            {"success": False, "error": str(message)},
            ensure_ascii=False,
        ),
        error_code="tool_execution_failed",
    )


def _json_value(value: object, fallback: Any) -> Any:
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

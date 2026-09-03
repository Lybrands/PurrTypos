"""Composition-facing builder for the screenplay Tool Catalog."""

from __future__ import annotations

import json

from database.crud.screenplay_source_receipts import record_source_receipts
from purra.artifacts import ArtifactValidationError

from domains.screenplay_agent.tools import (
    ScreenplayToolInputError,
    build_screenplay_tool_catalog as build_domain_catalog,
)
from infrastructure.screenplay.tools.candidate_artifact import (
    ScreenplayCandidateArtifacts,
)
from infrastructure.screenplay.tools.query import ScreenplayToolQuery
from infrastructure.screenplay.tools.read_cache import cached_screenplay_read
from infrastructure.screenplay.tools.read_evidence import (
    consumed_task_part_keys, has_prepared_read, record_prepared_source_receipts,
)


def build_screenplay_tool_catalog(*, db, candidate_normalizer=None):
    query = ScreenplayToolQuery(db)
    candidates = ScreenplayCandidateArtifacts(
        db,
        candidate_normalizer=candidate_normalizer,
    )

    def bind_read(tool_name, method):
        async def handler(state, arguments, signal):
            del signal
            try:
                result, from_cache = await cached_screenplay_read(
                    db, tool_name, method, state.domain, arguments,
                )
            except ScreenplayToolInputError as error:
                return _input_error(error)
            refs = query.source_refs(tool_name, result)
            if refs and state.run_id:
                await record_source_receipts(
                    db,
                    project_id=str(state.domain["projectId"]),
                    agent_run_id=str(state.run_id),
                    tool_name=tool_name,
                    refs=refs,
                )
            return {"content": _encode(result), "fromCache": from_cache}

        return handler

    async def write_candidate(state, arguments, signal):
        del signal
        try:
            dependency_keys = state.domain.get("dependencyPartKeys")
            evidence_read_operations = (
                {"readScreenplayDeliverable": read_operations[
                    "readScreenplayDeliverable"
                ]}
                if (
                    str(state.domain.get("toolAccess") or "")
                    == "creative_brief_section"
                    and bool(state.domain.get("deliverableRevisionScope"))
                )
                else read_operations
            )
            if dependency_keys and not set(dependency_keys).issubset(
                await consumed_task_part_keys(db, str(state.run_id or ""))
            ):
                raise ScreenplayToolInputError(
                    "The candidate cannot be written before its dependencies are read.",
                    guidance=(
                        "Call readScreenplayTaskDependencies with the bound Part "
                        "keys, wait for success, then retry the candidate write."
                    ),
                )
            if (
                str(state.domain.get("toolAccess") or "")
                in {
                    "all",
                    "review_dimension",
                    "source_chapter_digest",
                    "source_digest_reduction",
                    "source_analysis_section",
                    "creative_brief_section",
                    "series_arc_index",
                    "series_arc_phase",
                    "episode_plan_index",
                    "episode_plan_fragment",
                    "character_arcs_index",
                    "character_arc_fragment",
                    "scene_list_episode",
                }
                and str(state.domain.get("expectedPartType") or "")
                in {"document_section", "review_dimension"}
                and (
                    str(state.domain.get("toolAccess") or "")
                    != "creative_brief_section"
                    or "deliverableRevisionScope" not in state.domain
                    or bool(state.domain.get("deliverableRevisionScope"))
                )
                and not await _has_successful_read_operation(
                    db,
                    str(state.run_id or ""),
                    evidence_read_operations,
                )
            ):
                raise ScreenplayToolInputError(
                    "The candidate cannot be written before its evidence is read.",
                    guidance=(
                        "Call one available screenplay read tool, wait for its "
                        "successful result, then retry writeScreenplayCandidatePart."
                    ),
                )
            await record_prepared_source_receipts(db, state.run_id, str(state.domain["projectId"]))
            result = await candidates.write(state, arguments)
        except ScreenplayToolInputError as error:
            return _input_error(error)
        except ArtifactValidationError as error:
            if error.code not in _CORRECTABLE_CANDIDATE_CODES:
                raise
            return _input_error(ScreenplayToolInputError(
                "The screenplay candidate does not satisfy the candidate contract.",
                guidance=(
                    "Correct the candidate payload using the registered schema and "
                    "writeScreenplayCandidatePart instructions, then retry once."
                ),
                details={"validationCode": error.code, **dict(error.details)},
            ))
        return {
            "content": _encode(result),
            "effect": ("screenplay.candidate_part_written", result),
        }

    async def inspect_candidate(state, arguments, signal):
        del arguments, signal
        return {"content": _encode(await candidates.inspect(state))}

    read_operations = {
        "readScreenplayTaskDependencies": query.task_dependencies,
        "inspectScreenplayProject": query.inspect_project,
        "readScreenplayDeliverable": query.read_deliverable,
        "searchScreenplayDeliverables": query.search_deliverables,
        "getScreenplayEpisodeContext": query.episode_context,
        "inspectSourceStructure": query.inspect_source_structure,
        "readSourceChapters": query.read_source_chapters,
        "searchSourceText": query.search_source_text,
        "listSourceCharacters": query.list_characters,
        "readSourceCharacters": query.read_characters,
        "listSourceWorldEntities": query.list_world_entities,
        "readSourceWorldEntities": query.read_world_entities,
        "readSourceBackground": query.read_background,
        "querySourceStoryFacts": query.query_story_facts,
        "readSourceOutline": query.read_outline,
    }
    return build_domain_catalog(handlers={
        **{
            name: bind_read(name, operation)
            for name, operation in read_operations.items()
        },
        "writeScreenplayCandidatePart": write_candidate,
        "inspectScreenplayCandidate": inspect_candidate,
    })


async def _has_successful_read_operation(db, run_id: str, read_operations) -> bool:
    if not run_id:
        return False
    if await has_prepared_read(db, run_id, read_operations):
        return True
    placeholders = ",".join("?" for _ in read_operations)
    row = await db.fetch_one(
        "SELECT 1 AS present FROM ai_agent_run_events AS started "
        "JOIN ai_agent_run_events AS finished "
        "ON finished.run_id = started.run_id "
        "AND finished.event_type = 'operation.finished' "
        "AND json_extract(finished.payload_json, '$.operationId') = "
        "json_extract(started.payload_json, '$.operationId') "
        "WHERE started.run_id = ? "
        "AND started.event_type = 'operation.started' "
        "AND json_extract(started.payload_json, '$.kind') = 'tool' "
        f"AND json_extract(started.payload_json, '$.display.labelParams.toolName') "
        f"IN ({placeholders}) "
        "AND json_extract(finished.payload_json, '$.status') = 'succeeded' "
        "LIMIT 1",
        [run_id, *read_operations],
    )
    return row is not None


def _encode(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _input_error(error: ScreenplayToolInputError) -> dict[str, str]:
    return {
        "content": _encode(error.to_payload()),
        "errorCode": "tool_input_invalid",
        "effectState": "not_started",
    }


_CORRECTABLE_CANDIDATE_CODES = frozenset({
    "candidate_part_count_invalid",
    "candidate_part_empty",
    "candidate_part_too_large",
})


__all__ = ["build_screenplay_tool_catalog"]

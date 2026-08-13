"""Composition-facing builder for the screenplay Tool Catalog."""

from __future__ import annotations

import json

from database.crud.screenplay_source_receipts import record_source_receipts
from purra.artifacts.errors import ArtifactValidationError

from domains.screenplay_agent.tools import (
    ScreenplayToolInputError,
    build_screenplay_tool_catalog as build_domain_catalog,
)
from infrastructure.screenplay.tools.candidate_artifact import (
    ScreenplayCandidateArtifacts,
)
from infrastructure.screenplay.tools.query import ScreenplayToolQuery


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
                result = await method(state.domain, arguments)
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
            return {"content": _encode(result)}

        return handler

    async def write_candidate(state, arguments, signal):
        del signal
        try:
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
        "readSourceStyle": query.read_style,
    }
    return build_domain_catalog(handlers={
        **{
            name: bind_read(name, operation)
            for name, operation in read_operations.items()
        },
        "writeScreenplayCandidatePart": write_candidate,
        "inspectScreenplayCandidate": inspect_candidate,
    })


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

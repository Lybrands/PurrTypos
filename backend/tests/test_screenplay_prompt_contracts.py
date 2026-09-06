from __future__ import annotations

import json

import pytest

import application.screenplay_agent_task_executor as executor
from application.screenplay_part_contracts import PART_CONTRACTS
from application.screenplay_step_skills import with_screenplay_step_skill
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.candidate_projection import (
    SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
)
from domains.screenplay_agent.tools.catalog import build_screenplay_tool_catalog
from domains.screenplay_agent.tools.schemas import SCREENPLAY_TOOL_SCHEMAS
from purra.contracts import AgentRunRequest, ModelRequest


_PHASE = {"key": "phase-1", "title": '迷雾中的“门”', "objective": '找到 "出口"\n确认代价'}
_EPISODE = {"number": 3, "id": 'episode-"three"', "title": '门后的 "回声"'}
_CHARACTER = {"key": "character-1", "name": '被称作 "影子" 的人'}
_SCENE_IDS = ['scene-"one"', "scene-2"]
_CHAPTER_ID = 'chapter-"one"'
_DIGEST_ID = "source-analysis:reduction:1"


# final_response uses _compose_final_response -> run_public_text with its own
# instruction; that execution entry does not call with_screenplay_step_skill.
@pytest.mark.parametrize(
    "part_key", tuple(key for key in PART_CONTRACTS if key != "final_response"),
)
def test_every_skill_backed_part_contract_has_a_loadable_method(part_key):
    assembled = with_screenplay_step_skill(PART_CONTRACTS[part_key], "执行约束")
    assert assembled.endswith("\n\n执行约束")

_CANDIDATE_PROMPTS = [
    ("episode_metadata", executor._episode_metadata_tool_instruction(3),
     {"episodeNumber": 3}),
    ("review_dimension", executor._review_dimension_tool_instruction(3, "continuity", _SCENE_IDS),
     {"episodeNumber": 3, "dimension": "continuity", "allowedSceneIds": _SCENE_IDS,
      "reviewedDraftId": "draft-bound", "reviewedContentDigest": "a" * 64}),
    ("source_analysis.chapter_digest", executor._source_chapter_digest_tool_instruction(
        chapter_id=_CHAPTER_ID, chapter_title='第一章 "雨夜"', chapter_index=1),
     {"chapterId": _CHAPTER_ID}),
    ("source_analysis.digest_reduction", executor._source_digest_reduction_tool_instruction(_DIGEST_ID),
     {"digestId": _DIGEST_ID}),
    *[(f"source_analysis.{section}", executor._source_analysis_section_tool_instruction(section),
       {"sectionKey": section})
      for section in ("characters", "story", "world", "themes", "adaptation_risks")],
    *[(f"creative_brief.{section}", executor._creative_brief_section_tool_instruction(section),
       {"sectionKey": section})
      for section in ("positioning", "premise", "characters", "world", "adaptation_rules")],
    ("structure.series_arc_index", executor._structure_series_arc_index_tool_instruction(), {}),
    ("structure.series_arc_phase", executor._structure_series_arc_phase_tool_instruction(_PHASE),
     {"phaseKey": _PHASE["key"], "phaseTitle": _PHASE["title"], "phaseObjective": _PHASE["objective"]}),
    ("structure.episode_plan_index", executor._structure_episode_plan_index_tool_instruction(), {}),
    ("structure.episode_plan_fragment", executor._structure_episode_plan_fragment_tool_instruction(_EPISODE),
     {"episodeNumber": _EPISODE["number"], "episodeId": _EPISODE["id"], "episodeTitle": _EPISODE["title"]}),
    ("structure.character_arcs_index", executor._structure_character_arcs_index_tool_instruction(), {}),
    ("structure.character_arc_fragment", executor._structure_character_arc_tool_instruction(_CHARACTER),
     {"characterKey": _CHARACTER["key"], "characterName": _CHARACTER["name"]}),
    ("scene_list_episode", executor._scene_list_fragment_tool_instruction(3), {"episodeNumber": 3}),
]


@pytest.mark.parametrize(
    ("part_key", "prompt", "identity"),
    _CANDIDATE_PROMPTS,
    ids=[case[0] for case in _CANDIDATE_PROMPTS],
)
def test_prompt_candidate_example_satisfies_its_runtime_validator(part_key, prompt, identity):
    examples = [line for line in prompt.splitlines() if line.startswith('{"')]
    assert len(examples) == 1
    payload = json.loads(examples[0])
    candidate = {"contentText": payload.pop("contentText", ""), "payload": payload}
    normalized = executor.normalize_screenplay_candidate(
        {"protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
         "kind": PART_CONTRACTS[part_key].validation_kind, **identity},
        candidate,
    )
    assert normalized["payload"]


_ALL_PROMPTS = [(key, prompt) for key, prompt, _ in _CANDIDATE_PROMPTS] + [
    ("draft_scene", executor._scene_tool_instruction(3, _SCENE_IDS[0])),
    ("final_response", executor._final_response_instruction()),
]


@pytest.mark.parametrize(
    ("prompt", "required_instruction"),
    (
        (
            executor._scene_tool_instruction(3, _SCENE_IDS[0]),
            "先调用 getScreenplaySceneContext",
        ),
        (
            executor._review_dimension_tool_instruction(
                3,
                "continuity",
                _SCENE_IDS,
            ),
            "先调用 getScreenplayEpisodeContext 读取当前集材料",
        ),
        (
            executor._source_chapter_digest_tool_instruction(
                chapter_id=_CHAPTER_ID,
                chapter_title="第一章",
                chapter_index=1,
            ),
            "先调用 readSourceChapters 读取本章正文",
        ),
        (
            executor._scene_list_fragment_tool_instruction(3),
            "先调用 readScreenplayDeliverable 读取当前集结构",
        ),
    ),
)
def test_body_dependent_parts_require_an_explicit_read(
    prompt,
    required_instruction,
):
    assert required_instruction in prompt


@pytest.mark.parametrize(
    ("part_key", "prompt", "source_book_id"),
    [
        pytest.param(key, prompt, source_book_id,
                     id=f"{key}:{'adaptation' if source_book_id else 'original'}")
        for key, prompt in _ALL_PROMPTS
        for source_book_id in (
            ("source-book",) if key == "source_analysis.chapter_digest"
            else (None, "source-book")
        )
    ],
)
def test_prompt_tool_references_are_enabled_for_its_part(part_key, prompt, source_book_id):
    async def unused_handler(*args):
        raise AssertionError("Tool contract inspection must not execute a tool")

    role = {
        "source_analysis": "sourceAnalysis", "creative_brief": "creativeBrief",
        "structure": "structure", "review_dimension": "review",
        "scene_list_episode": "sceneList",
    }.get(part_key.split(".")[0], "screenplayDraft")
    context = ScreenplayAgentDomainContext(
        project_id="prompt-project", task_id="prompt-task", unit_id="prompt-unit",
        target_role=role, expected_part_type="document_section", expected_part_key=part_key,
        tool_access=PART_CONTRACTS[part_key].tool_profile,
        source_book_id=source_book_id, source_scope={"mode": "whole_book"},
    )
    request = AgentRunRequest(
        messages=(), model=ModelRequest(provider="openai", model="contract-test"),
        domain_context=context.to_core_context(), tools_enabled=True,
    )
    catalog = build_screenplay_tool_catalog(
        handlers={name: unused_handler for name in SCREENPLAY_TOOL_SCHEMAS},
    )
    referenced = {name for name in SCREENPLAY_TOOL_SCHEMAS if name in prompt}
    assert referenced <= catalog.enabled_names(request)

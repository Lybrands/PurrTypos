import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.dispatcher import NovelAnalysisReplacementDescriptorResolver
from agents.novel_analysis.map_execution import (
    ScalableChildRunError,
    raise_child_run_failure,
)
from agents.novel_analysis.skill_creation import (
    ScalableSkillOutputError,
    ScalableSkillUnitExecutor,
)
from application.writing_technique_service import WritingTechniqueService
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import RunCreateParams, SqliteRunRepository
from purra.json_values import canonical_json_digest
from purra.long_tasks import BudgetExhaustionDisposition
from purra.recovery import FailureCategory, FailureDisposition, decide_failure


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _commit(db, unit_id, run_id, payload):
    return await NovelAnalysisAttemptArtifactStore(db).commit(
        task_id="task",
        unit_id=unit_id,
        attempt=1,
        operation_id=f"task:{unit_id}:1",
        run_id=run_id,
        payload=payload,
    )


async def _context(db):
    await db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, completed_units) "
        "VALUES ('task', 'purrtypos.novel_analysis', 'novel_analysis.scalable.v2', "
        "'revision-1', 'root-run', 'running', 2, 1)"
    )
    synthesis_payload = {
        "schemaVersion": 3,
        "kind": "synthesize",
        "childRunId": "synthesis-child",
        "inputArtifactIds": ["reduce-artifact"],
        "coveredSliceIds": ["slice-0"],
        "summaryMarkdown": "整书总结",
        "facts": [{
            "id": "fact-background", "claimNature": "summary",
            "factKind": "background", "subjectKey": "故事背景",
            "predicate": "背景归纳", "value": {"content": "旧城"},
            "lifecycleStatus": "active",
        }],
        "craftCards": [{
            "id": "craft-pressure", "cardKind": "technique",
            "title": "持续压力", "bodyMarkdown": "让环境持续影响人物选择。",
        }],
    }
    synthesis = await _commit(db, "synthesize:whole-work", "synthesis-child", synthesis_payload)
    coverage_payload = {
        "schemaVersion": 1, "kind": "coverage", "covered": True,
        "summaryMarkdownPresent": True, "expectedSliceIds": ["slice-0"],
        "expectedPassIds": ["craft"], "passRoots": [],
        "synthesisArtifactId": synthesis.artifact_id,
        "synthesisDigest": canonical_json_digest(synthesis_payload),
    }
    coverage = await _commit(db, "coverage:gate", "root-run", coverage_payload)
    return SimpleNamespace(
        task=SimpleNamespace(id="task"),
        unit=SimpleNamespace(
            id="skill:create", attempt=1, dependencies=("coverage:gate",),
            metadata={"unitKind": "skill"},
        ),
        dependency_outputs={"coverage:gate": coverage.resource_ref},
        run_id="root-run",
    )


@dataclass
class _Runner:
    db: object
    calls: int = 0

    async def run(self, *, scope, context, signal=None):
        del scope, signal
        self.calls += 1
        await SqliteRunRepository(self.db).create(RunCreateParams(
            session_id=None,
            prompt="create Skill",
            mode="novel_analysis_skill",
            requested_run_id="skill-child",
            root_run_id=context.run_id,
            parent_run_id=context.run_id,
            agent_id="skill-child",
        ))
        return "skill-child", {
            "files": [{
                "path": "SKILL.md",
                "content": "---\nname: 持续压力\ndescription: 用环境压力推动人物选择。\nmetadata:\n  retrieval:\n    intents: [维持场景压力]\n    contexts: [人物必须在持续变化的外部压力下作出选择]\n    objectives: [让冲突持续影响人物行动]\n    keywords: [环境压力, 持续冲突]\n    exclusions: [只需要一次性突发事件的场景]\n---\n\n按情境调整持续压力。\n\n[变化方式](references/变化方式.md)",
            }, {
                "path": "references/变化方式.md",
                "content": "让压力随人物的选择发生变化。",
            }],
            "evidenceRefs": ["craft-pressure"],
            "scopeNotes": [],
        }


@pytest.mark.asyncio
async def test_skill_unit_materializes_one_editable_multifile_candidate(db):
    await SqliteRunRepository(db).create(RunCreateParams(
        session_id=None, prompt="root", mode="analysis",
        requested_run_id="root-run", agent_id="root-run",
    ))
    runner = _Runner(db)
    executor = ScalableSkillUnitExecutor(db, child_runner=runner)
    result = await executor.execute(await _context(db))
    payload = await NovelAnalysisAttemptArtifactStore(db).load_payload(result.metadata["artifactId"])
    candidate = payload["techniqueResult"]["candidate"]
    manifest = await asyncio.to_thread(
        WritingTechniqueService(db).techniques.get_version_manifest,
        {"kind": "technique", "id": candidate["techniqueId"], "versionId": candidate["versionId"]},
    )

    assert runner.calls == 1
    assert [item["path"] for item in manifest["files"]] == [
        "SKILL.md", "references/变化方式.md",
    ]
    assert payload["techniqueResult"]["evidenceRefs"] == ["craft-pressure"]
    assert manifest["metadata"]["retrieval"] == {
        "intents": ["维持场景压力"],
        "contexts": ["人物必须在持续变化的外部压力下作出选择"],
        "objectives": ["让冲突持续影响人物行动"],
        "keywords": ["环境压力", "持续冲突"],
        "exclusions": ["只需要一次性突发事件的场景"],
    }


def test_skill_model_output_failure_is_not_a_provider_pause() -> None:
    executor = ScalableSkillUnitExecutor.__new__(ScalableSkillUnitExecutor)

    signal = executor.classify_failure(ScalableSkillOutputError("bad output"))

    assert signal.category is FailureCategory.MODEL_OUTPUT_INVALID
    assert signal.retryable is True
    assert decide_failure(
        signal,
        attempts_remaining=0,
    ).disposition is FailureDisposition.FAIL_PERMANENT


def test_child_failure_preserves_its_error_for_root_settlement() -> None:
    aggregation = SimpleNamespace(
        results=({
            "runId": "child-run",
            "status": "failed",
            "errorCode": "max_model_rounds",
        },),
    )

    with pytest.raises(ScalableChildRunError) as failure:
        raise_child_run_failure(aggregation, "child-run", "Skill")

    assert failure.value.code == "max_model_rounds"
    executor = ScalableSkillUnitExecutor.__new__(ScalableSkillUnitExecutor)
    signal = executor.classify_failure(failure.value)
    assert signal.category is FailureCategory.TOOL_EXECUTION
    assert signal.retryable is True
    assert decide_failure(signal, attempts_remaining=0).disposition is (
        FailureDisposition.FAIL_PERMANENT
    )


@pytest.mark.asyncio
async def test_analysis_budget_exhaustion_is_terminal_not_paused() -> None:
    descriptor = await NovelAnalysisReplacementDescriptorResolver().resolve(
        None,
        None,
        SimpleNamespace(metadata={
            "sourceRevisionId": "revision-1",
            "commandId": "command-1",
            "modelAttemptBudget": 2,
        }),
    )

    assert descriptor.budget_exhaustion_disposition is (
        BudgetExhaustionDisposition.FAIL_PERMANENT
    )

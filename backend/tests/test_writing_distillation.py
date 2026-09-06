from copy import deepcopy
import json
import pytest

from domains.writing_distillation import (
    normalize_distillation, validate_report, validate_skill, render_skill,
    assessment_passed,
)
from domains.novel_analysis import compile_novel_analysis_recipe
from tests.support.writing_distillation import report, skill, assessment


def observations():
    return [{"contentDigest": "observation-1", "evidence": [{"excerpt": "这是来源中不能复制的完整句子。"}]}]


def test_distillation_uses_global_observations_and_tests_the_revised_skill():
    recipe = compile_novel_analysis_recipe(section_ids=['s1', 's2'], plan_step_ids=['a', 'b', 'c'])
    stages = {step.id: step for step in recipe.steps}
    assert stages['validate:evidence'].depends_on[-1] == 'aggregate:story'
    assert stages['skill:trial'].depends_on == ('skill:draft',)
    assert stages['skill:retrial'].depends_on == ('skill:revise',)
    assert stages['skill:assess'].depends_on == ('validate:evidence', 'skill:revise', 'skill:retrial')


def test_skill_cannot_reference_unread_or_removed_observations():
    value = skill(observations())
    value['evidenceIds'] = ['invented']
    with pytest.raises(ValueError, match='validated source'):
        validate_skill(value, observations())


def test_source_excerpt_is_rejected_at_skill_boundary():
    value = skill(observations())
    value['procedure'][0]['action'] = observations()[0]['evidence'][0]['excerpt']
    with pytest.raises(ValueError, match='source excerpt'):
        validate_skill(value, observations())


def test_edit_after_transfer_test_invalidates_receipt():
    value = report(observations())
    validate_report(value, observations())
    value['writingSkill']['procedure'][0]['action'] = '变更后的操作'
    with pytest.raises(ValueError, match='changed after'):
        validate_report(value, observations())


def test_quality_checks_cannot_skip_or_duplicate_dimension():
    value = assessment()
    value['checks'][-1] = deepcopy(value['checks'][0])
    with pytest.raises(ValueError, match='each quality dimension'):
        normalize_distillation('assess_skill', value)
    assert not assessment_passed(assessment(False))


def test_transfer_must_exercise_all_steps():
    value = report(observations())
    value['transferTrials']['trials'][1]['stepApplications'][0]['step'] = 2
    with pytest.raises(ValueError, match='every skill step'):
        validate_report(value, observations())


def test_skill_body_excludes_test_and_evidence_records():
    value = report(observations())
    body = render_skill(value['writingSkill'])
    assert '电梯' not in body and 'observation-1' not in body
    assert '执行方法' in body and '适用边界' in body


async def test_rejected_assessment_cannot_create_method(db):
    from application.writing_method_candidates import WritingMethodCandidateService
    from domains.writing.methods import WritingMethodConflictError
    await db.execute("INSERT INTO novel_source_analyses (id, source_revision_id, version_no, coverage_end_ordinal, schema_version, content_digest, summary_json) VALUES ('a', 'r', 1, 0, 2, 'digest', ?)", [json.dumps({'distillation': report(observations(), False)})])
    with pytest.raises(WritingMethodConflictError, match='迁移复核'):
        await WritingMethodCandidateService(db).create_from_analysis('a')
    assert await db.fetch_one("SELECT COUNT(*) AS count FROM writing_methods WHERE source_type='analysis_candidate'") == {'count': 0}


@pytest.fixture
async def db(tmp_path):
    from database.connection import DatabaseConnection
    value = DatabaseConnection(tmp_path)
    await value.init()
    try:
        yield value
    finally:
        await value.close()

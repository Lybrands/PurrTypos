from unittest.mock import AsyncMock
import pytest
from database.connection import DatabaseConnection
from tests.test_novel_analysis import _source
from application.novel_analysis_executor import NovelAnalysisTaskUnitExecutor, _normalize_candidates
from application.novel_analysis_service import NovelAnalysisService
from application.analysis_provenance import validate_policy
from application.analysis_evidence_references import reference_projection, restore_references
from application.novel_analysis_source import AnalysisEvidenceInputError
from application.continuation_service import ContinuationService


def summary(sections):
    return {'factKind':'background','subjectKey':'环境','predicate':'概述','value':'## 背景\n\n综合描述。','claimNature':'summary','lifecycleStatus':'active','evidence':[{'referenceKind':'chapter','sectionId':s} for s in sections]}


async def test_chapter_summary_publishes_and_preserves_fork_scope_without_quote_matching(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        revision = await _source(db)
        sections = [s['id'] for s in revision['sections']]
        executor = NovelAnalysisTaskUnitExecutor(db)
        executor._source.validate_excerpt = AsyncMock(side_effect=AssertionError('chapter summaries must not match quotes'))
        normalized = _normalize_candidates({'facts':[summary(sections)], 'craftCards':[]})
        validated = await executor._validate_candidates(normalized,revision_id=revision['id'],section_ids=sections)
        executor._source.validate_excerpt.assert_not_awaited()
        frozen = {'sourceRevisionId':revision['id'],**validated}
        projected = reference_projection(validated,revision['id'],payload=frozen)
        assert restore_references(projected,frozen) == validated
        service = NovelAnalysisService(db)
        service._source.validate_excerpt = AsyncMock(side_effect=AssertionError('publication must not match chapter quotes'))
        payload = await service._validated_publish_payload({**validated,'analysisSchemaVersion':3,'sourceRevisionId':revision['id'],'sectionIds':sections,
            'techniqueResult':{'status':'insufficient_material','reason':'fixture','scopeNotes':[],'evidenceRefs':[]}})
        published = await service._publish_transaction(payload,artifact_id='fixture')
        assert published['facts'][0]['claimNature'] == 'summary'
        assert all(e['referenceKind']=='chapter' and e['excerpt']=='' for e in published['facts'][0]['evidence'])
        service._source.validate_excerpt.assert_not_awaited()
        continuation = ContinuationService(db)
        early = await continuation.preview_canon(source_revision_id=revision['id'],source_analysis_id=published['id'],fork_section_id=sections[0])
        assert not early['records']
        late = await continuation.preview_canon(source_revision_id=revision['id'],source_analysis_id=published['id'],fork_section_id=sections[-1])
        assert late['records'][0]['claimNature']=='summary'
        with pytest.raises((AnalysisEvidenceInputError, PermissionError)):
            await executor._validate_candidates({'facts':[summary(['other'])]},revision_id=revision['id'],section_ids=sections)
    finally: await db.close()


@pytest.mark.parametrize('kind', ['character_identity','character_knowledge','relationship','world_rule','event','foreshadowing'])
def test_critical_claims_cannot_downgrade_to_chapter_reference(kind):
    with pytest.raises(AnalysisEvidenceInputError): validate_policy({**summary(['s']), 'factKind':kind})


def test_creative_proposal_cannot_masquerade_as_source_fact():
    with pytest.raises(AnalysisEvidenceInputError): validate_policy({**summary(['s']), 'claimNature':'proposal'})
    with pytest.raises(AnalysisEvidenceInputError): validate_policy({**summary(['s']), 'claimNature':'fact'})


def test_new_creative_material_records_origin_without_source_evidence():
    from infrastructure.obsidian.material_document import create, decode
    raw = create('background-id','background',{'content':'下一卷可以增加一座城市。'})
    document = decode(raw, '背景.md')
    assert document['metadata']['purr_origin'] == 'creation'
    assert '下一卷可以增加一座城市' in document['body']


def test_ordinary_character_and_relationship_summaries_allow_chapter_sources():
    for kind in ('character_summary','relationship_summary'):
        validate_policy({**summary(['s']), 'factKind':kind})
    for kind in ('character_state','relationship','character_knowledge'):
        with pytest.raises(AnalysisEvidenceInputError):
            validate_policy({**summary(['s']), 'factKind':kind})

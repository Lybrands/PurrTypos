import pytest
from application.analysis_evidence_references import evidence_id, evidence_index, reference_projection, restore_references
from application.writing_technique_generation_tools import project_unit_input


def payload(revision='r'):
    return {'sourceRevisionId': revision, 'sectionCandidates': [{'facts': [
        {'subjectKey': '甲', 'evidence': [{'sectionId': 's', 'excerpt': '甲离开了。', 'segmentStartCharacter': 0, 'segmentEndCharacter': 12}]},
        {'subjectKey': '乙', 'evidence': [{'sectionId': 's', 'excerpt': '甲离开了。', 'segmentStartCharacter': 0, 'segmentEndCharacter': 12}]},
    ]}]}


def test_references_reuse_evidence_without_repeating_excerpt_and_round_trip():
    source = payload()
    projected = reference_projection(source, 'r')
    assert len(evidence_index(source)) == 1
    assert '甲离开了。' not in str(projected)
    assert restore_references(projected, source) == source
    assert project_unit_input(source)['sectionCandidates'] == projected['sectionCandidates']


def test_foreign_and_forged_references_fail_closed():
    source = payload()
    key = next(iter(evidence_index(source)))
    for value in [{'evidenceId': key, 'excerpt': '捏造'}, {'evidenceId': 'missing'}]:
        with pytest.raises(ValueError):
            restore_references({'evidence': [value]}, source)
    with pytest.raises(ValueError):
        restore_references({'evidence': [{'evidenceId': key}]}, payload('other'))


def test_repeated_text_in_different_segments_has_distinct_identity():
    source = payload()
    source['sectionCandidates'][0]['facts'][1]['evidence'][0]['segmentStartCharacter'] = 6
    assert len(evidence_index(source)) == 2


def test_evidence_identity_survives_host_validation_metadata():
    raw = payload()['sectionCandidates'][0]['facts'][0]['evidence'][0]
    validated = {**raw, 'locator': {'start': 0, 'end': 5}, 'sectionOrdinal': 1, 'excerptDigest': 'digest'}
    assert evidence_id('r', raw) == evidence_id('r', validated)


async def test_repeated_quote_requires_unambiguous_bound_range(monkeypatch):
    from application.novel_analysis_source import NovelAnalysisSourceReader
    reader = NovelAnalysisSourceReader(None)
    async def section(**kwargs):
        return {'id': 's', 'ordinal': 0, 'text': '甲来了。甲来了。', 'contentDigest': 'digest'}
    monkeypatch.setattr(reader, 'read_section', section)
    args = dict(source_revision_id='r', bound_section_ids=['s'], section_id='s', excerpt='甲来了。')
    with pytest.raises(ValueError, match='ambiguous'):
        await reader.validate_excerpt(**args)
    matched = await reader.validate_excerpt(**args, start_character=4, end_character=8)
    assert matched['locator'] == {'start': 4, 'end': 8}


def test_normalization_preserves_unreturned_facts_and_chapter_boundaries():
    from application.novel_analysis_executor import _combine_candidates, _preserve_fact_scopes
    first = {'factKind': 'character_state', 'subjectKey': '甲', 'predicate': '伤势', 'value': '受伤',
             'evidence': [{'sectionId': 's1', 'excerpt': '甲受伤。'}]}
    later = {**first, 'evidence': [{'sectionId': 's2', 'excerpt': '甲仍受伤。'}]}
    source = [{'facts': [first, later]}]
    assert len(_combine_candidates(source)['facts']) == 2
    assert _preserve_fact_scopes([], source) == [first, later]
    merged = {**first, 'subjectKey': '甲的全名', 'evidence': first['evidence'] + later['evidence']}
    restored = _preserve_fact_scopes([merged], source)
    assert [r['subjectKey'] for r in restored] == ['甲的全名', '甲的全名']
    assert restored[0]['evidence'] == first['evidence']
    assert restored[1]['evidence'] == later['evidence']


async def test_layout_matching_restores_source_and_rejects_rewrites(monkeypatch):
    from application.novel_analysis_source import NovelAnalysisSourceReader
    from domains.novel_analysis import canonical_digest
    reader = NovelAnalysisSourceReader(None)
    text = '前言。他是人类。\r\n\t今天回来。结尾。'
    async def section(**kwargs):
        return {'id': 's', 'ordinal': 0, 'text': text, 'contentDigest': 'digest'}
    monkeypatch.setattr(reader, 'read_section', section)
    args = dict(source_revision_id='r', bound_section_ids=['s'], section_id='s')
    receipt = await reader.validate_excerpt(**args, excerpt='他是人类。今天回来。')
    assert receipt['excerpt'] == '他是人类。\r\n\t今天回来。'
    assert text[receipt['locator']['start']:receipt['locator']['end']] == receipt['excerpt']
    assert receipt['excerptDigest'] == canonical_digest(receipt['excerpt'])
    for quote in ('我是人类。今天回来。', '他是人类。现在回来。', '他是人类，今天回来。'):
        with pytest.raises(ValueError, match='does not exist'):
            await reader.validate_excerpt(**args, excerpt=quote)
    with pytest.raises(ValueError, match='does not exist'):
        await reader.validate_excerpt(**args, excerpt='他是人类。今天回来。', end_character=10)


async def test_layout_equivalent_duplicates_are_ambiguous(monkeypatch):
    from application.novel_analysis_source import NovelAnalysisSourceReader
    reader = NovelAnalysisSourceReader(None)
    async def section(**kwargs):
        return {'id': 's', 'ordinal': 0, 'text': '甲来了。甲\n来了。', 'contentDigest': 'digest'}
    monkeypatch.setattr(reader, 'read_section', section)
    with pytest.raises(ValueError, match='ambiguous'):
        await reader.validate_excerpt(source_revision_id='r', bound_section_ids=['s'], section_id='s', excerpt='甲来了。')


def test_host_scope_precedes_model_position_validation():
    from application.novel_analysis_executor import bind_model_candidate_scope, _normalize_candidates, _restore_evidence_scopes
    raw = {'facts': [{'factKind': 'event', 'subjectKey': '甲', 'predicate': '说', 'value': '好',
        'evidence': [{'sectionId': 's', 'excerpt': '甲说。好。', 'segmentStartCharacter': 999}]}], 'craftCards': []}
    binding = {'segmentId': 'host', 'startCharacter': 0, 'endCharacter': 20}
    normalized = _normalize_candidates(bind_model_candidate_scope(raw, binding))
    assert normalized['facts'][0]['evidence'][0]['segmentEndCharacter'] == 20
    assert normalized['facts'][0]['evidence'][0]['segmentStartCharacter'] == 0
    unbound = _normalize_candidates(bind_model_candidate_scope(raw))
    dependencies = [{'facts': [{'evidence': [{'sectionId': 's', 'excerpt': '甲说。\n好。',
        'segmentId': 'host', 'segmentStartCharacter': 0, 'segmentEndCharacter': 20}]}]}]
    restored = _restore_evidence_scopes(unbound, dependencies, required=True)
    assert restored['facts'][0]['evidence'][0]['segmentId'] == 'host'
    assert raw['facts'][0]['evidence'][0]['segmentStartCharacter'] == 999


def test_short_references_are_stable_for_subsets_and_scoped_to_frozen_evidence():
    from application.analysis_evidence_references import model_evidence_index
    from copy import deepcopy
    source = payload()
    other = deepcopy(source['sectionCandidates'][0]['facts'][0])
    other['evidence'][0]['excerpt'] = '乙回来了。'
    source['sectionCandidates'][0]['facts'].append(other)
    index = model_evidence_index(source)
    assert len(index) == 2 and all(len(key) < 16 and key.startswith('E') for key in index)
    projected = reference_projection(source, 'r', payload=source)
    subset = reference_projection(other, 'r', payload=source)
    assert subset == projected['sectionCandidates'][0]['facts'][-1]
    assert restore_references(subset, source) == other
    reordered = deepcopy(source)
    reordered['sectionCandidates'][0]['facts'].reverse()
    assert model_evidence_index(reordered) == index
    key = subset['evidence'][0]['evidenceId']
    with pytest.raises(ValueError):
        restore_references({'evidence': [{'evidenceId': key}]}, payload('another-revision'))
    with pytest.raises(ValueError, match='listAnalysisEvidence'):
        restore_references({'evidence': [{'evidenceId': key + '9'}]}, source)


def test_observation_handles_restore_for_merge_and_technique_without_cross_scope_aliasing():
    from application.analysis_observation_references import observation_index, restore_observation_references
    from copy import deepcopy
    source = {'sourceRevisionId': 'r', 'observations': [
        {'contentDigest': 'digest-b', 'evidence': []}, {'contentDigest': 'digest-a', 'evidence': []}]}
    index = observation_index(source)
    handles = list(index)
    model = {'craftCards': [{'mergedObservationIds': handles}], 'evidenceRefs': handles}
    restored = restore_observation_references(model, source)
    assert restored['evidenceRefs'] == ['digest-a', 'digest-b']
    assert restored['craftCards'][0]['mergedObservationIds'] == restored['evidenceRefs']
    reordered = deepcopy(source)
    reordered['observations'].reverse()
    assert observation_index(reordered) == index
    with pytest.raises(ValueError, match='listAnalysisObservations'):
        restore_observation_references(model, {**source, 'sourceRevisionId': 'other'})
    assert model['evidenceRefs'] == handles

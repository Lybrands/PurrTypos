from copy import deepcopy
from types import SimpleNamespace

import pytest

from application.analysis_source_spans import annotate_source, expand_spans, find_spans, span_id
from application.novel_analysis_source import AnalysisEvidenceInputError, NovelAnalysisSourceReader


@pytest.fixture
def source_scope(monkeypatch):
    prefix = '片段之外。\n'
    body = '甲' * 159 + '门开了。\n' + '同一句。\n同一句。\n' + ''.join(f'第{i}行原文。\n' for i in range(20))
    row = {'id': 'section-one', 'revisionId': 'revision-one', 'text': prefix + body + '范围之外。'}
    payload = {'sourceBinding': {'sourceRevisionId': row['revisionId'], 'sectionId': row['id'],
        'startCharacter': len(prefix), 'endCharacter': len(prefix + body)}, 'sourceEvidence': {'text': body}}
    async def read(self, **kwargs):
        assert kwargs['source_revision_id'] == row['revisionId']
        assert kwargs['bound_section_ids'] == [row['id']]
        assert kwargs['section_id'] == row['id']
        return deepcopy(row)
    monkeypatch.setattr(NovelAnalysisSourceReader, 'read_section', read)
    return row, SimpleNamespace(domain={'sourceRevisionId': row['revisionId'], 'unitInput': payload})


async def test_short_handles_shared_by_input_search_pagination_and_reconstructed_state(source_scope):
    row, state = source_scope
    payload = deepcopy(state.domain['unitInput'])
    public = annotate_source(payload)['sourceEvidence']['excerpts']
    assert public[0]['sourceSpanId'] == 'S001'
    assert len(public) > 8 and all(len(item['sourceSpanId']) == 4 for item in public)
    assert state.domain['unitInput'] == payload
    page, offset = [], 0
    while offset is not None:
        result = await find_spans(None, state, offset=offset, limit=5)
        page.extend(result['items'])
        offset = result['nextOffset']
    assert page == public
    found = await find_spans(None, deepcopy(state), '门开')
    assert found['items'] == public[:2], 'cross-boundary queries return stable original handles'
    for item in public:
        result = await expand_spans(None, deepcopy(state), {'facts': [{'evidence': [{'sourceSpanId': item['sourceSpanId']}]}]})
        evidence = result['facts'][0]['evidence'][0]
        assert evidence['excerpt'] == item['excerpt']
        assert row['text'][evidence['segmentStartCharacter']:evidence['segmentEndCharacter']] == item['excerpt']
        assert 'sourceSpanId' not in evidence
    repeated = await find_spans(None, state, '同一句')
    assert len(repeated['items']) == 2
    assert repeated['items'][0]['sourceSpanId'] != repeated['items'][1]['sourceSpanId']
    # Search order and misses do not change identifiers.
    await find_spans(None, state, '不在原文中的内容')
    assert (await find_spans(None, state, '门开'))['items'] == public[:2]


async def test_legacy_handles_remain_strict_and_return_current_recovery_candidates(source_scope):
    row, state = source_scope
    start = state.domain['unitInput']['sourceBinding']['startCharacter']
    end = start + 5
    legacy = span_id(row['revisionId'], row['id'], start, end, row['text'][start:end])
    value = {'facts': [{'evidence': [{'sourceSpanId': legacy}]}]}
    assert (await expand_spans(None, state, value))['facts'][0]['evidence'][0]['excerpt'] == row['text'][start:end]
    wrong = legacy[:-1] + ('0' if legacy[-1] != '0' else '1')
    with pytest.raises(AnalysisEvidenceInputError, match='S001:'):
        await expand_spans(None, state, {'facts': [{'evidence': [{'sourceSpanId': wrong, 'excerpt': row['text'][start:end]}]}]})
    for handle in ['S999', 'S1', 'S001-guess', span_id('other-revision', row['id'], start, end, row['text'][start:end])]:
        with pytest.raises(AnalysisEvidenceInputError, match='findAnalysisSourceEvidence'):
            await expand_spans(None, state, {'craftCards': [{'evidence': [{'sourceSpanId': handle}]}]})
    # A valid identity outside the authorized segment is still rejected.
    outside = span_id(row['revisionId'], row['id'], 0, 5, row['text'][:5])
    with pytest.raises(AnalysisEvidenceInputError):
        await expand_spans(None, state, {'facts': [{'evidence': [{'sourceSpanId': outside}]}]})


async def test_short_handles_cannot_reinterpret_changed_source_or_foreign_scope(source_scope):
    _, state = source_scope
    value = {'facts': [{'evidence': [{'sourceSpanId': 'S001'}]}]}
    for mutate in ('text', 'revision', 'range'):
        changed = deepcopy(state)
        if mutate == 'text':
            changed.domain['unitInput']['sourceEvidence']['text'] += '内容改变'
        elif mutate == 'revision':
            changed.domain['unitInput']['sourceBinding']['sourceRevisionId'] = 'other-version'
        else:
            changed.domain['unitInput']['sourceBinding']['endCharacter'] += 1000
        with pytest.raises(AnalysisEvidenceInputError):
            await expand_spans(None, changed, value)
        with pytest.raises(AnalysisEvidenceInputError):
            await find_spans(None, changed)
    with pytest.raises(AnalysisEvidenceInputError, match='章节'):
        await expand_spans(None, state, {'facts': [{'evidence': [{'sourceSpanId': 'S001', 'sectionId': 'foreign'}]}]})


def test_repeated_fact_defaults_match_saved_batch_without_merging_inferences():
    from application.novel_analysis_executor import _combine_candidates
    raw = {'factKind': 'event', 'subjectKey': '甲', 'predicate': '开门', 'value': '门开了',
           'evidence': [{'sectionId': 's', 'excerpt': '门开了。'}]}
    saved = {**raw, 'claimNature': 'fact', 'lifecycleStatus': 'active'}
    inferred = {**raw, 'claimNature': 'inference'}
    result = _combine_candidates([{'facts': [saved]}, {'facts': [raw, inferred]}])
    assert len(result['facts']) == 2
    assert {item['claimNature'] for item in result['facts']} == {'fact', 'inference'}

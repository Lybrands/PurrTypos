import pytest

from application.observation_grounding import evidence_context, quoted_text_outside_evidence
from domains.writing.techniques import TechniqueError


def test_quote_diagnostic_exposes_unlinked_literal_without_semantic_verdict():
    observation = {'bodyMarkdown': '顾遥以“屏住气”结束，随后出现“车铃”。',
                   'evidence': [{'excerpt': '她终于觉得羞愧。'}, {'excerpt': '巷口传来车铃。'}]}
    assert quoted_text_outside_evidence(observation) == ['屏住气']
    assert 'supportStatus' not in observation


def test_context_never_expands_outside_authorized_segment():
    text = '不在选区的前文' + '她终于觉得羞愧。另一边，岑野屏住气。' + '不在选区的后文'
    lower, upper = len('不在选区的前文'), len(text) - len('不在选区的后文')
    start = text.index('屏住气')
    result = evidence_context({'sectionId': 's', 'excerpt': '屏住气',
        'locator': {'start': start, 'end': start + 3},
        'segmentStartCharacter': lower, 'segmentEndCharacter': upper}, text)
    assert result['text'] == text[lower:upper]
    assert result['startCharacter'] == lower and result['endCharacter'] == upper
    assert result['excerptStartCharacter'] == start


@pytest.mark.parametrize('locator', [{'start': 0, 'end': 3}, {'start': -1, 'end': 2}, {'start': True, 'end': 3}])
def test_mismatched_locator_is_not_silently_replaced(locator):
    with pytest.raises(TechniqueError):
        evidence_context({'sectionId': 's', 'excerpt': '屏住气', 'locator': locator}, '岑野屏住气。')


def test_unlocated_legacy_quote_is_searched_only_inside_bound_range():
    text = '屏住气。前段结束。岑野屏住气。'
    lower = text.index('岑野')
    result = evidence_context({'sectionId': 's', 'excerpt': '屏住气',
        'segmentStartCharacter': lower, 'segmentEndCharacter': len(text)}, text)
    assert result['excerptStartCharacter'] == text.rindex('屏住气')


def test_context_size_is_bounded():
    text = '前' * 1000 + '证据' + '后' * 1000
    result = evidence_context({'sectionId': 's', 'excerpt': '证据'}, text)
    assert len(result['text']) == 242
    assert result['startCharacter'] == 880

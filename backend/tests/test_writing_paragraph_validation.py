import pytest

from domains.writing.paragraph_validation import SingleProseParagraphValidator, requests_single_prose_paragraph


@pytest.mark.parametrize("text,expected", [
    ("写一段安静回忆：退役跑步选手整理旧号码布，直到段末才承认自己仍不甘心。", True),
    ("写一段送信员躲开追赶的场景。", True),
    ("写1段回忆，不要分段。", True),
    ("写一段回忆，再写一段追逐场景。", False),
    ("写一段回忆，分段展示。", False),
    ("写一段代码，生成回忆场景。", False),
    ("分析这段故事的节奏。", False),
    ("写三段回忆。", False),
])
def test_only_unambiguous_prose_requests_activate_paragraph_contract(text, expected):
    assert requests_single_prose_paragraph(text) is expected


def test_multiline_wrap_is_allowed_but_blank_line_paragraphs_require_repair():
    validator = SingleProseParagraphValidator()
    assert not validator.validate(content="他拿起号码布。\n他终于承认不甘心。", messages=()).violation_code
    result = validator.validate(content="他拿起号码布。\n\n他终于承认不甘心。", messages=())
    assert result.violation_code == "writing_paragraph_count_mismatch"


def test_production_response_contract_installs_paragraph_validation():
    from domains.writing.response import writing_response_validators
    from tests.test_writing_domain_adapter import _request
    request = _request(user_text="写一段安静回忆，直到段末才承认不甘心。")
    assert any(isinstance(item, SingleProseParagraphValidator) for item in writing_response_validators(request))

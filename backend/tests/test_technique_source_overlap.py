from application.technique_source_overlap import source_overlap


def test_overlap_detects_copied_prose_across_punctuation_and_line_breaks():
    excerpt = "".join(chr(0x4e00 + i) for i in range(40))
    observations = [{"evidence": [{"excerpt": excerpt[:20] + "。" + excerpt[20:]}]}]
    copied = "# 示例\n\n" + excerpt[:17] + "\n" + excerpt[17:]
    assert source_overlap({"SKILL.md": copied}, observations) == [
        {"path": "SKILL.md", "line": 3, "match": excerpt[:32]}]


def test_short_craft_phrases_and_new_examples_do_not_trigger_overlap():
    observations = [{"evidence": [{"excerpt": "屏住气。门还开着。"}]}]
    assert source_overlap({"SKILL.md": "用短句写即时判断，例如：屏住气。门还开着。"}, observations) == []


def test_evidence_boundaries_are_not_joined_to_invent_a_match():
    text = "".join(chr(0x4e00 + i) for i in range(40))
    observations = [{"evidence": [{"excerpt": text[:20]}, {"excerpt": text[20:]}]}]
    assert source_overlap({"SKILL.md": text}, observations) == []

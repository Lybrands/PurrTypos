"""
writing_rules 行为快照（characterization tests）。

写在重构 ① 之前 —— 这些用例只描述 *现在* 的行为，**不**评判这些行为是否最优。
重构 normalize_* 时全部测试必须保持绿色，才算行为不变。
"""

from __future__ import annotations

from services.writing_rules import (
    extract_structured_json_from_model_text,
    normalize_analyze_report,
    normalize_writing_blueprint,
    normalize_draft_document,
    normalize_style_unify_result,
    normalize_polished_result,
    normalize_review_issues,
)


# ---------------------------------------------------------------------------
# extract_structured_json_from_model_text
# ---------------------------------------------------------------------------

class TestExtractStructuredJson:
    def test_pure_json_object(self):
        assert extract_structured_json_from_model_text('{"a": 1}') == {"a": 1}

    def test_pure_json_array(self):
        assert extract_structured_json_from_model_text("[1, 2, 3]") == [1, 2, 3]

    def test_markdown_fence_json(self):
        text = '```json\n{"a": 1}\n```'
        assert extract_structured_json_from_model_text(text) == {"a": 1}

    def test_markdown_fence_no_lang(self):
        text = '```\n{"a": 1}\n```'
        assert extract_structured_json_from_model_text(text) == {"a": 1}

    def test_object_embedded_in_prose(self):
        text = '前面有些废话 {"a": 1, "b": "x"} 后面还有'
        assert extract_structured_json_from_model_text(text) == {"a": 1, "b": "x"}

    def test_array_embedded_in_prose(self):
        text = "废话 [1, 2] 废话"
        assert extract_structured_json_from_model_text(text) == [1, 2]

    def test_empty_string(self):
        assert extract_structured_json_from_model_text("") is None

    def test_none_input(self):
        # 函数签名是 str，但运行时模型偶发返回非字符串；这里走的是 str(text or "") 兜底
        assert extract_structured_json_from_model_text(None) is None  # type: ignore[arg-type]

    def test_garbage_text(self):
        assert extract_structured_json_from_model_text("just plain prose") is None


# ---------------------------------------------------------------------------
# normalize_analyze_report
# ---------------------------------------------------------------------------

class TestNormalizeAnalyzeReport:
    def test_full_dict(self):
        raw = {
            "summary": "abc",
            "goals": ["g1", "g2"],
            "constraints": ["c1"],
            "risks": ["r1"],
            "evidence": [{"source": "s1", "snippet": "snip"}],
        }
        out = normalize_analyze_report(raw)
        assert out["summary"] == "abc"
        assert out["goals"] == ["g1", "g2"]
        assert out["constraints"] == ["c1"]
        assert out["risks"] == ["r1"]
        assert out["evidence"] == [{"source": "s1", "snippet": "snip"}]

    def test_summary_truncated_to_1200(self):
        raw = {"summary": "x" * 2000}
        out = normalize_analyze_report(raw)
        assert len(out["summary"]) == 1200

    def test_summary_fallback_when_missing(self):
        out = normalize_analyze_report({}, fallback_user_text="user said this")
        assert out["summary"] == "user said this"

    def test_summary_empty_when_no_fallback(self):
        out = normalize_analyze_report({})
        assert out["summary"] == ""

    def test_goals_caps_at_12(self):
        raw = {"goals": [f"g{i}" for i in range(20)]}
        out = normalize_analyze_report(raw)
        assert len(out["goals"]) == 12

    def test_goals_non_list_returns_empty(self):
        assert normalize_analyze_report({"goals": "not a list"})["goals"] == []

    def test_evidence_snippet_truncated_to_400(self):
        raw = {"evidence": [{"source": "s", "snippet": "z" * 999}]}
        out = normalize_analyze_report(raw)
        assert len(out["evidence"][0]["snippet"]) == 400

    def test_evidence_caps_at_12(self):
        raw = {"evidence": [{"source": str(i), "snippet": "x"} for i in range(20)]}
        out = normalize_analyze_report(raw)
        assert len(out["evidence"]) == 12

    def test_evidence_garbage_items_become_empty_strings(self):
        # 当前实现：非 dict 的 evidence 项 → {"source": "", "snippet": ""}
        raw = {"evidence": ["not a dict", 42]}
        out = normalize_analyze_report(raw)
        assert out["evidence"] == [
            {"source": "", "snippet": ""},
            {"source": "", "snippet": ""},
        ]

    def test_non_dict_input(self):
        out = normalize_analyze_report("not a dict")  # type: ignore[arg-type]
        assert out == {
            "summary": "",
            "goals": [],
            "constraints": [],
            "risks": [],
            "evidence": [],
        }


# ---------------------------------------------------------------------------
# normalize_writing_blueprint
# ---------------------------------------------------------------------------

class TestNormalizeWritingBlueprint:
    def test_string_beats(self):
        raw = {"chapterGoal": "goal", "beats": ["b1", "b2"], "tone": "calm"}
        out = normalize_writing_blueprint(raw)
        assert out["chapterGoal"] == "goal"
        assert out["beats"] == ["b1", "b2"]
        assert out["tone"] == "calm"

    def test_dict_beats_preserved(self):
        raw = {"beats": [{"title": "开场", "summary": "破题"}]}
        out = normalize_writing_blueprint(raw)
        assert out["beats"] == [{"title": "开场", "summary": "破题"}]

    def test_dict_beat_content_field_keeps_8000(self):
        # _normalize_beat_entry 对 key in (content/body/text/description/summary/plot) 用 8000 上限
        raw = {"beats": [{"content": "x" * 9000}]}
        out = normalize_writing_blueprint(raw)
        assert len(out["beats"][0]["content"]) == 8000

    def test_dict_beat_other_field_truncated_to_2000(self):
        raw = {"beats": [{"note": "y" * 3000}]}
        out = normalize_writing_blueprint(raw)
        assert len(out["beats"][0]["note"]) == 2000

    def test_dict_beat_with_list_subfield(self):
        raw = {"beats": [{"chars": ["a", "b", "c"]}]}
        out = normalize_writing_blueprint(raw)
        assert out["beats"][0]["chars"] == ["a", "b", "c"]

    def test_dict_beat_with_nested_dict_serialized(self):
        raw = {"beats": [{"meta": {"k": "v"}}]}
        out = normalize_writing_blueprint(raw)
        # 嵌套 dict 被 json.dumps 后塞回去
        assert isinstance(out["beats"][0]["meta"], str)
        assert "k" in out["beats"][0]["meta"] and "v" in out["beats"][0]["meta"]

    def test_dict_beat_empty_dict_becomes_none_and_dropped(self):
        raw = {"beats": [{}, {"title": "ok"}]}
        out = normalize_writing_blueprint(raw)
        assert out["beats"] == [{"title": "ok"}]

    def test_string_beat_truncated_to_3000(self):
        raw = {"beats": ["z" * 5000]}
        out = normalize_writing_blueprint(raw)
        assert len(out["beats"][0]) == 3000

    def test_beats_capped_at_20(self):
        raw = {"beats": [f"b{i}" for i in range(50)]}
        out = normalize_writing_blueprint(raw)
        assert len(out["beats"]) == 20

    def test_chapter_goal_truncated_to_1000(self):
        raw = {"chapterGoal": "g" * 2000}
        out = normalize_writing_blueprint(raw)
        assert len(out["chapterGoal"]) == 1000

    def test_required_materials_caps_at_40_and_note_at_300(self):
        raw = {"requiredMaterials": [{"type": "char", "ref": "r", "note": "n" * 1000}] * 60}
        out = normalize_writing_blueprint(raw)
        assert len(out["requiredMaterials"]) == 40
        assert len(out["requiredMaterials"][0]["note"]) == 300


# ---------------------------------------------------------------------------
# normalize_draft_document
# ---------------------------------------------------------------------------

class TestNormalizeDraftDocument:
    def test_basic(self):
        raw = {"title": "T", "content": "C", "notes": ["n1"]}
        assert normalize_draft_document(raw) == {"title": "T", "content": "C", "notes": ["n1"]}

    def test_notes_caps_at_12(self):
        raw = {"notes": [f"n{i}" for i in range(20)]}
        assert len(normalize_draft_document(raw)["notes"]) == 12

    def test_non_dict_input(self):
        assert normalize_draft_document(None) == {"title": "", "content": "", "notes": []}


# ---------------------------------------------------------------------------
# normalize_style_unify_result
# ---------------------------------------------------------------------------

class TestNormalizeStyleUnifyResult:
    def test_basic(self):
        raw = {
            "styleAnchors": "anchor",
            "content": "正文",
            "changeSummary": "改了一些",
            "priorChaptersRead": [{"chapterIndex": 3, "title": "第三章"}],
        }
        out = normalize_style_unify_result(raw)
        assert out["styleAnchors"] == "anchor"
        assert out["content"] == "正文"
        assert out["changeSummary"] == "改了一些"
        assert out["priorChaptersRead"] == [{"chapterIndex": 3, "title": "第三章"}]

    def test_style_anchors_truncated_to_4000(self):
        out = normalize_style_unify_result({"styleAnchors": "x" * 5000})
        assert len(out["styleAnchors"]) == 4000

    def test_content_truncated_to_500000(self):
        out = normalize_style_unify_result({"content": "y" * 600000})
        assert len(out["content"]) == 500000

    def test_prior_chapters_caps_at_8(self):
        raw = {"priorChaptersRead": [{"chapterIndex": i, "title": f"t{i}"} for i in range(20)]}
        out = normalize_style_unify_result(raw)
        assert len(out["priorChaptersRead"]) == 8

    def test_prior_chapters_garbage_item_falls_back_to_zero_index(self):
        raw = {"priorChaptersRead": ["not a dict"]}
        out = normalize_style_unify_result(raw)
        assert out["priorChaptersRead"] == [{"chapterIndex": 0, "title": ""}]

    def test_prior_chapter_title_truncated_to_120(self):
        raw = {"priorChaptersRead": [{"chapterIndex": 1, "title": "t" * 200}]}
        out = normalize_style_unify_result(raw)
        assert len(out["priorChaptersRead"][0]["title"]) == 120

    def test_prior_chapter_non_finite_index_becomes_zero(self):
        raw = {"priorChaptersRead": [{"chapterIndex": float("nan"), "title": "x"}]}
        out = normalize_style_unify_result(raw)
        assert out["priorChaptersRead"][0]["chapterIndex"] == 0


# ---------------------------------------------------------------------------
# normalize_polished_result
# ---------------------------------------------------------------------------

class TestNormalizePolishedResult:
    def test_final_text_preferred(self):
        raw = {"finalText": "final", "content": "ignored", "changeSummary": "summary"}
        out = normalize_polished_result(raw)
        assert out["finalText"] == "final"
        assert out["changeSummary"] == "summary"

    def test_falls_back_to_content_field(self):
        # finalText 缺失时，回退读 content
        out = normalize_polished_result({"content": "正文", "changeSummary": "s"})
        assert out["finalText"] == "正文"

    def test_truncations(self):
        raw = {"finalText": "x" * 600000, "changeSummary": "y" * 3000}
        out = normalize_polished_result(raw)
        assert len(out["finalText"]) == 500000
        assert len(out["changeSummary"]) == 2000

    def test_non_dict_input(self):
        assert normalize_polished_result(None) == {"finalText": "", "changeSummary": ""}


# ---------------------------------------------------------------------------
# normalize_review_issues
# ---------------------------------------------------------------------------

class TestNormalizeReviewIssues:
    def test_list_input(self):
        raw = [{
            "segmentIndex": 1,
            "span": "片段",
            "issueType": "logic",
            "severity": "high",
            "suggestion": "建议",
            "context": "上下文",
        }]
        assert normalize_review_issues(raw) == [{
            "segmentIndex": 1,
            "span": "片段",
            "issueType": "logic",
            "severity": "high",
            "suggestion": "建议",
            "context": "上下文",
        }]

    def test_dict_with_issues_field(self):
        raw = {"issues": [{"span": "x"}]}
        out = normalize_review_issues(raw)
        assert len(out) == 1
        assert out[0]["span"] == "x"
        assert out[0]["issueType"] == "general"
        assert out[0]["severity"] == "medium"

    def test_caps_at_120(self):
        raw = [{"span": str(i)} for i in range(200)]
        assert len(normalize_review_issues(raw)) == 120

    def test_non_finite_segment_index_becomes_zero(self):
        raw = [{"segmentIndex": float("inf"), "span": "x"}]
        assert normalize_review_issues(raw)[0]["segmentIndex"] == 0

    def test_non_numeric_segment_index_becomes_zero(self):
        raw = [{"segmentIndex": "abc", "span": "x"}]
        assert normalize_review_issues(raw)[0]["segmentIndex"] == 0

    def test_truncations(self):
        raw = [{
            "span": "s" * 500,
            "issueType": "t" * 200,
            "severity": "u" * 50,
            "suggestion": "x" * 1000,
            "context": "y" * 1000,
        }]
        out = normalize_review_issues(raw)[0]
        assert len(out["span"]) == 200
        assert len(out["issueType"]) == 80
        assert len(out["severity"]) == 20
        assert len(out["suggestion"]) == 600
        assert len(out["context"]) == 500

    def test_garbage_items_become_defaults(self):
        # 当前行为：非 dict 会被当作空 dict 处理
        raw = ["string item", 42, None]
        out = normalize_review_issues(raw)
        assert len(out) == 3
        for o in out:
            assert o["segmentIndex"] == 0
            assert o["span"] == ""
            assert o["issueType"] == "general"
            assert o["severity"] == "medium"

    def test_neither_list_nor_dict_returns_empty(self):
        assert normalize_review_issues("nope") == []
        assert normalize_review_issues(None) == []

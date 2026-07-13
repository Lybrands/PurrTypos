from __future__ import annotations

from services.agent_eval_cases import PLANNER_EVAL_CASES, get_planner_eval_case
from services.agent_eval_harness import (
    evaluate_planner_case,
    run_planner_eval_suite,
    summarize_planner_suite,
)


def _case(case_id: str):
    case = get_planner_eval_case(case_id)
    assert case is not None
    return case


def test_case_catalog_has_stable_unique_ids():
    ids = [case.case_id for case in PLANNER_EVAL_CASES]

    assert len(ids) == len(set(ids))
    assert len(ids) >= 5


def test_character_review_case_accepts_relevant_read_only_plan():
    result = evaluate_planner_case(_case("character-consistency-review"), {
        "needsTodos": True,
        "title": "人物一致性检查",
        "todos": [
            {
                "id": "read-characters",
                "title": "读取人物设定",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["getBookCharacters"],
            },
            {
                "id": "read-chapter",
                "title": "读取当前章节",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["getChapterContent"],
            },
            {"id": "analyze", "title": "分析一致性", "type": "analyze", "executor": "model"},
        ],
    })

    assert result["verdict"] == "pass"


def test_destructive_case_rejects_delete_before_read():
    result = evaluate_planner_case(_case("destructive-cleanup-is-ordered"), {
        "needsTodos": True,
        "title": "清理重复人物",
        "todos": [
            {
                "id": "delete",
                "title": "删除重复人物",
                "type": "write",
                "executor": "tool",
                "expectedTools": ["deleteCharacter"],
            },
            {
                "id": "read",
                "title": "读取人物资料",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["getBookCharacters"],
            },
        ],
    })

    assert result["verdict"] == "fail"
    assert any(check["name"] == "destructiveOrder" and check["status"] == "fail" for check in result["checks"])


def test_negative_cases_pass_when_host_rejects_invalid_candidates():
    unknown = evaluate_planner_case(_case("unknown-tool-is-rejected"), {
        "needsTodos": True,
        "todos": [{
            "id": "bad",
            "title": "未知工具",
            "type": "read",
            "executor": "tool",
            "expectedTools": ["notARealTool"],
        }],
    })
    missing_scope = evaluate_planner_case(_case("tool-step-needs-scope"), {
        "needsTodos": True,
        "todos": [{
            "id": "read",
            "title": "读取",
            "type": "read",
            "executor": "tool",
        }],
    })

    assert unknown["verdict"] == "pass"
    assert missing_scope["verdict"] == "pass"
    assert summarize_planner_suite([unknown, missing_scope]) == {
        "total": 2,
        "passed": 2,
        "failed": 0,
    }


async def test_suite_runner_keeps_generation_separate_from_scoring():
    async def _generate(case):
        if case.expect_valid_plan:
            tools = sorted(
                case.required_tools,
                key=lambda tool: (tool == "deleteCharacter", tool),
            )
            todos = [
                {
                    "id": f"read-{index}",
                    "title": f"read {tool}",
                    "type": "read",
                    "executor": "tool",
                    "expectedTools": [tool],
                }
                for index, tool in enumerate(tools)
            ]
            todos.append({"id": "analyze", "title": "analyze", "type": "analyze", "executor": "model"})
            return {"needsTodos": True, "title": case.title, "todos": todos}
        if case.case_id == "unknown-tool-is-rejected":
            return {
                "needsTodos": True,
                "todos": [{"id": "bad", "title": "bad", "type": "read", "executor": "tool", "expectedTools": ["unknown"]}],
            }
        return {
            "needsTodos": True,
            "todos": [{"id": "missing", "title": "missing", "type": "read", "executor": "tool"}],
        }

    suite = await run_planner_eval_suite(_generate)

    assert suite["summary"] == {"total": 5, "passed": 5, "failed": 0}

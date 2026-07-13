"""Small, deterministic planner-evaluation cases for CI regression checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannerEvalCase:
    case_id: str
    title: str
    prompt: str
    available_tools: frozenset[str]
    expect_valid_plan: bool
    required_tools: frozenset[str] = frozenset()
    forbidden_tools: frozenset[str] = frozenset()
    destructive_tools_need_prior_read: bool = False


PLANNER_EVAL_CASES: tuple[PlannerEvalCase, ...] = (
    PlannerEvalCase(
        case_id="character-consistency-review",
        title="人物一致性检查必须读取人物资料",
        prompt="检查女主在当前章节与人物设定中的性格是否一致，并给出修改建议。",
        available_tools=frozenset({"getBookCharacters", "getChapterContent", "deleteCharacter"}),
        expect_valid_plan=True,
        required_tools=frozenset({"getBookCharacters", "getChapterContent"}),
        forbidden_tools=frozenset({"deleteCharacter"}),
    ),
    PlannerEvalCase(
        case_id="chapter-continuity-review",
        title="章节衔接检查必须读取章节",
        prompt="检查当前章节与上一章的剧情衔接，并指出矛盾。",
        available_tools=frozenset({"getChapterContent", "queryOutline", "deleteCharacter"}),
        expect_valid_plan=True,
        required_tools=frozenset({"getChapterContent"}),
        forbidden_tools=frozenset({"deleteCharacter"}),
    ),
    PlannerEvalCase(
        case_id="destructive-cleanup-is-ordered",
        title="删除人物前必须先读取人物资料",
        prompt="删除重复人物前先核对人物资料，确认后再删除重复项。",
        available_tools=frozenset({"getBookCharacters", "deleteCharacter"}),
        expect_valid_plan=True,
        required_tools=frozenset({"getBookCharacters", "deleteCharacter"}),
        destructive_tools_need_prior_read=True,
    ),
    PlannerEvalCase(
        case_id="unknown-tool-is-rejected",
        title="未知工具计划必须被拒绝",
        prompt="读取当前章节并分析。",
        available_tools=frozenset({"getChapterContent"}),
        expect_valid_plan=False,
    ),
    PlannerEvalCase(
        case_id="tool-step-needs-scope",
        title="工具步骤必须声明工具范围",
        prompt="读取当前章节并分析。",
        available_tools=frozenset({"getChapterContent"}),
        expect_valid_plan=False,
    ),
)


def get_planner_eval_case(case_id: str) -> PlannerEvalCase | None:
    key = str(case_id or "").strip()
    return next((case for case in PLANNER_EVAL_CASES if case.case_id == key), None)

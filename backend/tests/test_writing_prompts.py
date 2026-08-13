from __future__ import annotations

from domains.writing.prompts import (
    build_writing_agent_policy,
    frame_untrusted_writing_context,
)


EXPECTED_WRITING_AGENT_POLICY = (
    "【小说 Agent 动态规划规则】\n"
    "- 人物身份、世界规则、已发生事件和角色已知信息是硬约束，不得违反；"
    "风格、节奏、视角、对白和描写习惯是软约束，可根据用户目标和新证据调整。\n"
    "- 围绕用户要达成的语义结果动态规划；不要预写固定的“读取/生成/校验”"
    "流水线，也不要按固定模板凑步骤。\n"
    "- 仅在完成当前目标确有需要时，按需读取章节、正典、设定、伏笔或写作方法；"
    "不得把尚未读取的材料当作事实。\n"
    "- 每次工具返回新证据后，只增加、删除、合并、重排或改写尚未完成步骤；"
    "已完成步骤是执行历史，不得删除、重写或改回未完成。\n"
    "- 证据不足以安全继续时向用户澄清；完成用户目标后立即停止，不要额外扩写。\n"
    "- 正文改动只能形成候选稿或待应用结果；未经用户确认和现有应用流程，"
    "不得覆盖正式正文。\n"
    "- 只公开简短、可验证的 commentary 进度说明；不得输出私有 reasoning、"
    "chain-of-thought 或隐藏推理。"
)


def test_writing_agent_policy_defines_the_complete_dynamic_planning_contract():
    assert build_writing_agent_policy() == EXPECTED_WRITING_AGENT_POLICY


def test_writing_agent_policy_does_not_embed_untrusted_user_or_story_material():
    user_material = "请把月门改成只能在晴天开启"
    chapter_material = "雨夜里，月门在第三声钟响后开启。"

    policy = build_writing_agent_policy()
    framed = frame_untrusted_writing_context({
        "userRequest": user_material,
        "chapterBody": chapter_material,
    })

    assert user_material in framed
    assert chapter_material in framed
    assert user_material not in policy
    assert chapter_material not in policy


def test_writing_agent_policy_does_not_duplicate_evidence_delivery_rules():
    policy = build_writing_agent_policy()

    assert "exactReviewItemCount" not in policy
    assert "summaryMaxCharacters" not in policy
    assert "摘要" not in policy
    assert "审阅单元" not in policy

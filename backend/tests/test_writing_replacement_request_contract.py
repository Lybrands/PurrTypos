from __future__ import annotations

import ast
from pathlib import Path

from agents.writing.profile import WritingReplacementProfile
from agents.writing.public_facts import WritingPublicFactsProvider
from agents.writing.request_contract import WritingRequestContext
from agents.writing.response_contract import AtomicContinuityJudgePolicy
from application.request_mapping import (
    to_writing_agent_request,
    writing_run_options,
)
from schemas.ai import ChatStreamRequest


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _body(content: str) -> ChatStreamRequest:
    return ChatStreamRequest(
        messages=[{"role": "user", "content": content}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "model",
            "model_profile": "deepseek:deepseek-v4-flash",
            "profile_binding": "compatible",
        },
        enableAgentTools=True,
        bookId="book-1",
        chapterId="chapter-1",
        associatedOutlineIds=["outline-1"],
        chatAgentMode="agent",
    )


def _request(content: str):
    return to_writing_agent_request(_body(content), {"model": "model"})


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_request_mapping_uses_replacement_owned_writing_contracts():
    imports = _imports(BACKEND_DIR / "application" / "request_mapping.py")

    assert {
        "agents.writing.context_claims",
        "agents.writing.public_facts",
        "agents.writing.request_contract",
        "agents.writing.response_contract",
    }.issubset(imports)
    assert not {
        "domains.writing.context",
        "domains.writing.contracts",
        "domains.writing.public_facts",
        "domains.writing.response",
    }.intersection(imports)


def test_replacement_request_context_preserves_host_locators():
    request = _request("查看当前章节。")
    context = WritingRequestContext.from_core_context(request.domain_context)

    assert context.book_id == "book-1"
    assert context.chapter_id == "chapter-1"
    assert context.associated_outline_ids == ("outline-1",)
    assert "writing_chapters" not in request.domain_context.payload
    assert "writing_technique_snapshot" not in request.domain_context.payload


def test_replacement_options_own_validated_response_facts():
    request = _request("读取当前章节，给出一个 150 字以内的摘要。")
    options = writing_run_options(request, {})

    assert options.response_validators
    assert isinstance(
        options.committed_result_facts_provider,
        WritingPublicFactsProvider,
    )


def test_replacement_profile_restores_atomic_continuity_judge():
    request = _request(
        "对照当前章节与关联大纲，找出两处不一致，并给出最小修改建议。"
    )
    profile = WritingReplacementProfile(object())

    policies = profile.response_judge_policies(request)

    assert len(policies) == 1
    assert isinstance(policies[0], AtomicContinuityJudgePolicy)

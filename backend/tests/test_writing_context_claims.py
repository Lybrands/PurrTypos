"""写作上下文预算声明的让路护栏。

purra 的分配器语义：先保障各 claim 的 minimum_tokens，再按优先级分配
desired。因此「可让路」= minimum 为 0（或极小）；minimum 之和超过可用池
会直接抛 minimum_context_demand_exceeds_pool。这里锁住写作侧声明的
让路性质，防止未来把可协商的检索需求改成硬性下限。
"""
from dataclasses import replace

from purra.contracts import AgentMessage, AgentRunRequest, DomainContext, ModelRequest
from purra.model_protocol import generic_capability_snapshot
from agents.writing.context_claims import (
    CONTINUATION_CANON_CONTEXT,
    WRITING_RETRIEVAL_CONTEXT,
    writing_context_claims,
)
from agents.writing.request_contract import WRITING_DOMAIN_NAMESPACE


def _request(payload: dict, *, window: int = 200_000) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="写下一章"),),
        model=ModelRequest(
            provider="openai",
            model="test-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="openai:test-model",
            ),
        ),
        domain_context=DomainContext(
            namespace=WRITING_DOMAIN_NAMESPACE,
            payload=payload,
        ),
        context_window=window,
    )


def test_retrieval_claim_is_fully_yieldable_even_on_tiny_windows():
    request = _request({
        "book_id": "book-1",
        "associated_chapter_ids": ("c1", "c2"),
        "associated_outline_ids": ("o1",),
    }, window=8_000)
    claims = {claim.name: claim for claim in writing_context_claims(request)}

    assert WRITING_RETRIEVAL_CONTEXT in claims
    retrieval = claims[WRITING_RETRIEVAL_CONTEXT]
    # desired 保留 6k/24k 的产品下限（有用性），但 minimum 恒为 0：
    # 分配器在任何窗口下都可以完全让路，不会触发最小需求超池。
    assert retrieval.minimum_tokens == 0
    assert retrieval.desired_tokens > 0


def test_retrieval_claim_scales_with_window():
    small = _request({"book_id": "book-1"}, window=32_000)
    large = _request({"book_id": "book-1"}, window=200_000)
    small_claim = {c.name: c for c in writing_context_claims(small)}[WRITING_RETRIEVAL_CONTEXT]
    large_claim = {c.name: c for c in writing_context_claims(large)}[WRITING_RETRIEVAL_CONTEXT]
    assert small_claim.desired_tokens < large_claim.desired_tokens


def test_technique_minimum_comes_only_from_manual_selections():
    # 仅自动候选：完全可让路。
    auto_only = _request({
        "book_id": "book-1",
        "writing_technique_snapshot": {"manual": [], "candidates": [{"ref": {}}]},
    })
    auto_claims = {c.name: c for c in writing_context_claims(auto_only)}
    assert auto_claims["writing_techniques"].minimum_tokens == 0

    # 手动选择：minimum 恰为必需条目之和，不多占。
    manual = _request({
        "book_id": "book-1",
        "writing_technique_snapshot": {
            "manual": [{"entryBytes": 4_000, "composition": ""}],
            "candidates": [],
        },
    })
    manual_claims = {c.name: c for c in writing_context_claims(manual)}
    technique = manual_claims["writing_techniques"]
    # 空 composition 的 JSON 估算按 1 token 计。
    assert technique.minimum_tokens == 4_000 // 2 + 1 + 1024


def test_continuation_canon_minimum_stays_negligible():
    request = _request({
        "book_id": "book-1",
        "creation_mode": "continuation",
        "inherited_canon_records": [{"title": "旧作", "facts": ["x"] * 500}],
    })
    claims = {c.name: c for c in writing_context_claims(request)}
    canon = claims[CONTINUATION_CANON_CONTEXT]
    assert canon.minimum_tokens <= 1_000
    assert canon.minimum_tokens <= canon.desired_tokens


def test_all_claims_stay_well_formed():
    request = _request({
        "book_id": "book-1",
        "associated_chapter_ids": ("c1",),
        "writing_technique_snapshot": {
            "manual": [{"entryBytes": 2_000, "composition": ""}],
            "candidates": [{"ref": {}}],
        },
        "creation_mode": "continuation",
        "inherited_canon_records": [{"title": "旧作"}],
    })
    claims = writing_context_claims(request)
    names = [claim.name for claim in claims]
    assert len(names) == len(set(names))
    for claim in claims:
        assert 0 <= claim.minimum_tokens <= claim.desired_tokens

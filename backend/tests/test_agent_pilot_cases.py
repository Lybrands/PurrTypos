from __future__ import annotations


def test_pilot_case_catalogue_has_stable_ids_and_required_test_instructions():
    from services.agent_pilot_cases import get_pilot_cases

    cases = get_pilot_cases()

    assert [case["id"] for case in cases] == [
        "P1-direct-answer",
        "P2-grounded-context",
        "P3-read-only-tool",
        "P4-destructive-rejection",
        "P5-multistep-proposal",
    ]
    for case in cases:
        assert case["prompt"].strip()
        assert case["purpose"].strip()
        assert case["observe"]


def test_pilot_case_catalogue_returns_copies():
    from services.agent_pilot_cases import get_pilot_cases

    first, second = get_pilot_cases(), get_pilot_cases()
    first[0]["title"] = "mutated"

    assert second[0]["title"] != "mutated"

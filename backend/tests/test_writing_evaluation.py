"""Writing-owned deterministic regression and security contracts."""

from __future__ import annotations


def test_writing_domain_supplies_cases_to_content_free_core_regression_harness():
    from purra.evaluation import AgentRuntimeRegressionCase
    from domains.writing.evaluation.cases import (
        WRITING_RUNTIME_REGRESSION_CASES,
    )

    assert all(
        isinstance(case, AgentRuntimeRegressionCase)
        for case in WRITING_RUNTIME_REGRESSION_CASES
    )
    assert WRITING_RUNTIME_REGRESSION_CASES[0].expected_tool_sequence == (
        "listWritingChapters",
        "getChapterContent",
    )


def test_writing_domain_owns_book_scope_and_retrieved_prose_redteam_cases():
    from domains.writing.evaluation.security import (
        get_writing_security_redteam_cases,
    )

    def _validate_book_scope(context: dict, arguments: dict) -> str | None:
        return (
            "outside scope"
            if context.get("bookId") != arguments.get("bookId")
            else None
        )

    cases = get_writing_security_redteam_cases(_validate_book_scope)

    assert [case_id for case_id, _, _ in cases] == [
        "RT4-book-scope-override",
        "RT6-indirect-prompt-injection",
    ]
    assert all(check() for _, _, check in cases)


def test_application_security_suite_preserves_case_order_and_shape():
    from application.operations.deterministic_checks import (
        run_agent_security_redteam_suite,
    )

    suite = run_agent_security_redteam_suite()

    assert [result["caseId"] for result in suite["results"]] == [
        "RT1-malformed-tool-arguments",
        "RT2-duplicate-tool-call-ids",
        "RT3-tool-batch-resource-limit",
        "RT4-book-scope-override",
        "RT4-unsupported-nested-schema",
        "RT5-sensitive-error-redaction",
        "RT6-indirect-prompt-injection",
    ]
    assert suite["summary"] == {"total": 7, "passed": 7, "failed": 0}

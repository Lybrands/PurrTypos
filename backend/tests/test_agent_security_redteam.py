import pytest


def test_security_redteam_suite_covers_host_boundaries():
    from services.agent_security_redteam import run_agent_security_redteam_suite

    suite = run_agent_security_redteam_suite()

    assert suite["summary"] == {"total": 6, "passed": 6, "failed": 0}
    assert {result["caseId"] for result in suite["results"]} == {
        "RT1-malformed-tool-arguments",
        "RT2-duplicate-tool-call-ids",
        "RT3-tool-batch-resource-limit",
        "RT4-book-scope-override",
        "RT5-sensitive-error-redaction",
        "RT6-indirect-prompt-injection",
    }


@pytest.mark.asyncio
async def test_security_redteam_endpoint_returns_report():
    from routers.ai import get_agent_security_redteam

    response = await get_agent_security_redteam()

    assert response["success"] is True
    assert response["data"]["summary"]["failed"] == 0

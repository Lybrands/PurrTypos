import pytest

from application.assistant_text_facts import AssistantTextFactsProvider
from database.connection import DatabaseConnection
from infrastructure.persistence import approval_store, run_store
from purra.contracts import AgentRunResult, ApprovalStatus, RunStatus
from purra.json_values import thaw_json_value


@pytest.mark.asyncio
async def test_presentation_uses_only_current_run_rejected_decisions(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        for run_id in ("current", "foreign"):
            await run_store.create_run(
                db, run_id=run_id, session_id=None, prompt="synthetic", mode=None,
            )
        for approval_id, run_id, title, status in (
            ("foreign-reject", "foreign", "其他运行的私有标题", ApprovalStatus.REJECTED),
            ("approved", "current", "已批准操作", ApprovalStatus.APPROVED),
            ("current-reject", "current", "删除合成人物", ApprovalStatus.REJECTED),
        ):
            await approval_store.create_approval(
                db, approval_id=approval_id, run_id=run_id,
                tool_call_id=approval_id, tool_name="deleteCharacter", title=title,
                risk_level="destructive", summary="private synthetic arguments",
                expires_at_ms=9999999999999,
            )
            await approval_store.transition_pending(
                db, approval_id=approval_id, run_id=run_id, status=status,
            )
        provider = AssistantTextFactsProvider(db)
        result = AgentRunResult(
            run_id="current", status=RunStatus.DONE,
            final_response="已删除，删除失败，请联系管理员。",
        )
        bundle = await provider.facts_for("current", result)
        assert [(f.key, thaw_json_value(f.value)) for f in bundle.facts] == [
            ("approvalOutcomes", [{"action": "删除合成人物",
                                   "decision": "rejected_by_user",
                                   "execution": "not_executed"}]),
        ]
        with pytest.raises(ValueError):
            await provider.facts_for("foreign", result)
        with pytest.raises(ValueError):
            await provider.facts_for("current", AgentRunResult(
                run_id="current", status=RunStatus.FAILED, final_response="invalid",
            ))
        await db.execute("DELETE FROM ai_agent_approvals WHERE id = 'current-reject'")
        plain = await provider.facts_for("current", result)
        assert [(f.key, f.value) for f in plain.facts] == [
            ("assistantAnswer", result.final_response),
        ]
        framed = await provider.facts_for("current", AgentRunResult(
            run_id="current", status=RunStatus.DONE,
            final_response="【公开说明】完成检查。【说明结束】最终结果。",
        ))
        assert framed.facts[0].value == "完成检查。最终结果。"
    finally:
        await db.close()

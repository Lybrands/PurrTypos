"""Public presentation input for contracts whose result is assistant prose."""
from purra.contracts import AgentRunResult, RunStatus
from purra.output import PublicFact, PublicFactBundle
from constants import AGENT_PUBLIC_COMMENTARY_OPEN, AGENT_PUBLIC_COMMENTARY_CLOSE


class AssistantTextFactsProvider:
    def __init__(self, db=None):
        self._db = db

    async def facts_for(self, run_id: str, result: AgentRunResult) -> PublicFactBundle:
        if result.run_id != run_id or result.status is not RunStatus.DONE:
            raise ValueError("Assistant presentation requires its completed result")
        text = str(result.final_response or "").strip()
        text = text.replace(AGENT_PUBLIC_COMMENTARY_OPEN, "").replace(AGENT_PUBLIC_COMMENTARY_CLOSE, "").strip()
        if not text:
            raise ValueError("Assistant presentation requires nonempty prose")
        if self._db is not None:
            rejected = await self._db.fetch_all(
                "SELECT title FROM ai_agent_approvals "
                "WHERE run_id = ? AND status = 'rejected' ORDER BY id",
                [run_id],
            )
            if rejected:
                # A model's account of the rejected operation is not evidence.
                # Only the persisted decision supplies its public outcome.
                return PublicFactBundle(facts=(PublicFact("approvalOutcomes", [
                    {"action": row["title"], "decision": "rejected_by_user",
                     "execution": "not_executed"}
                    for row in rejected
                ]),))
        return PublicFactBundle(facts=(PublicFact("assistantAnswer", text),))

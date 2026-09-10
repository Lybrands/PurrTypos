"""Fence affected live Runs through the shared cancellation service."""

import json

from domains.writing.techniques import TechniqueError


async def cancel_invalid_technique_runs(db, *, book_id=None):
    from application.agent_composition import get_agent_composition
    from application.agent_cancellation_service import AgentCancellationService
    from application.writing_technique_access import WritingTechniqueAccess
    from purra.errors import ContractViolationError

    try:
        composition = get_agent_composition()
    except RuntimeError:
        return
    access = WritingTechniqueAccess(db)
    rows = await db.fetch_all("SELECT r.id,r.binding_attributes_json,s.automatic_refs_json FROM ai_agent_runs r "
        "LEFT JOIN writing_technique_run_state s ON s.run_id=r.id WHERE r.status='running'")
    for row in rows:
        attrs = json.loads(row['binding_attributes_json'] or '{}')
        snapshot = attrs.get('writingTechniqueSnapshot')
        if not snapshot or (book_id is not None and snapshot.get('bookId') != book_id):
            continue
        selected = access.resolve(snapshot, json.loads(row['automatic_refs_json'] or '[]'))['selected']
        try:
            await access.validate(snapshot, selected=selected)
        except TechniqueError:
            try:
                await AgentCancellationService(db, composition).cancel(row['id'])
            except ContractViolationError:
                current = await db.fetch_one("SELECT status FROM ai_agent_runs WHERE id=?", [row['id']])
                if current and current['status'] == 'running':
                    raise

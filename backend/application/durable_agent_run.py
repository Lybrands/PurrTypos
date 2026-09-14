"""Execute model/tool operations in the Run that owns a durable task."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import time

from purra.errors import ContractViolationError
from purra.long_tasks import LongTaskRunRelation
from infrastructure.persistence.sqlite_long_task_repository import SqliteLongTaskRepository

_operation_run: ContextVar[str | None] = ContextVar("durable_operation_run", default=None)


@contextmanager
def operation_run_scope(run_id: str):
    token = _operation_run.set(run_id)
    try:
        yield
    finally:
        _operation_run.reset(token)


async def run_durable_operation(*, db, runs, request, options, api_key, signal,
                                bind_run, task_id: str, unit_id: str,
                                owning_run_id: str | None = None):
    tasks = SqliteLongTaskRepository(db)
    task = await tasks.load(task_id)
    unit = next((row for row in await tasks.list_units(task_id) if row.id == unit_id), None) if task else None
    if (unit is None or unit.status.value not in {"claimed", "running"}
            or unit.lease_expires_at_ms is None or unit.lease_expires_at_ms <= int(time.time() * 1000)):
        raise ContractViolationError("Model operation requires a claimed Unit", code="long_task_unit_lease_lost")
    if unit.run_id is not None or options.agent_execution_checkpoint is not None or options.durable_continuation is not None:
        raise ContractViolationError("Operation cannot replace an independent Run", code="operation_scope_invalid")
    owner = owning_run_id or _operation_run.get() or task.created_by_run_id
    if not any(row.run_id == owner and (owner == task.created_by_run_id or row.relation is LongTaskRunRelation.CONTINUATION)
               for row in await tasks.list_run_bindings(task_id)):
        raise ContractViolationError("Operation belongs to another Run", code="recipe_root_binding_conflict")
    owner_row = await db.fetch_one("SELECT agent_id, parent_run_id FROM ai_agent_runs WHERE id=?", [owner])
    if owner_row and owner_row.get("agent_id"):
        from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository
        try:
            agent = await SqliteRunTreeRepository(db).get_agent(owner_row["agent_id"])
        except ContractViolationError as error:
            # Standalone roots have canonical output identity without an Agent tree.
            if (error.code != "agent_not_found" or owner_row["agent_id"] != owner
                    or owner_row.get("parent_run_id") is not None):
                raise
        else:
            options = replace(options, agent_capability_grant=agent.capability_grant)
    if bind_run is not None:
        await bind_run(owner)
    operation_id = f"{task_id}:{unit_id}:{unit.attempt}"
    request = replace(request, metadata={**request.metadata, "operationScopeId": operation_id,
                                        "operationBinding": {"taskId": task_id, "unitId": unit_id, "unitAttempt": unit.attempt},
                                        "responseAudience": "internal", "progressAudience": "internal"})
    result = await runs.run_operation(request=request, options=options, run_id=owner,
                                    operation_id=operation_id, api_key=api_key, signal=signal)

    if result.run_id != owner:
        raise ContractViolationError("Operation result changed its owning Run", code="run_identity_conflict")
    return result, operation_id
